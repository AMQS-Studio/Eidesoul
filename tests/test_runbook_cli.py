"""Runbook regressions for the CLI parser, people removal, and uninstall guard."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from monoagens import cli
from monoagens.admin import people as admin_people


def test_bare_group_prints_help_and_exits_zero(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--env", ".env", "people"]) == 0
    assert "usage: monoagens people" in capsys.readouterr().out


def test_typo_suggests_and_exits_two(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--env", ".env", "peple"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "error:" in err
    assert "did you mean:" in err
    assert "people" in err


@pytest.mark.parametrize(
    ("argv", "attribute", "value"),
    [
        (["people", "remove", "per_aaaaaaaaaa", "--purge"], "people_command", "remove"),
        (["people", "rm", "per_aaaaaaaaaa", "--role", "guest"], "people_command", "rm"),
        (["gateway", "unpair", "wa"], "gateway_command", "unpair"),
        (["gateway", "uninstall", "wa"], "gateway_command", "uninstall"),
        (["plugins", "rm", "demo"], "plugins_command", "rm"),
        (["tools", "rm", "sandbox"], "tools_command", "rm"),
        (["setting", "reset", "engine.llm.model"], "setting_command", "reset"),
        (["config", "export"], "config_command", "export"),
        (["memory", "forget", "mem_1"], "memory_command", "forget"),
        (["db", "downgrade", "abc123"], "db_command", "downgrade"),
    ],
)
def test_symmetric_subcommands_parse(argv, attribute, value):
    args = cli.build_parser().parse_args(argv)
    assert getattr(args, attribute) == value


def test_setting_help_exits_zero(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--env", ".env", "setting", "help"]) == 0
    assert "usage: monoagens setting" in capsys.readouterr().out


def test_people_remove_refuses_last_administrator(tmp_path):
    roles = tmp_path / "roles.yaml"
    roles.write_text(
        "roles:\n  administrator:\n    users:\n      - per_aaaaaaaaaa\n",
        encoding="utf-8",
    )
    paths = SimpleNamespace(roles=roles)
    with pytest.raises(ValueError, match="last administrator"):
        admin_people.remove(None, paths, "per_aaaaaaaaaa", role="administrator")


def test_purge_refuses_deployment_root(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("engine: {}\n", encoding="utf-8")
    current = SimpleNamespace(
        state=tmp_path,
        paths=SimpleNamespace(config=config),
        mode="native",
        root=tmp_path,
    )
    with pytest.raises(ValueError, match="deployment root"):
        cli._purge(current)


def test_env_file_resolves_under_config_parent(tmp_path, monkeypatch):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    config = deploy / "config.yaml"
    config.write_text("engine: {}\n", encoding="utf-8")
    env = deploy / ".env"
    env.write_text("RUNBOOK_ENV_PROBE=ok\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.delenv("RUNBOOK_ENV_PROBE", raising=False)
    assert cli.main(["--config", str(config), "--env", str(env), "people"]) == 0
    assert os.environ["RUNBOOK_ENV_PROBE"] == "ok"
    assert Path.cwd() == elsewhere
