"""Installer contract: dry-run writes nothing, sudo needs consent, no secret in the log.

ADR-0030 §4.1/§7: `bash install.sh --dry-run` prints the plan and leaves the filesystem
unchanged; a normal run performs no `sudo` call; a marker password never reaches the install
log; the pinned uv archive is checked before anything else happens.
"""


import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "install.sh"
UV_DIGESTS = (
    "c5221ff41b7892bfc223543883600125f6a47fe845093e0f5bdff4f359196d83",
    "71279f8d3c67241e807b9d8db872d1bf541579fb9f08b7d1eb1682196d1e57dc",
)
MARKER = "MARKER-PASSWORD-9f1c"


def clean_env(**overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    for name in ("MONOAGENS_DATABASE_URL", "MONOAGENS_DATABASE_URL", "MONOAGENS_INSTALL_SOURCED"):
        env.pop(name, None)
    env["NO_COLOR"] = "1"
    env.update(overrides)
    return env


def listing(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def run_script(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def stub_bin(tmp_path: Path) -> tuple[Path, Path]:
    """A PATH with a no-op uv, a Python 3.14, and a sudo that shouts when it is called.

    `uv run monoagens init` is delegated to the interpreter in `STUB_MONOAGENS_PYTHON` so the
    end-to-end test exercises the real init; every other child exits 0.
    """
    folder = tmp_path / "bin"
    folder.mkdir()
    uv = folder / "uv"
    uv.write_text(
        '#!/usr/bin/env bash\n'
        'if [[ "${1:-}" == "--version" ]]; then echo "uv 0.13.7 (fake)"; exit 0; fi\n'
        'if [[ "${1:-}" == "run" && "${2:-}" == "monoagens" && "${3:-}" == "init" '
        '&& -n "${STUB_MONOAGENS_PYTHON:-}" ]]; then\n'
        '    shift 3\n'
        '    exec "$STUB_MONOAGENS_PYTHON" -m monoagens init "$@"\n'
        'fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    python = folder / "python3"
    python.write_text(
        '#!/usr/bin/env bash\n'
        'if [[ "${1:-}" == "-V" ]]; then echo "Python 3.14.0"; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    sudo = folder / "sudo"
    sudo.write_text(
        '#!/usr/bin/env bash\ntouch "$SUDO_MARKER"\nexit 77\n', encoding="utf-8"
    )
    for path in (uv, python, sudo):
        path.chmod(0o755)
    return folder, tmp_path / "sudo-called"


def test_dry_run_prints_the_plan_and_writes_nothing(tmp_path):
    before = listing(tmp_path)
    env = clean_env(HOME=str(tmp_path / "home"))

    result = run_script(
        ["--dry-run", "--dir", str(tmp_path / "app"), "--provider", "openai"], env
    )

    assert result.returncode == 0, result.stderr
    assert "dry-run: yes" in result.stdout
    assert "would run: uv sync --locked" in result.stdout
    assert "would run: uv run monoagens init" in result.stdout
    assert "\x1b[" not in result.stdout
    assert listing(tmp_path) == before == []


def test_a_stubbed_run_keeps_the_secret_out_of_the_log_and_never_calls_sudo(tmp_path):
    folder, sudo_marker = stub_bin(tmp_path)
    app = tmp_path / "app"
    app.mkdir()
    (app / "pyproject.toml").write_text("[project]\nname = 'fake'\n", encoding="utf-8")
    env = clean_env(
        HOME=str(tmp_path / "home"),
        PATH=f"{folder}:/usr/bin:/bin",
        SUDO_MARKER=str(sudo_marker),
        STUB_MONOAGENS_PYTHON=sys.executable,
        PYTHONPATH=str(ROOT),
    )
    url = f"postgresql+psycopg://monoagens:{MARKER}@127.0.0.1:5433/monoagens"

    result = run_script(
        [
            "--dir",
            str(app),
            "--provider",
            "openai",
            "--database-url",
            url,
            "--skip-selftest",
            "--non-interactive",
        ],
        env,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert not sudo_marker.exists(), "install.sh called sudo"
    log = tmp_path / "home" / ".monoagens" / "logs" / "install.log"
    assert log.is_file()
    assert log.stat().st_mode & 0o777 == 0o600
    text = log.read_text(encoding="utf-8")
    assert MARKER not in text
    assert MARKER not in output
    assert "database-url=<provided>" in text
    assert "monoagens init" in text
    assert "panel: not built in this version" in output
    assert "uv run monoagens run" in output
    for name, mode in (
        ("config.yaml", 0o644),
        ("roles.yaml", 0o600),
        ("soul.yaml", 0o644),
        (".env", 0o600),
    ):
        path = app / name
        assert path.is_file(), f"{name} was not written by the installer"
        assert path.stat().st_mode & 0o777 == mode
    assert MARKER in (app / ".env").read_text(encoding="utf-8")


def test_the_non_interactive_run_without_provider_prints_the_flag_list(tmp_path):
    folder, _ = stub_bin(tmp_path)
    app = tmp_path / "app"
    app.mkdir()
    (app / "pyproject.toml").write_text("[project]\nname = 'fake'\n", encoding="utf-8")
    env = clean_env(
        HOME=str(tmp_path / "home"), PATH=f"{folder}:/usr/bin:/bin"
    )

    result = run_script(["--dir", str(app), "--non-interactive"], env)

    assert result.returncode != 0
    assert "--provider" in result.stderr
    assert not (app / "config.yaml").exists()


def test_the_pinned_uv_digest_is_checked_before_anything_else(tmp_path):
    source = SCRIPT.read_text(encoding="utf-8")
    body = source[source.index("install_uv() {") :]
    assert body.index("verify_sha256") < body.index("tar -xzf")


def test_the_pin_block_holds_real_digests_and_verification_works(tmp_path):
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
printf 'payload' > "$2/archive"
digest="$(sha256sum "$2/archive" | awk '{print $1}')"
verify_sha256 "$2/archive" "$digest" || exit 4
zero=0000000000000000000000000000000000000000000000000000000000000000
if verify_sha256 "$2/archive" "$zero"; then exit 5; fi
detect_arch
uv_digest
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT), str(tmp_path)],
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() in UV_DIGESTS


def test_a_missing_package_is_printed_not_installed(tmp_path):
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
postgres_hint
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT)],
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0
    assert "dnf install postgresql-server pgvector" in result.stderr
    assert "postgresql-setup --initdb" in result.stderr


def test_the_closing_choice_offers_the_panel_only_when_it_is_built(tmp_path):
    app = tmp_path / "app"
    (app / "panel").mkdir(parents=True)
    (app / "panel" / "pyproject.toml").write_text("[project]\nname = 'panel'\n", encoding="utf-8")
    (app / "config.yaml").write_text("web:\n  enabled: true\n  host: 127.0.0.1\n", encoding="utf-8")
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
dir="$2"
if panel_available; then echo yes; else echo no; fi
"""
    env = clean_env()
    args = ["bash", "-c", code, "_", str(ROOT), str(app)]
    offered = subprocess.run(
        args, env=env, capture_output=True, text=True, timeout=60, check=False
    )
    assert offered.stdout.strip() == "yes"

    (app / "config.yaml").write_text("web:\n  enabled: false\n", encoding="utf-8")
    withheld = subprocess.run(
        args, env=env, capture_output=True, text=True, timeout=60, check=False
    )
    assert withheld.stdout.strip() == "no"


def test_sudo_is_confined_to_the_explicit_runtime_installer():
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("install_runtime() {"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    sudo_lines = [i for i, line in enumerate(lines) if re.match(r"\s*sudo\b", line)]

    assert sudo_lines, "the opt-in runtime installer should carry the sudo commands"
    assert all(start < i < end for i in sudo_lines)
    source = "\n".join(lines)
    assert "install Podman now with sudo?" in source
    assert "--install-runtime" in source


def test_an_existing_archive_checkout_is_refreshed(tmp_path):
    app = tmp_path / "app"
    (app / "monoagens").mkdir(parents=True)
    (app / "pyproject.toml").write_text("[project]\nname = 'monoagens'\n", encoding="utf-8")
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
dir="$2"
source_root=""
non_interactive=1
refresh_archive_checkout() { echo REFRESHED; }
prepare_dir
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT), str(app)],
        env=clean_env(HOME=str(tmp_path)),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "REFRESHED" in result.stdout


def test_directory_normalization_treats_dot_as_the_current_directory(tmp_path):
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
normalize_directory "./"
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT)],
        cwd=tmp_path,
        env=clean_env(HOME=str(tmp_path)),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(tmp_path)


def test_database_env_generation_is_idempotent_and_external_is_untouched(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
dir="$2"
python_bin="$3"
mode="$(prepare_database_env native)"
printf '%s\n' "$mode"
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT), str(app), sys.executable],
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "container"
    env = app / ".env"
    assert env.stat().st_mode & 0o777 == 0o600
    text = env.read_text(encoding="utf-8")
    password = next(
        line.partition("=")[2]
        for line in text.splitlines()
        if line.startswith("POSTGRES_PASSWORD=")
    )
    assert password
    assert password not in result.stdout + result.stderr
    assert "MONOAGENS_DB_CONTAINER=1" in text
    assert f"monoagens:{password}@127.0.0.1:5432/monoagens" in text

    second = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT), str(app), sys.executable],
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == "container"
    assert env.read_text(encoding="utf-8") == text

    external = tmp_path / "external"
    external.mkdir()
    (external / ".env").write_text(
        "MONOAGENS_DATABASE_URL=postgresql+psycopg://monoagens:secret@db.example:5432/monoagens\n",
        encoding="utf-8",
    )
    before = (external / ".env").read_text(encoding="utf-8")
    external_result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT), str(external), sys.executable],
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert external_result.returncode == 0, external_result.stderr
    assert external_result.stdout.strip() == "external"
    assert (external / ".env").read_text(encoding="utf-8") == before
