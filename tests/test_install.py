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

import pytest

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

    `uv run python -m monoagens init` is delegated to the interpreter in
    `STUB_MONOAGENS_PYTHON` so the end-to-end test exercises the real init; every other
    child exits 0.
    """
    folder = tmp_path / "bin"
    folder.mkdir()
    uv = folder / "uv"
    uv.write_text(
        '#!/usr/bin/env bash\n'
        'if [[ "${1:-}" == "--version" ]]; then echo "uv 0.13.7 (fake)"; exit 0; fi\n'
        'if [[ "${1:-}" == "run" && "${2:-}" == "python" && "${3:-}" == "-m" '
        '&& "${4:-}" == "monoagens" && "${5:-}" == "init" '
        '&& -n "${STUB_MONOAGENS_PYTHON:-}" ]]; then\n'
        '    shift 5\n'
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
    assert "would run: uv sync --locked --no-dev" in result.stdout
    assert "would run: uv run python -m monoagens init" in result.stdout
    assert "\x1b[" not in result.stdout
    assert listing(tmp_path) == before == []


def test_a_stubbed_run_keeps_the_secret_out_of_the_log_and_never_calls_sudo(tmp_path):
    folder, sudo_marker = stub_bin(tmp_path)
    app = tmp_path / "app"
    app.mkdir()
    (app / "pyproject.toml").write_text("[project]\nname = 'fake'\n", encoding="utf-8")
    (app / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
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
            "--language",
            "en",
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
    assert "The global launcher is installed" in output
    assert "monoagens run" in output
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


def test_host_locale_without_a_language_requires_the_flag(tmp_path):
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
non_interactive=1
ask_language
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT)],
        env=clean_env(LC_ALL="", LC_MESSAGES="", LANGUAGE="", LANG="C.UTF-8"),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode != 0
    assert "--language" in result.stderr


def test_language_fields_are_forwarded_to_init(tmp_path):
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
dir="$2"
language=en
locale=en-US
timezone=Asia/Jakarta
build_init_args
printf '%s\n' "${INIT_ARGS[@]}"
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT), str(tmp_path / "app")],
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    args = result.stdout.splitlines()
    assert args[args.index("--language") + 1] == "en"
    assert args[args.index("--locale") + 1] == "en-US"
    assert args[args.index("--timezone") + 1] == "Asia/Jakarta"


def test_install_ui_turns_ctrl_c_into_a_clean_cancel(monkeypatch, tmp_path):
    from monoagens.admin import install_ui

    def interrupted(**_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(install_ui.terminal, "choose", interrupted)
    assert install_ui.main(["stack", "--out", str(tmp_path / "answer")]) == 130


def test_a_line_menu_reprompts_on_an_invalid_choice(tmp_path):
    out = tmp_path / "stack.txt"
    result = subprocess.run(
        [sys.executable, "-m", "monoagens.admin.install_ui", "stack", "--out", str(out)],
        input="x\n2\n",
        cwd=ROOT,
        env=clean_env(PYTHONPATH=str(ROOT)),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "choose 1-2" in result.stdout
    assert out.read_text(encoding="utf-8").strip() == "compose"


def test_init_receives_capability_choices(tmp_path):
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
dir="$2"
gateways=(whatsapp signal)
websearch=ddgs
tools=read,web_search
voice_parts=affect,style
build_init_args
printf '%s\n' "${INIT_ARGS[@]}"
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT), str(tmp_path / "app")],
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    args = result.stdout.splitlines()
    assert "--capabilities" not in args
    assert args.count("--gateway") == 2
    assert args[args.index("--websearch") + 1] == "ddgs"
    assert args[args.index("--tools") + 1] == "read,web_search"
    assert args[args.index("--voice-parts") + 1] == "affect,style"


def test_the_non_interactive_run_without_provider_prints_the_flag_list(tmp_path):
    folder, _ = stub_bin(tmp_path)
    app = tmp_path / "app"
    app.mkdir()
    (app / "pyproject.toml").write_text("[project]\nname = 'fake'\n", encoding="utf-8")
    (app / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
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
    assert "sudo will ask for your password" in source
    assert "--install-runtime" in source


def test_installer_menus_return_the_selected_values(tmp_path):
    def run(args, answer):
        out = tmp_path / f"{args[0]}.txt"
        result = subprocess.run(
            [sys.executable, "-m", "monoagens.admin.install_ui", *args, "--out", str(out)],
            input=answer,
            cwd=ROOT,
            env=clean_env(PYTHONPATH=str(ROOT)),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return out.read_text(encoding="utf-8").strip()

    assert run(["stack"], "2\n") == "compose"
    location = run(["location", "--default", "/tmp/default", "--here", "/tmp/here"], "2\n")
    assert location == "/tmp/here"
    assert run(["database", "--external-allowed"], "2\n") == "external"
    assert run(
        ["engine", "--podman", "installed", "--docker", "not installed"], "2\n"
    ) == "docker"


def test_the_installer_refuses_the_home_directory(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
dir="$HOME"
guard_install_dir
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT)],
        env=clean_env(HOME=str(home)),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0
    assert "home directory" in result.stderr


def test_a_non_checkout_directory_is_never_removed(tmp_path):
    target = tmp_path / "keep"
    target.mkdir()
    marker = target / "important.txt"
    marker.write_text("do not delete\n", encoding="utf-8")
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
dir="$2"
prepare_dir
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT), str(target)],
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0
    assert "never removes it" in result.stderr
    assert marker.read_text(encoding="utf-8") == "do not delete\n"


def test_a_partial_checkout_is_not_removed_automatically(tmp_path):
    target = tmp_path / "partial"
    target.mkdir()
    (target / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
dir="$2"
prepare_dir
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT), str(target)],
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0
    assert (target / "install.sh").is_file()


def test_an_unrelated_project_is_not_refreshed(tmp_path):
    target = tmp_path / "other"
    target.mkdir()
    (target / "pyproject.toml").write_text("[project]\nname = 'other'\n", encoding="utf-8")
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
dir="$2"
prepare_dir
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT), str(target)],
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0
    assert "not a MonoAgens checkout" in result.stderr
    assert (target / "pyproject.toml").read_text(encoding="utf-8") == "[project]\nname = 'other'\n"


def test_prepare_database_env_marks_a_container_database(tmp_path):
    env_file = tmp_path / ".env"
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
dir="$2"
python_bin="$3"
prepare_database_env container "$2/.env"
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT), str(tmp_path), sys.executable],
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "container"
    text = env_file.read_text(encoding="utf-8")
    assert "MONOAGENS_DB_CONTAINER=1" in text
    assert "POSTGRES_PASSWORD=" in text
    assert "MONOAGENS_DATABASE_URL=postgresql+psycopg://" in text


def test_prepare_database_env_prefers_the_existing_container_password(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "POSTGRES_PASSWORD=kept-password\n"
        "MONOAGENS_DATABASE_URL=postgresql+psycopg://monoagens:old-password@127.0.0.1:5432/monoagens\n",
        encoding="utf-8",
    )
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
dir="$2"
python_bin="$3"
prepare_database_env container "$2/.env" >/dev/null
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT), str(tmp_path), sys.executable],
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    text = env_file.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=kept-password" in text
    assert "monoagens:kept-password@" in text
    assert "MONOAGENS_DB_CONTAINER=1" in text


def test_prepare_database_before_init_reuses_existing_credentials(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "POSTGRES_PASSWORD=kept-password\n"
        "MONOAGENS_DATABASE_URL=postgresql+psycopg://monoagens:kept-password@127.0.0.1:5432/monoagens\n",
        encoding="utf-8",
    )
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
dir="$2"
python_bin="$3"
database_mode=container
stack=native
prepare_database_before_init
printf '%s' "$database_url"
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT), str(tmp_path), sys.executable],
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "monoagens:kept-password@" in result.stdout


def test_wait_for_postgres_runs_inside_the_install_directory(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    marker = tmp_path / "cwd"
    fake = tmp_path / "bin"
    fake.mkdir()
    podman = fake / "podman"
    podman.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$PWD" >> "$CWD_MARKER"\nexit 0\n',
        encoding="utf-8",
    )
    podman.chmod(0o755)
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
dir="$2"
COMPOSE=(podman)
wait_for_postgres
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT), str(app)],
        env=clean_env(PATH=f"{fake}:/usr/bin:/bin", CWD_MARKER=str(marker)),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8").strip() == str(app)


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


# --- dynamic catalogs, wrapper, and ordered compose setup ------------------------------


def fake_containers(tmp_path: Path) -> tuple[Path, Path]:
    """A PATH with Podman/Docker that shout when anything real is invoked."""
    folder = tmp_path / "containers"
    folder.mkdir()
    marker = tmp_path / "container-called"
    for name in ("podman", "docker", "podman-compose", "docker-compose"):
        script = folder / name
        script.write_text(
            '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$CONTAINER_MARKER"\nexit 0\n',
            encoding="utf-8",
        )
        script.chmod(0o755)
    return folder, marker


@pytest.mark.parametrize("stack", ["native", "compose"])
def test_dry_run_reads_the_catalogs_and_never_starts_a_container(tmp_path, stack):
    home = tmp_path / "home"
    containers, marker = fake_containers(tmp_path)
    before = listing(tmp_path)
    env = clean_env(
        HOME=str(home),
        SHELL="/bin/bash",
        PATH=f"{containers}:{os.environ['PATH']}",
        CONTAINER_MARKER=str(marker),
    )
    args = [
        "--dry-run",
        "--dir",
        str(tmp_path / "app"),
        "--stack",
        stack,
        "--engine",
        "docker",
        "--provider",
        "google",
        "--model",
        "operator-model",
        "--web-search",
        "tavily",
        "--websearch-key-env",
        "MY_SEARCH_KEY",
        "--gateway",
        "whatsapp",
        "--approval-mode",
        "strict",
        "--voice-parts",
        "affect,style",
        "--language",
        "en",
        "--timezone",
        "UTC",
    ]

    result = run_script(args, env)

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "dry-run: yes" in output
    assert "uv sync --locked --no-dev" in output
    assert "--provider google" in output
    assert "--websearch tavily" in output
    assert "--gateway whatsapp" in output
    assert "--voice-parts affect,style" in output
    assert "engine.approval.mode=strict" in output
    assert "web_search.api_key_env=MY_SEARCH_KEY" in output
    target = f"{home}/.local/bin/monoagens"
    assert output.count(target) == 1
    assert "would install the monoagens wrapper" in output
    assert output.count("to PATH in ") == 1
    assert "exactly once" in output
    assert not marker.exists(), "a dry run called a container command"
    assert listing(tmp_path) == before


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    [
        ("--provider", "mystery", "unknown provider 'mystery'"),
        ("--web-search", "bing", "unknown web search provider 'bing'"),
        ("--gateway", "telegram", "unknown gateway 'telegram'"),
    ],
)
def test_an_unknown_catalog_choice_exits_two(tmp_path, flag, value, message):
    env = clean_env(HOME=str(tmp_path / "home"))

    result = run_script(
        ["--dry-run", "--dir", str(tmp_path / "app"), "--stack", "native", flag, value],
        env,
    )

    assert result.returncode == 2
    assert message in result.stderr
    assert "choose one of" in result.stderr
    assert listing(tmp_path) == []


def test_compose_setup_runs_before_the_daemon_starts(tmp_path):
    env = clean_env(HOME=str(tmp_path / "home"))

    result = run_script(
        [
            "--dry-run",
            "--dir",
            str(tmp_path / "app"),
            "--stack",
            "compose",
            "--engine",
            "docker",
            "--provider",
            "openai",
            "--gateway",
            "whatsapp",
        ],
        env,
    )

    output = result.stdout
    assert result.returncode == 0, result.stderr
    upgrade = output.index("monoagens db upgrade")
    people = output.index("monoagens people add console local")
    pair = output.index("monoagens gateway pair whatsapp")
    daemon = output.index("up -d monoagens")
    assert upgrade < people < pair < daemon
    assert "docker compose" in output


def test_the_retired_voice_layer_flag_names_its_replacement(tmp_path):
    result = run_script(["--dry-run", "--voice-layer", "on"], clean_env())

    assert result.returncode == 2
    assert "retired" in result.stderr
    assert "--voice-parts" in result.stderr


def test_the_voice_and_web_search_aliases_reach_init(tmp_path):
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
set -- --voice affect,style --web-search brave --approval-mode strict \
    --natural-confirm --websearch-key-env BRAVE_TOKEN --gateway whatsapp
parse_args "$@"
build_init_args
printf '%s\n' "${INIT_ARGS[@]}"
printf 'approval=%s natural=%s\\n' "$approval_mode" "$natural_confirm"
printf 'key-env=%s\\n' "$websearch_key_env"
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT)],
        env=clean_env(HOME=str(tmp_path / "home")),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[-2:] == ["approval=strict natural=true", "key-env=BRAVE_TOKEN"]
    assert lines[lines.index("--websearch") + 1] == "brave"
    assert lines[lines.index("--voice-parts") + 1] == "affect,style"
    assert lines[lines.index("--gateway") + 1] == "whatsapp"


def test_the_wrapper_and_path_entry_land_once(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    home = tmp_path / "home"
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
dir="$2/app"
stack=native
container_engine=podman
install_wrapper
install_wrapper
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT), str(tmp_path)],
        env=clean_env(HOME=str(home), SHELL="/bin/bash"),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    target = home / ".local" / "bin" / "monoagens"
    assert target.is_file()
    assert target.stat().st_mode & 0o777 == 0o755
    body = target.read_text(encoding="utf-8")
    assert str(app) in body
    assert ".venv/bin/python" in body
    profile = home / ".profile"
    assert profile.read_text(encoding="utf-8").count(
        "# MonoAgens: put ~/.local/bin on PATH"
    ) == 1
    assert "PATH: added ~/.local/bin" in result.stdout
    assert "PATH already has" in result.stdout


def test_the_compose_wrapper_routes_into_the_stack(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    home = tmp_path / "home"
    code = """
MONOAGENS_INSTALL_SOURCED=1
source "$1/install.sh"
dir="$2/app"
stack=compose
container_engine=docker
install_wrapper
"""
    result = subprocess.run(
        ["bash", "-c", code, "_", str(ROOT), str(tmp_path)],
        env=clean_env(HOME=str(home), SHELL="/bin/bash"),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    body = (home / ".local" / "bin" / "monoagens").read_text(encoding="utf-8")
    assert "COMPOSE=(docker compose)" in body
    assert "run --rm" in body
    assert 'exec "${COMPOSE[@]}" exec' in body
    assert str(app) in body


def test_the_catalog_command_lists_what_the_shell_validates(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "monoagens.admin.catalog", "choices"],
        cwd=ROOT,
        env=clean_env(PYTHONPATH=str(ROOT)),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    kinds = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert "google" in kinds["providers"].split(",")
    assert "tavily" in kinds["websearch"].split(",")
    assert "off" in kinds["websearch"].split(",")
    assert "whatsapp" in kinds["gateways"].split(",")
