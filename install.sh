#!/usr/bin/env bash
# Eidesoul installer: one path, two stacks (native and compose).
#
#   bash install.sh                  # install on this machine, interactive setup
#   bash install.sh --dry-run        # print every action, write nothing
#   bash install.sh --dir "$PWD"     # install into an existing checkout
#   bash install.sh --stack compose  # install the compose stack instead
#
# It never runs sudo, never installs a system package, and never edits PATH: a missing
# package is printed with the exact command that installs it. Everything is appended to
# ~/.eidesoul/logs/install.log. No secret value is ever logged or printed.
#
# Repository: https://github.com/inimuqsith/Eidesoul
# Decision:   docs/v0.1.0/adr/0030-install-and-init.md
set -euo pipefail

REPO_URL="https://github.com/inimuqsith/Eidesoul"
REVISION="main"

# --- uv pin: bump UV_VERSION and both digests together --------------------------------
# The digest list lives at, for the version you pick:
#   https://github.com/astral-sh/uv/releases/download/<version>/uv-<arch>-unknown-linux-gnu.tar.gz.sha256
UV_VERSION="0.13.7"
UV_SHA256_X86_64="c5221ff41b7892bfc223543883600125f6a47fe845093e0f5bdff4f359196d83"
UV_SHA256_AARCH64="71279f8d3c67241e807b9d8db872d1bf541579fb9f08b7d1eb1682196d1e57dc"
# --------------------------------------------------------------------------------------

eidesoul_home="${HOME}/.eidesoul"
default_dir="${eidesoul_home}/app"
log_file="${eidesoul_home}/logs/install.log"
uv_bin="${eidesoul_home}/bin/uv"

dir="$default_dir"
stack="native"
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
    warn "eidesoul install: $*"
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
        printf 'eidesoul install: %s\n\n' "$message" >&2
    fi
    cat <<'EOF'
Eidesoul installer - one path, two stacks.

Usage: bash install.sh [options]

  --dir PATH             install directory (default: ~/.eidesoul/app)
  --stack STACK          native (this machine) or compose (the stack in --dir)
  --dry-run              print every action and write nothing
  --non-interactive      never prompt; fail when a required value is missing
  --provider NAME        provider for `eidesoul init`
  --model ID             model id for `eidesoul init` (optional)
  --database-url URL     Postgres URL; written to .env, never to YAML or the log
  --label TEXT           label for the first administrator (roles.yaml)
  --voice-layer on|off   keep emotion, humanize, character (default on) or turn them off
  --skip-selftest        skip the closing `eidesoul selftest`
  --no-color             plain output (also honoured via NO_COLOR)
  -h, --help             this text

The script never runs sudo and never installs a package: when Postgres, git, or Python is
missing it prints the exact command and stops that stage. Log: ~/.eidesoul/logs/install.log
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
    log "== eidesoul install $(date -Is) =="
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
    printf '\neidesoul install: interrupted; nothing else was started\n' >&2
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
            --dir) [[ $# -ge 2 ]] || usage 2 "missing value for --dir"; dir="$2"; shift 2 ;;
            --stack) [[ $# -ge 2 ]] || usage 2 "missing value for --stack"; stack="$2"; shift 2 ;;
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
            --dir=*) dir="${1#*=}"; shift ;;
            --stack=*) stack="${1#*=}"; shift ;;
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
    for candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
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
        warn "no Python 3.11+ interpreter found; the image keeps its own 3.12 pin."
        warn "Install it, then re-run:"
        warn "  dnf install python3"
        die "Python 3.11+ is required"
    fi
    detail "python: $python_bin ($("$python_bin" -V 2>&1)); the container image pins 3.12"
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
        detail "copy this checkout from $source_root (no .git, no .venv)"
        (( dry_run )) && return 0
        mkdir -p -- "$dir"
        tar -C "$source_root" --exclude=.git --exclude=.venv --exclude=__pycache__ -cf - . \
            | tar -C "$dir" -xf - || die "cannot copy the checkout into $dir"
        return 0
    fi
    if command -v git >/dev/null 2>&1; then
        if [[ -d "$dir" && -n "$(ls -A -- "$dir" 2>/dev/null)" ]]; then
            die "$dir is not empty and has no pyproject.toml; pass --dir at an empty directory"
        fi
        detail "clone $REPO_URL into $dir (no local changes exist to protect)"
        (( dry_run )) && return 0
        mkdir -p -- "$(dirname -- "$dir")"
        git clone --depth 1 -- "$REPO_URL" "$dir" >>"$log_file" 2>&1 \
            || die "git clone failed; see $log_file"
        return 0
    fi
    warn "nothing to install at $dir and no git to fetch a checkout. Install git, then re-run:"
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
    ( cd -- "$dir" && exec "$uv" run eidesoul "$@" )
}

# --- init -----------------------------------------------------------------------------


build_init_args() {
    INIT_ARGS=(init --dir "$dir")
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
    stage "configure the host (eidesoul init)"
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
        detail "would run: uv run eidesoul init --dir $dir (the database URL stays out of this output)"
        return 0
    fi
    if (( attached )); then
        detail "interactive setup; answers are written only after the review step"
        local code=0
        uv_run_in_dir "${INIT_ARGS[@]}" 2>&1 | tee -a "$log_file" || code=$?
        if (( code == 130 )); then
            say ""
            say "cancelled; nothing was written"
            exit 130
        fi
        if (( code != 0 )); then
            detail "last log lines:"
            tail -n 15 -- "$log_file" | sed 's/^/     /' >&2 || true
            die "eidesoul init failed (exit $code); see $log_file"
        fi
        return 0
    fi
    run_logged "write the four host files" "eidesoul init --dir $dir --non-interactive" \
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
    if [[ -n "$database_url" || -n "${EIDESOUL_DATABASE_URL:-}" ]]; then
        return 0
    fi
    [[ -f "$dir/.env" ]] || return 1
    grep -Eq '^EIDESOUL_DATABASE_URL=[^[:space:]]' "$dir/.env"
}

stage_database() {
    if ! database_configured; then
        stage "schema: waiting for .env"
        if ! have_postgres; then
            postgres_hint
        fi
        detail "EIDESOUL_DATABASE_URL is empty; after filling it run: eidesoul db upgrade"
        return 0
    fi
    run_logged "publish the schema (eidesoul db upgrade)" "eidesoul db upgrade" \
        uv_run_in_dir db upgrade
}

stage_selftest() {
    if (( skip_selftest )); then
        stage "self-check: skipped"
        detail "run it yourself later: eidesoul selftest"
        return 0
    fi
    if ! database_configured; then
        stage "self-check: skipped"
        detail "it needs a database URL; fill .env, then run: eidesoul selftest"
        return 0
    fi
    run_soft "self-check (eidesoul selftest)" "eidesoul selftest" uv_run_in_dir selftest || true
}

# --- compose stack --------------------------------------------------------------------


detect_compose() {
    if command -v podman >/dev/null 2>&1; then
        COMPOSE=(podman compose)
        return 0
    fi
    if command -v docker >/dev/null 2>&1; then
        COMPOSE=(docker compose)
        return 0
    fi
    return 1
}

postgres_password_set() {
    [[ -f "$dir/.env" ]] || return 1
    grep -Eq '^POSTGRES_PASSWORD=[^[:space:]]' "$dir/.env"
}

compose_up() {
    ( cd -- "$dir" && exec "${COMPOSE[@]}" up -d )
}

compose_run() {
    ( cd -- "$dir" && exec "${COMPOSE[@]}" exec -T eidesoul eidesoul "$@" )
}

stage_compose() {
    if ! detect_compose; then
        stage "compose: unavailable"
        warn "no podman or docker compose found; the files are written and ready."
        return 0
    fi
    if ! postgres_password_set; then
        stage "compose: waiting for .env"
        warn "the stack needs POSTGRES_PASSWORD in $dir/.env"
        detail "fill it, then re-run: bash install.sh --stack compose --dir $dir"
        return 0
    fi
    run_logged "bring the stack up" "${COMPOSE[*]} up -d" compose_up
    run_logged "publish the schema (docker compose exec)" \
        "${COMPOSE[*]} exec eidesoul eidesoul db upgrade" compose_run db upgrade
    if (( skip_selftest )); then
        detail "self-check skipped (--skip-selftest)"
    else
        run_soft "self-check (docker compose exec)" \
            "${COMPOSE[*]} exec eidesoul eidesoul selftest" compose_run selftest || true
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
            say "    # fill .env first: POSTGRES_PASSWORD, EIDESOUL_DATABASE_URL, and the provider key"
        fi
        say "    $runner exec eidesoul eidesoul db upgrade"
        say "    $runner exec eidesoul eidesoul run"
        say "  Run the setup inside the container too: $runner exec eidesoul eidesoul init --dir ."
    else
        say "  Stay on the command line:"
        say "    cd $dir"
        if ! database_configured; then
            say "    # fill .env first: EIDESOUL_DATABASE_URL, POSTGRES_PASSWORD, and the provider key"
        fi
        say "    uv run eidesoul db upgrade"
        say "    uv run eidesoul run"
        say "  Run the setup again: uv run eidesoul init --dir ."
    fi
    say ""
    say "  Log: $log_file"
}

open_panel() {
    if (( dry_run )); then
        detail "would start the panel: (cd $dir && uv run --project panel eidesoul-panel)"
        return 0
    fi
    say "  starting the panel; Ctrl-C stops it"
    ( cd -- "$dir" && exec "$uv" run --project panel eidesoul-panel )
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
            detail "start it with: (cd $dir && uv run --project panel eidesoul-panel)"
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
    detect_arch
    say "Eidesoul installer - $REPO_URL ($(revision_label))"
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
        stage_database
        stage_selftest
    fi
    finish
    stage "done"
    detail "host files: $dir"
    detail "log: $log_file"
    return 0
}

if [[ -z "${EIDESOUL_INSTALL_SOURCED:-}" ]]; then
    main "$@"
fi
