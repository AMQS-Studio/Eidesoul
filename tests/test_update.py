"""`monoagens update`: the HTTP archive bundle and the host-level compose lifecycle."""

import io
import tarfile
from pathlib import Path
from typing import Any, cast

import yaml

from monoagens.admin import service as admin_service


def archive(files: dict[str, bytes], top: str = "MonoAgens-main") -> bytes:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as bundle:
        for name, body in files.items():
            info = tarfile.TarInfo(f"{top}/{name}" if not name.startswith("/") else name)
            info.size = len(body)
            bundle.addfile(info, io.BytesIO(body))
    return payload.getvalue()


def test_the_archive_refreshes_code_and_keeps_operator_files(tmp_path) -> None:
    (tmp_path / "roles.yaml").write_text("roles: {}\n", encoding="utf-8")
    (tmp_path / "plugins" / "mine").mkdir(parents=True)
    (tmp_path / "plugins" / "mine" / "plugin.yaml").write_text("name: mine\n", encoding="utf-8")
    (tmp_path / "monoagens").mkdir()
    (tmp_path / "monoagens" / "old.py").write_text("# old\n", encoding="utf-8")

    written = admin_service.apply_archive(
        tmp_path,
        archive(
            {
                "monoagens/engine.py": b"# new\n",
                "uv.lock": b"# lock\n",
                "roles.yaml": b"roles: stolen\n",
                "config.yaml": b"engine: stolen\n",
                ".env": b"SECRET=stolen\n",
                "plugins/mine/plugin.yaml": b"name: stolen\n",
            }
        ),
    )

    assert sorted(written) == ["monoagens/engine.py", "uv.lock"]
    assert (tmp_path / "monoagens" / "engine.py").read_text() == "# new\n"
    assert (tmp_path / "uv.lock").read_text() == "# lock\n"
    assert (tmp_path / "roles.yaml").read_text() == "roles: {}\n"
    assert not (tmp_path / "config.yaml").exists()
    assert not (tmp_path / ".env").exists()
    assert (tmp_path / "plugins" / "mine" / "plugin.yaml").read_text() == "name: mine\n"
    # an update never deletes a file the operator still has
    assert (tmp_path / "monoagens" / "old.py").read_text() == "# old\n"


def test_the_archive_never_escapes_the_root(tmp_path) -> None:
    written = admin_service.apply_archive(
        tmp_path, archive({"../escape.py": b"# nope\n"})
    )
    assert written == []
    assert not (tmp_path.parent / "escape.py").exists()


def test_extras_follow_the_enabled_gateways(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "monoagens"\nversion = "0"\n'
        '[project.optional-dependencies]\nwhatsapp = []\ntelegram = []\n',
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {"gateways": {"whatsapp": {"enabled": True}, "telegram": {"enabled": False}}}
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("POSTGRES_PASSWORD=keep\n", encoding="utf-8")

    assert admin_service.write_extras(tmp_path) == "--extra whatsapp"
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=keep" in text
    assert "EXTRAS=--extra whatsapp" in text
    assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600


def test_the_compose_update_builds_migrates_and_recreates(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, cwd):
        calls.append(list(argv))
        return 0

    monkeypatch.setattr(admin_service, "_run", fake_run)
    monkeypatch.setattr(admin_service, "compose_argv", lambda service: ["podman", "compose"])
    monkeypatch.setattr(admin_service, "_compose_profiles", lambda service: [])
    monkeypatch.setattr(admin_service, "write_extras", lambda root: "")

    service = admin_service.Service(
        root=tmp_path,
        mode=admin_service.COMPOSE,
        host=cast(Any, object()),
        paths=cast(Any, object()),
        state=tmp_path / ".monoagens",
    )

    assert admin_service.compose_update(service) == 0
    assert calls == [
        ["podman", "compose", "build", "monoagens"],
        ["podman", "compose", "run", "--rm", "monoagens", "monoagens", "db", "upgrade"],
        ["podman", "compose", "up", "-d", "--force-recreate", "monoagens"],
    ]


def test_the_compose_update_stops_at_a_failed_build(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, cwd):
        calls.append(list(argv))
        return 2

    monkeypatch.setattr(admin_service, "_run", fake_run)
    monkeypatch.setattr(admin_service, "compose_argv", lambda service: ["docker", "compose"])
    monkeypatch.setattr(admin_service, "_compose_profiles", lambda service: [])
    monkeypatch.setattr(admin_service, "write_extras", lambda root: "")

    service = admin_service.Service(
        root=tmp_path,
        mode=admin_service.COMPOSE,
        host=cast(Any, object()),
        paths=cast(Any, object()),
        state=tmp_path / ".monoagens",
    )
    assert admin_service.compose_update(service) == 2
    assert calls == [["docker", "compose", "build", "monoagens"]]


def test_the_wrapper_runs_lifecycle_verbs_on_the_host() -> None:
    text = (Path(__file__).resolve().parents[1] / "install.sh").read_text(encoding="utf-8")
    host = text[text.index("Lifecycle verbs belong to the host") :]
    block = host[: host.index("esac")]
    for verb in (
        "update",
        "upgrade",
        "start",
        "stop",
        "restart",
        "status",
        "logs",
        "uninstall",
    ):
        assert verb in block
    assert ".venv/bin/python -m monoagens" in block
    # the container path stays for the interactive commands
    assert 'exec "${COMPOSE[@]}" run --rm' in text


def test_update_core_fetches_the_archive_before_syncing(tmp_path, monkeypatch) -> None:
    from monoagens import cli

    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_fetch(root, url):
        seen["url"] = url
        return ["monoagens/engine.py"]

    class Done:
        returncode = 0

    monkeypatch.setattr(admin_service, "fetch_archive", fake_fetch)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: Done())

    assert cli._update_core(tmp_path) == 0
    assert str(seen["url"]).endswith("/archive/refs/heads/main.tar.gz")


def test_update_core_keeps_the_local_tree_when_the_download_fails(tmp_path, monkeypatch) -> None:
    from monoagens import cli

    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    synced: list[object] = []

    def fail(root, url):
        raise OSError("offline")

    class Done:
        returncode = 0

    def fake_run(*args, **kwargs):
        synced.append(args[0])
        return Done()

    monkeypatch.setattr(admin_service, "fetch_archive", fail)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli._update_core(tmp_path) == 0
    assert synced
    assert synced[0][-3:] == ["sync", "--locked", "--no-dev"]


def test_update_refreshes_host_files_before_reading_the_service(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    from monoagens import cli
    from monoagens.admin import service as admin_service
    from monoagens.admin import templates as admin_templates

    order: list[str] = []
    paths = SimpleNamespace(config=tmp_path / "config.yaml")
    monkeypatch.setattr(cli, "load_host", lambda config: (SimpleNamespace(), paths))
    monkeypatch.setattr(cli, "_update_core", lambda root: order.append("core") or 0)
    monkeypatch.setattr(
        admin_templates,
        "refresh_host_files",
        lambda target: order.append("refresh") or [target.config],
    )
    monkeypatch.setattr(
        admin_service,
        "load",
        lambda config: order.append("load")
        or SimpleNamespace(mode=admin_service.NATIVE, root=tmp_path),
    )
    monkeypatch.setattr(admin_service, "read_pid", lambda service: None)
    monkeypatch.setattr(admin_service, "reload", lambda config, reason="": 0)
    monkeypatch.setattr(
        "monoagens.admin.database.upgrade", lambda config: order.append("db")
    )

    args = SimpleNamespace(config=str(tmp_path / "config.yaml"), core=True)
    assert cli._update(args) == 0
    assert order == ["core", "refresh", "load", "db"]
