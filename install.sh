#!/usr/bin/env bash
# MonoAgens installer: one path, two runtimes (native and compose).
#
#   bash install.sh                  # install on this machine, interactive setup
#   bash install.sh --dry-run        # print every action, write nothing
#   bash install.sh --dir "$PWD"     # install into an existing checkout
#   bash install.sh --stack compose  # run the engine and Postgres in containers
#   bash install.sh --stack native   # run the engine on this host, Postgres in a container
#
# Both stacks put Postgres in Podman/Docker by default. The installer asks before it installs
# a missing runtime with sudo; without consent it prints the exact command and stops that
# stage. Everything is appended to ~/.monoagens/logs/install.log. No secret value is ever
# logged or printed.
#
# Repository: https://github.com/AMQStudio/MonoAgens
# Decision:   docs/v0.1.0/adr/0030-install-and-init.md
set -euo pipefail

REPO_URL="https://github.com/AMQStudio/MonoAgens"
REVISION="main"

# --- uv pin: bump UV_VERSION and both digests together --------------------------------
# The digest list lives at, for the version you pick:
#   https://github.com/astral-sh/uv/releases/download/<version>/uv-<arch>-unknown-linux-gnu.tar.gz.sha256
UV_VERSION="0.13.7"
UV_SHA256_X86_64="c5221ff41b7892bfc223543883600125f6a47fe845093e0f5bdff4f359196d83"
UV_SHA256_AARCH64="71279f8d3c67241e807b9d8db872d1bf541579fb9f08b7d1eb1682196d1e57dc"
# --------------------------------------------------------------------------------------

monoagens_home="${HOME}/.monoagens"
default_dir="${monoagens_home}/app"
log_file="${monoagens_home}/logs/install.log"
uv_bin="${monoagens_home}/bin/uv"

dir="$default_dir"
dir_set=0
stack="native"
stack_set=0
install_runtime=0
dry_run=0
non_interactive=0
skip_selftest=0
no_color=0
color=1
provider=""
model=""
database_url=""
label=""
voice_layer=""
uv=""
python_bin=""
source_root=""
arch=""
COMPOSE=()
TMP_DIRS=()

# --- output ---------------------------------------------------------------------------


say() {
    printf '%s\n' "$*"
}

detail() {
    printf '   %s\n' "$*"
}

warn() {
    if (( color )); then
        printf '\033[33m%s\033[0m\n' "$*" >&2
    else
        printf '%s\n' "$*" >&2
    fi
}

die() {
    warn "monoagens install: $*"
    exit 1
}

stage() {
    if (( color )); then
        printf '\n\033[36m== %s\033[0m\n' "$*"
    else
        printf '\n== %s\n' "$*"
    fi
}

usage() {
    local code="$1" message="${2:-}"
    if [[ -n "$message" ]]; then
        printf 'monoagens install: %s\n\n' "$message" >&2
    fi
    cat <<'EOF'
MonoAgens installer - one path, two runtimes.

Usage: bash install.sh [options]

  --dir PATH             install directory (default: ~/.monoagens/app)
  --stack STACK          native (engine on host, Postgres in a container) or compose
                         (engine and Postgres in containers); asks when omitted
  --install-runtime      allow installing Podman/Docker and compose with sudo when missing
  --dry-run              print every action and write nothing
  --non-interactive      never prompt; fail when a required value is missing
  --provider NAME        provider for `monoagens init`
  --model ID             model id for `monoagens init` (optional)
  --database-url URL     external Postgres URL; skips the container database in native mode
  --label TEXT           label for the first administrator (roles.yaml)
  --voice-layer on|off   keep emotion, humanize, character (default on) or turn them off
  --skip-selftest        skip the closing `monoagens selftest`
  --no-color             plain output (also honoured via NO_COLOR)
  -h, --help             this text

Both stacks use Podman/Docker for Postgres unless --database-url names an external server.
The script never runs sudo without explicit consent: a missing runtime prints the exact
command and stops that stage, or is installed after a yes or --install-runtime. Log:
~/.monoagens/logs/install.log
EOF
    exit "$code"
}

setup_colors() {
    color=1
    [[ -t 1 ]] || color=0
    [[ -n "${NO_COLOR:-}" ]] && color=0
    (( no_color )) && color=0
    return 0
}

log() {
    (( dry_run )) && return 0
    printf '%s\n' "$*" >>"$log_file"
}

log_init() {
    if (( dry_run )); then
        say "dry-run: no log file; a real run appends to $log_file"
        return 0
    fi
    mkdir -p -- "$(dirname -- "$log_file")"
    touch -- "$log_file"
    chmod 0600 -- "$log_file" 2>/dev/null || true
    log "== monoagens install $(date -Is) =="
    log "dir=$dir stack=$stack provider=${provider:-<ask>} model=${model:-<unset>} voice=${voice_layer:-<ask>}"
}

# One line describing the init invocation: names only, never the database URL.
log_init_meta() {
    local url_state="<unset>" label_state="<unset>"
    [[ -n "$database_url" ]] && url_state="<provided>"
    [[ -n "$label" ]] && label_state="<set>"
    log "init: provider=${provider:-<ask>} model=${model:-<unset>} voice=${voice_layer:-<ask>} database-url=$url_state label=$label_state"
    return 0
}

# --- signals and temp files -----------------------------------------------------------


cleanup() {
    local path
    for path in "${TMP_DIRS[@]:-}"; do
        if [[ -n "$path" ]]; then
            rm -rf -- "$path"
        fi
    done
    return 0
}

interrupted() {
    local signal="$1" code="$2"
    trap - INT TERM HUP
    log "== interrupted by SIG$signal =="
    printf '\nmonoagens install: interrupted; nothing else was started\n' >&2
    kill -s "$signal" $$ 2>/dev/null || exit "$code"
    exit "$code"
}

install_traps() {
    trap 'interrupted INT 130' INT
    trap 'interrupted TERM 143' TERM
    trap 'interrupted HUP 129' HUP
    trap cleanup EXIT
}

# --- flags ----------------------------------------------------------------------------


parse_args() {
    while (($#)); do
        case "$1" in
            --dir)
                [[ $# -ge 2 ]] || usage 2 "missing value for --dir"
                dir="$2"
                dir_set=1
                shift 2
                ;;
            --stack)
                [[ $# -ge 2 ]] || usage 2 "missing value for --stack"
                stack="$2"
                stack_set=1
                shift 2
                ;;
            --provider) [[ $# -ge 2 ]] || usage 2 "missing value for --provider"; provider="$2"; shift 2 ;;
            --model) [[ $# -ge 2 ]] || usage 2 "missing value for --model"; model="$2"; shift 2 ;;
            --database-url)
                [[ $# -ge 2 ]] || usage 2 "missing value for --database-url"
                database_url="$2"
                shift 2
                ;;
            --label) [[ $# -ge 2 ]] || usage 2 "missing value for --label"; label="$2"; shift 2 ;;
            --voice-layer)
                [[ $# -ge 2 ]] || usage 2 "missing value for --voice-layer"
                voice_layer="$2"
                shift 2
                ;;
            --dir=*) dir="${1#*=}"; dir_set=1; shift ;;
            --stack=*) stack="${1#*=}"; stack_set=1; shift ;;
            --install-runtime) install_runtime=1; shift ;;
            --provider=*) provider="${1#*=}"; shift ;;
            --model=*) model="${1#*=}"; shift ;;
            --database-url=*) database_url="${1#*=}"; shift ;;
            --label=*) label="${1#*=}"; shift ;;
            --voice-layer=*) voice_layer="${1#*=}"; shift ;;
            --dry-run) dry_run=1; shift ;;
            --non-interactive) non_interactive=1; shift ;;
            --skip-selftest) skip_selftest=1; shift ;;
            --no-color) no_color=1; shift ;;
            -h | --help) usage 0 ;;
            *) usage 2 "unknown option: $1" ;;
        esac
    done
    case "$voice_layer" in "" | on | off) ;; *) usage 2 "--voice-layer must be on or off" ;; esac
    case "$stack" in native | compose) ;; *) usage 2 "--stack must be native or compose" ;; esac
    [[ -n "$dir" ]] || usage 2 "--dir must not be empty"
    dir="${dir/#\~/$HOME}"
    [[ "$dir" = /* ]] || dir="${PWD}/${dir}"
    # The checkout this script runs from, when it is a file (`curl | bash` has no source).
    if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
        source_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    else
        source_root=""
    fi
}

ask_runtime() {
    local answer=""
    (( stack_set )) && return 0
    (( non_interactive || dry_run )) && return 0
    [[ -t 0 ]] || return 0
    say ""
    say "Where should the engine run? Postgres always uses Podman/Docker unless --database-url names an external server."
    say "  1) native  - engine on this host, Postgres in a container"
    say "  2) compose - engine and Postgres in containers"
    read -r -p "  runtime [1]: " answer || answer="1"
    case "$answer" in
        "" | 1 | native) stack="native" ;;
        2 | compose) stack="compose" ;;
        *) usage 2 "runtime must be 1/native or 2/compose" ;;
    esac
    stack_set=1
}

ask_directory() {
    local answer="" choice=""
    (( dir_set || non_interactive || dry_run )) && return 0
    [[ -t 0 ]] || return 0
    say ""
    say "Where should MonoAgens live?"
    say "  config.yaml, roles.yaml, soul.yaml, .env, and the runtime state are written there."
    say "  1) $default_dir (recommended)"
    say "  2) custom directory"
    read -r -p "  location [1]: " choice || choice="1"
    case "$choice" in
        "" | 1 | recommended) dir="$default_dir" ;;
        2 | custom)
            read -r -p "  custom directory: " answer || answer=""
            if [[ -n "$answer" ]]; then
                dir="$answer"
            fi
            ;;
        *) dir="$choice" ;;
    esac
    dir="${dir/#\~/$HOME}"
    [[ "$dir" = /* ]] || dir="${PWD}/${dir}"
    [[ -n "$dir" ]] || usage 2 "install directory must not be empty"
    dir_set=1
}

# --- platform and preflight -----------------------------------------------------------


detect_arch() {
    case "$(uname -s)" in
        Linux) ;;
        *) die "this installer supports Linux only (found $(uname -s))" ;;
    esac
    case "$(uname -m)" in
        x86_64 | amd64) arch="x86_64" ;;
        aarch64 | arm64) arch="aarch64" ;;
        *) die "this installer supports x86_64 and aarch64 only (found $(uname -m))" ;;
    esac
}

find_python() {
    local candidate
    for candidate in python3.14 python3.13 python3.12 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 14) else 1)' \
                >/dev/null 2>&1; then
                printf '%s' "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

find_uv() {
    if [[ -x "$uv_bin" ]]; then
        printf '%s' "$uv_bin"
        return 0
    fi
    command -v uv 2>/dev/null
}

uv_archive_name() {
    printf 'uv-%s-unknown-linux-gnu.tar.gz' "$arch"
}

revision_label() {
    if [[ -n "$source_root" && -d "$source_root/.git" ]] && command -v git >/dev/null 2>&1; then
        git -C "$source_root" rev-parse --short HEAD 2>/dev/null && return 0
    fi
    printf '%s' "$REVISION"
}

uv_digest() {
    case "$arch" in
        x86_64) printf '%s' "$UV_SHA256_X86_64" ;;
        aarch64) printf '%s' "$UV_SHA256_AARCH64" ;;
        *) return 1 ;;
    esac
}

# `verify_sha256 FILE DIGEST`; the test seam for the pin, no network involved.
verify_sha256() {
    local file="$1" expected="$2" actual
    actual="$(sha256sum -- "$file" | awk '{print $1}')"
    [[ "$actual" == "$expected" ]]
}

install_uv() {
    local archive url tmp expected
    if ! command -v curl >/dev/null 2>&1; then
        warn "curl is not on PATH, so the pinned uv cannot be fetched. Install it (dnf install curl),"
        warn "or install uv yourself (https://docs.astral.sh/uv/) and re-run."
        die "curl is required to fetch uv $UV_VERSION"
    fi
    archive="$(uv_archive_name)"
    url="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${archive}"
    detail "fetch $url"
    log "fetch $url"
    tmp="$(mktemp -d)"
    TMP_DIRS+=("$tmp")
    if ! curl -fsSL --retry 3 --proto '=https' --proto-redir '=https' -o "$tmp/$archive" "$url"; then
        die "cannot download uv $UV_VERSION; install it yourself (https://docs.astral.sh/uv/) and re-run"
    fi
    expected="$(uv_digest)"
    if ! verify_sha256 "$tmp/$archive" "$expected"; then
        die "uv archive checksum mismatch (expected $expected); refusing an unverified binary"
    fi
    detail "checksum verified ($expected)"
    mkdir -p -- "$(dirname -- "$uv_bin")"
    if ! tar -xzf "$tmp/$archive" -C "$tmp"; then
        die "cannot unpack the uv archive; remove it and re-run"
    fi
    install -m 0755 -- "$tmp/${archive%.tar.gz}/uv" "$uv_bin" || die "cannot install uv to $uv_bin"
    uv="$uv_bin"
    detail "installed $("$uv" --version 2>/dev/null || echo "uv $UV_VERSION") at $uv_bin"
}

stage_preflight() {
    stage "preflight"
    detail "platform: linux/$arch"
    if ! python_bin="$(find_python)"; then
        warn "no Python 3.14+ interpreter found; the container image pins 3.14."
        warn "Install it, then re-run:"
        warn "  dnf install python3.14"
        die "Python 3.14+ is required"
    fi
    detail "python: $python_bin ($("$python_bin" -V 2>&1)); the container image pins 3.14"
    if uv_path="$(find_uv)" && [[ -n "$uv_path" ]]; then
        uv="$uv_path"
        detail "uv: $uv_path ($("$uv" --version 2>/dev/null || echo 'version unknown'))"
        detail "pinned release $UV_VERSION is used only when uv is absent"
    else
        uv="$uv_bin"
        detail "uv: absent; the pinned release $UV_VERSION will be fetched and verified"
    fi
}

stage_uv() {
    [[ -x "$uv" ]] && return 0
    stage "install uv $UV_VERSION"
    if (( dry_run )); then
        detail "would download https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$(uv_archive_name)"
        detail "would verify SHA-256 $(uv_digest) and install it to $uv_bin"
        return 0
    fi
    install_uv
}

# --- install directory ----------------------------------------------------------------


prepare_dir() {
    stage "install directory: $dir"
    if [[ -f "$dir/pyproject.toml" ]]; then
        detail "using the existing checkout"
        return 0
    fi
    if [[ -n "$source_root" && -f "$source_root/pyproject.toml" ]]; then
        if [[ "$dir" == "$source_root" || "$dir" == "$source_root"/* ]]; then
            die "--dir must sit beside this checkout, not inside it; pass --dir at the checkout itself or somewhere outside"
        fi
        if [[ -d "$dir" && -n "$(ls -A -- "$dir" 2>/dev/null)" ]]; then
            die "$dir is not empty and has no pyproject.toml; pass --dir at an empty directory or at this checkout"
        fi
        detail "copy this checkout from $source_root (no .git, .venv, .env, or runtime data)"
        (( dry_run )) && return 0
        mkdir -p -- "$dir"
        tar -C "$source_root" \
            --exclude=.git --exclude=.venv --exclude=__pycache__ \
            --exclude=.env --exclude=.monoagens --exclude='*.egg-info' \
            -cf - . | tar -C "$dir" -xf - || die "cannot copy the checkout into $dir"
        return 0
    fi
    if [[ -d "$dir" && -n "$(ls -A -- "$dir" 2>/dev/null)" ]]; then
        die "$dir is not empty and has no pyproject.toml; pass --dir at an empty directory"
    fi
    if command -v curl >/dev/null 2>&1 && command -v tar >/dev/null 2>&1; then
        local archive_url="${REPO_URL}/archive/refs/heads/${REVISION}.tar.gz"
        detail "download $archive_url into $dir"
        (( dry_run )) && return 0
        mkdir -p -- "$dir"
        if curl -fsSL --retry 3 --proto '=https' --proto-redir '=https' "$archive_url" \
            | tar -xz --strip-components=1 -C "$dir"; then
            return 0
        fi
        rm -rf -- "$dir"
        warn "archive download failed; trying git"
    fi
    if command -v git >/dev/null 2>&1; then
        detail "clone $REPO_URL into $dir (no local changes exist to protect)"
        (( dry_run )) && return 0
        mkdir -p -- "$(dirname -- "$dir")"
        git clone --depth 1 -- "$REPO_URL" "$dir" >>"$log_file" 2>&1 \
            || die "git clone failed; see $log_file"
        return 0
    fi
    warn "nothing to install at $dir and no curl/git to fetch a checkout. Install git, then re-run:"
    warn "  dnf install git"
    die "no checkout at $dir"
}

# --- running children -----------------------------------------------------------------


run_logged() {
    local label="$1" display="$2" code=0
    shift 2
    stage "$label"
    log "\$ $display"
    if (( dry_run )); then
        detail "would run: $display"
        return 0
    fi
    "$@" >>"$log_file" 2>&1 || code=$?
    if (( code != 0 )); then
        detail "failed (exit $code); last log lines:"
        tail -n 15 -- "$log_file" | sed 's/^/     /' >&2 || true
        die "stage failed: $label; full log: $log_file"
    fi
    detail "done"
}

run_soft() {
    local label="$1" display="$2" code=0
    shift 2
    stage "$label"
    log "\$ $display"
    if (( dry_run )); then
        detail "would run: $display"
        return 0
    fi
    "$@" >>"$log_file" 2>&1 || code=$?
    if (( code != 0 )); then
        warn "$label reported a problem (exit $code); the install stands."
        warn "fix .env, then run: $display"
        return 1
    fi
    detail "ok"
}

uv_sync() {
    ( cd -- "$dir" && exec "$uv" sync --locked )
}

uv_run_in_dir() {
    ( cd -- "$dir" && exec "$uv" run monoagens "$@" )
}

# --- init -----------------------------------------------------------------------------


build_init_args() {
    INIT_ARGS=(init --dir "$dir" --no-location)
    if [[ -n "$voice_layer" ]]; then INIT_ARGS+=(--voice-layer "$voice_layer"); fi
    if [[ -n "$provider" ]]; then INIT_ARGS+=(--provider "$provider"); fi
    if [[ -n "$model" ]]; then INIT_ARGS+=(--model "$model"); fi
    if [[ -n "$database_url" ]]; then INIT_ARGS+=(--database-url "$database_url"); fi
    if [[ -n "$label" ]]; then INIT_ARGS+=(--label "$label"); fi
    return 0
}

run_init() {
    local attached=0
    build_init_args
    stage "configure the host (monoagens init)"
    log_init_meta
    if [[ ! -t 0 ]] || (( non_interactive )); then
        INIT_ARGS+=(--non-interactive)
        attached=0
    else
        attached=1
    fi
    if (( ! attached )) && [[ -z "$provider" ]]; then
        if (( dry_run )); then
            detail "would stop here: no terminal and no --provider; the flag list is printed instead"
            return 0
        fi
        die "no terminal and no --provider. Pass --provider <name> (optionally --model, --database-url, --label, --voice-layer), or run with a terminal"
    fi
    if (( dry_run )); then
        detail "would run: uv run monoagens init --dir $dir (the database URL stays out of this output)"
        return 0
    fi
    if (( attached )); then
        detail "interactive setup; answers are written only after the review step"
        local code=0
        # Do not pipe stdout through tee here: init uses curses only when both
        # stdin and stdout are terminals. The interactive screen stays on the
        # terminal; the log records the stage, never the wizard's secrets.
        uv_run_in_dir "${INIT_ARGS[@]}" || code=$?
        log "interactive init exited with status $code; wizard output stayed on the terminal"
        if (( code == 130 )); then
            say ""
            say "cancelled; nothing was written"
            exit 130
        fi
        if (( code != 0 )); then
            detail "last log lines:"
            tail -n 15 -- "$log_file" | sed 's/^/     /' >&2 || true
            die "monoagens init failed (exit $code); see $log_file"
        fi
        return 0
    fi
    run_logged "write the four host files" "monoagens init --dir $dir --non-interactive" \
        uv_run_in_dir "${INIT_ARGS[@]}"
}

# --- database and self-test -----------------------------------------------------------


have_postgres() {
    command -v psql >/dev/null 2>&1 || command -v pg_isready >/dev/null 2>&1
}

postgres_hint() {
    warn "Postgres is not on PATH; install it, then re-run:"
    warn "  dnf install postgresql-server pgvector"
    warn "  postgresql-setup --initdb"
}

database_configured() {
    if [[ -n "$database_url" || -n "${MONOAGENS_DATABASE_URL:-}" ]]; then
        return 0
    fi
    [[ -f "$dir/.env" ]] || return 1
    grep -Eq '^MONOAGENS_DATABASE_URL=[^[:space:]]' "$dir/.env"
}

# `prepare_database_env MODE` fills .env with one generated password and one URL.
# MODE is native (engine on host) or compose (engine in the stack). It prints
# "container" when the database belongs to compose, "external" when the operator
# supplied their own server. A generated URL carries MONOAGENS_DB_CONTAINER=1 so a
# later run repairs the container instead of mistaking it for an external server.
prepare_database_env() {
    local mode="$1" result
    if (( dry_run )); then
        detail "would prepare the $mode database credentials in $dir/.env" >&2
        printf 'container'
        return 0
    fi
    result="$(
        "$python_bin" - "$dir/.env" "$mode" <<'PY'
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

path = Path(sys.argv[1])
mode = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def get(name: str) -> str:
    for line in lines:
        if line.partition("=")[0].strip() == name and "=" in line:
            return line.partition("=")[2].strip()
    return ""


def set_key(name: str, value: str) -> None:
    for index, line in enumerate(lines):
        if line.partition("=")[0].strip() == name and "=" in line:
            lines[index] = f"{name}={value}"
            return
    lines.append(f"{name}={value}")


url = get("MONOAGENS_DATABASE_URL")
password = get("POSTGRES_PASSWORD")
container_marker = get("MONOAGENS_DB_CONTAINER") == "1"
if mode == "native" and url and not container_marker:
    print("external")
    sys.exit(0)
if not url:
    port = get("POSTGRES_PORT") or "5432"
    name = get("POSTGRES_DB") or "monoagens"
    url = f"postgresql+psycopg://monoagens@127.0.0.1:{port}/{name}"
parts = urlsplit(url)
if parts.scheme != "postgresql+psycopg":
    print("database URL must use postgresql+psycopg://", file=sys.stderr)
    sys.exit(2)
if not parts.hostname:
    print("database URL needs a host", file=sys.stderr)
    sys.exit(2)
if parts.username and parts.username != "monoagens":
    print("the container database user must be monoagens", file=sys.stderr)
    sys.exit(2)
try:
    port = parts.port or 5432
except ValueError:
    print("database URL has an invalid port", file=sys.stderr)
    sys.exit(2)
name = parts.path.lstrip("/") or "monoagens"
url_password = unquote(parts.password) if parts.password else ""
if password and url_password and password != url_password:
    print("POSTGRES_PASSWORD does not match the database URL", file=sys.stderr)
    sys.exit(2)
password = password or url_password or secrets.token_hex(24)
host = parts.hostname
if host not in ("127.0.0.1", "localhost", "::1"):
    print("compose mode needs a local Postgres URL; omit --database-url", file=sys.stderr)
    sys.exit(2)
netloc = f"monoagens:{quote(password, safe='')}@{host}:{port}"
url = urlunsplit((parts.scheme, netloc, f"/{name}", parts.query, parts.fragment))
set_key("POSTGRES_PASSWORD", password)
set_key("POSTGRES_PORT", str(port))
set_key("POSTGRES_DB", name)
set_key("MONOAGENS_DATABASE_URL", url)
set_key("MONOAGENS_DB_CONTAINER", "1")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
os.chmod(path, 0o600)
print("container")
PY
    )" || die "cannot prepare the database environment"
    printf '%s' "$result"
}

stage_database() {
    if ! database_configured; then
        stage "schema: waiting for .env"
        if ! have_postgres; then
            postgres_hint
        fi
        detail "MONOAGENS_DATABASE_URL is empty; after filling it run: monoagens db upgrade"
        return 0
    fi
    run_logged "publish the schema (monoagens db upgrade)" "monoagens db upgrade" \
        uv_run_in_dir db upgrade
}

stage_selftest() {
    if (( skip_selftest )); then
        stage "self-check: skipped"
        detail "run it yourself later: monoagens selftest"
        return 0
    fi
    if ! database_configured; then
        stage "self-check: skipped"
        detail "it needs a database URL; fill .env, then run: monoagens selftest"
        return 0
    fi
    run_soft "self-check (monoagens selftest)" "monoagens selftest" uv_run_in_dir selftest || true
}

# --- compose stack --------------------------------------------------------------------


detect_compose() {
    if command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then
        COMPOSE=(podman compose)
        return 0
    fi
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        COMPOSE=(docker compose)
        return 0
    fi
    if command -v podman-compose >/dev/null 2>&1; then
        COMPOSE=(podman-compose)
        return 0
    fi
    return 1
}

runtime_hint() {
    local runtime="podman" compose="podman-compose"
    if command -v docker >/dev/null 2>&1 && ! command -v podman >/dev/null 2>&1; then
        runtime="docker"
        compose="docker-compose"
    fi
    warn "Podman or Docker with compose is required for the database container."
    if command -v pacman >/dev/null 2>&1; then
        warn "  sudo pacman -S --needed $runtime $compose"
    elif command -v dnf >/dev/null 2>&1; then
        warn "  sudo dnf install -y $runtime $compose"
    elif command -v apt-get >/dev/null 2>&1; then
        warn "  sudo apt-get update && sudo apt-get install -y $runtime $compose"
    elif command -v zypper >/dev/null 2>&1; then
        warn "  sudo zypper install -y $runtime $compose"
    else
        warn "  install $runtime and $compose, then re-run"
    fi
    warn "or re-run with --install-runtime to install it now"
}

install_runtime() {
    local packages=()
    if ! command -v podman >/dev/null 2>&1 && ! command -v docker >/dev/null 2>&1; then
        packages+=(podman)
    fi
    if ! detect_compose; then
        if command -v docker >/dev/null 2>&1; then
            packages+=(docker-compose)
        else
            packages+=(podman-compose)
        fi
    fi
    if ((${#packages[@]} == 0)); then
        return 0
    fi
    if command -v pacman >/dev/null 2>&1; then
        run_logged "install the container runtime" \
            "sudo pacman -S --needed --noconfirm ${packages[*]}" \
            sudo pacman -S --needed --noconfirm "${packages[@]}"
    elif command -v dnf >/dev/null 2>&1; then
        run_logged "install the container runtime" \
            "sudo dnf install -y ${packages[*]}" \
            sudo dnf install -y "${packages[@]}"
    elif command -v apt-get >/dev/null 2>&1; then
        run_logged "install the container runtime" \
            "sudo apt-get update && sudo apt-get install -y ${packages[*]}" \
            sudo bash -c 'apt-get update && apt-get install -y "$@"' _ "${packages[@]}"
    elif command -v zypper >/dev/null 2>&1; then
        run_logged "install the container runtime" \
            "sudo zypper install -y ${packages[*]}" \
            sudo zypper install -y "${packages[@]}"
    else
        runtime_hint
        die "no supported package manager; install the runtime yourself and re-run"
    fi
}

ensure_compose() {
    detect_compose && return 0
    if (( dry_run )); then
        detail "would install Podman/Docker compose if missing"
        return 0
    fi
    if (( install_runtime )); then
        install_runtime
    elif [[ -t 0 ]] && (( ! non_interactive )); then
        local answer=""
        say ""
        say "Podman/Docker with compose is not available."
        read -r -p "  install Podman now with sudo? [y/N]: " answer || answer=""
        case "$answer" in
            y | Y | yes | YES) install_runtime ;;
            *) runtime_hint; die "container runtime is required for the database" ;;
        esac
    else
        runtime_hint
        die "container runtime is required; pass --install-runtime or install it yourself"
    fi
    detect_compose || die "the runtime installed but compose is still unavailable"
}

postgres_password_set() {
    [[ -f "$dir/.env" ]] || return 1
    grep -Eq '^POSTGRES_PASSWORD=[^[:space:]]' "$dir/.env"
}

compose_up() {
    ( cd -- "$dir" && exec "${COMPOSE[@]}" up -d "$@" )
}

compose_run() {
    ( cd -- "$dir" && exec "${COMPOSE[@]}" exec -T monoagens monoagens "$@" )
}

wait_for_postgres() {
    local attempt
    for attempt in {1..30}; do
        if "${COMPOSE[@]}" exec -T postgres pg_isready -U monoagens >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

stage_postgres() {
    ensure_compose
    run_logged "start the Postgres container" \
        "${COMPOSE[*]:-podman compose} up -d postgres" compose_up postgres
    run_logged "wait for Postgres" "compose exec postgres pg_isready" wait_for_postgres
}

stage_native_database() {
    local mode
    mode="$(prepare_database_env native)"
    if [[ "$mode" == "container" ]]; then
        stage_postgres
    fi
    stage_database
    stage_selftest
}

stage_compose() {
    prepare_database_env compose >/dev/null
    ensure_compose
    run_logged "bring the stack up" "${COMPOSE[*]:-podman compose} up -d" compose_up
    run_logged "wait for Postgres" "compose exec postgres pg_isready" wait_for_postgres
    run_logged "publish the schema (compose exec)" \
        "${COMPOSE[*]:-podman compose} exec monoagens monoagens db upgrade" compose_run db upgrade
    if (( skip_selftest )); then
        detail "self-check skipped (--skip-selftest)"
    else
        run_soft "self-check (compose exec)" \
            "${COMPOSE[*]:-podman compose} exec monoagens monoagens selftest" compose_run selftest || true
    fi
}

# --- finish ---------------------------------------------------------------------------


panel_available() {
    [[ -f "$dir/panel/pyproject.toml" ]] || return 1
    [[ -f "$dir/config.yaml" ]] || return 1
    awk '
        /^[[:space:]]*web:[[:space:]]*$/ {
            match($0, /^[[:space:]]*/)
            base = RLENGTH
            inside = 1
            next
        }
        inside {
            if ($0 ~ /^[[:space:]]*$/) next
            match($0, /^[[:space:]]*/)
            if (RLENGTH <= base) { inside = 0; next }
            if ($0 ~ /^[[:space:]]+enabled:[[:space:]]*true/) { found = 1 }
        }
        END { exit(found ? 0 : 1) }
    ' "$dir/config.yaml"
}

print_cli_steps() {
    local runner="${COMPOSE[*]:-podman compose}"
    say ""
    if [[ "$stack" == "compose" ]]; then
        say "  Stay on the command line inside the stack:"
        say "    cd $dir"
        if ! postgres_password_set; then
            say "    # fill .env first: POSTGRES_PASSWORD, MONOAGENS_DATABASE_URL, and the provider key"
        fi
        say "    $runner exec monoagens monoagens db upgrade"
        say "    $runner exec monoagens monoagens run"
        say "  Run the setup inside the container too: $runner exec monoagens monoagens init --dir ."
    else
        say "  Stay on the command line:"
        say "    cd $dir"
        if ! database_configured; then
            say "    # fill .env first: MONOAGENS_DATABASE_URL, POSTGRES_PASSWORD, and the provider key"
        fi
        say "    uv run monoagens db upgrade"
        say "    uv run monoagens run"
        say "  Run the setup again: uv run monoagens init --dir ."
    fi
    say ""
    say "  Log: $log_file"
}

open_panel() {
    if (( dry_run )); then
        detail "would start the panel: (cd $dir && uv run --project panel monoagens-panel)"
        return 0
    fi
    say "  starting the panel; Ctrl-C stops it"
    ( cd -- "$dir" && exec "$uv" run --project panel monoagens-panel )
}

finish() {
    stage "what next"
    if panel_available; then
        detail "panel: available in $dir/panel (web.enabled is true)"
        if [[ -t 0 ]] && (( ! non_interactive )); then
            say "  1) open the panel"
            say "  2) stay on the command line"
            local answer=""
            read -r -p "  choice [2]: " answer || answer="2"
            if [[ "$answer" == "1" ]]; then
                open_panel
                return 0
            fi
        else
            detail "start it with: (cd $dir && uv run --project panel monoagens-panel)"
        fi
    else
        detail "panel: not built in this version, so the command line is the only path."
    fi
    print_cli_steps
}

# --- main -----------------------------------------------------------------------------


main() {
    parse_args "$@"
    setup_colors
    ask_runtime
    ask_directory
    detect_arch
    say "MonoAgens installer - $REPO_URL ($(revision_label))"
    say "stack: $stack | uv pin: $UV_VERSION | arch: $arch | dry-run: $([[ $dry_run == 1 ]] && echo yes || echo no)"
    log_init
    install_traps
    stage_preflight
    stage_uv
    prepare_dir
    run_logged "install the locked dependencies (uv sync --locked)" "uv sync --locked" uv_sync
    run_init
    if [[ "$stack" == "compose" ]]; then
        stage_compose
    else
        stage_native_database
    fi
    finish
    stage "done"
    detail "host files: $dir"
    detail "log: $log_file"
    return 0
}

if [[ -z "${MONOAGENS_INSTALL_SOURCED:-}" ]]; then
    main "$@"
fi
