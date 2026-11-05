"""Combined config contract: one config.yaml plus roles.yaml and soul.yaml; files are authority."""


import os

import pytest
import yaml
from support import database

from monoagens.engine.config import (
    DEFAULT_SENSITIVE_COMMANDS,
    ConfigError,
    load_config,
    load_env,
    load_host,
)
from monoagens.engine.stores.documents import DocumentStore

BASE = {
    "database": {
        "url": "postgresql+psycopg://monoagens:monoagens@127.0.0.1:5433/monoagens_test",
        "url_env": "",
    },
    "workspace": ".monoagens/workspace",
    "roles_file": "roles.yaml",
    "engine": {
        "llm": {
            "provider": "custom",
            "base_url": "http://x",
            "api_key_env": "MONOAGENS_TEST_KEY",
            "model": "seed-model",
        },
        "default_role": "guest",
        "memory": {
            "privacy": {
                "enabled": True,
                "default_level": 2,
                "ask_level": 3,
                "group_max_level": 1,
                "dm_max_level": 4,
                "auditor": False,
            }
        },
    },
    "soul_file": "soul.yaml",
    "gateways": {"cli": {"enabled": True, "thread_id": "cli:local"}},
}

SOUL = "name: MonoAgens\nlanguage: en\nlocale: en-US\ntimezone: UTC\npronouns: aku\n"

ROLES = {
    "roles": {
        "guest": {"name": "guest", "tools": ["read"], "commands": ["help"]},
        "administrator": {
            "name": "administrator",
            "tools": ["*"],
            "commands": ["*"],
            "memory_admin": True,
        },
    }
}


def write_folder(tmp_path, *, config=None, roles=None, soul=SOUL) -> None:
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(config if config is not None else BASE, sort_keys=False), encoding="utf-8"
    )
    if soul is not None:
        (tmp_path / "soul.yaml").write_text(soul, encoding="utf-8")
    if roles is not False:
        (tmp_path / "roles.yaml").write_text(
            yaml.safe_dump(roles if roles is not None else ROLES, sort_keys=False), encoding="utf-8"
        )


def test_load_config_reads_all_sections(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MONOAGENS_TEST_KEY", "x")
    write_folder(tmp_path)

    host, paths = load_host(tmp_path / "config.yaml")
    config = load_config(paths.config, require_key=False)

    assert host.roles_file == tmp_path / "roles.yaml"
    assert paths.roles == tmp_path / "roles.yaml"
    assert paths.soul_yaml == tmp_path / "soul.yaml"
    assert config.llm.model == "seed-model"
    assert config.default_role == "guest"
    assert config.soul.language == "en"
    assert config.gateways["cli"].enabled is True
    assert config.roles.get("administrator").memory_admin is True
    assert config.memory.privacy.group_max_level == 1
    assert config.memory.privacy.auditor is False


def test_files_win_over_a_changed_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MONOAGENS_TEST_KEY", "x")
    write_folder(tmp_path)
    docs = DocumentStore(database(), load_host(tmp_path / "config.yaml")[1])
    docs.put(
        "engine",
        "llm:\n  provider: custom\n  base_url: http://doc\n"
        "  model: doc-model\n  api_key_env: MONOAGENS_TEST_KEY\ndefault_role: administrator\n",
    )
    docs.put("soul", "name: monoagens\nlanguage: id\nlocale: id-ID\ntimezone: UTC\npronouns: aku\n")
    docs.put(
        "role:administrator",
        "name: administrator\ntools: ['*']\ncommands: ['*']\nmemory_admin: true\n",
    )
    docs.put("role:guest", "name: guest\ntools: []\n")

    config = load_config(tmp_path / "config.yaml", require_key=False)

    assert config.llm.model == "seed-model"
    assert config.soul.language == "en"
    assert config.default_role == "guest"
    assert config.roles.names() == ["administrator", "guest"]
    assert config.roles.get("guest").tools == ("read",)
    assert config.roles.get("administrator").memory_admin is True


def test_editing_roles_yaml_changes_access_without_an_import(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MONOAGENS_TEST_KEY", "x")
    roles = yaml.safe_load(yaml.safe_dump(ROLES))
    roles["roles"]["guest"]["tools"] = ["grep"]
    write_folder(tmp_path, roles=roles)

    config = load_config(tmp_path / "config.yaml", require_key=False)

    assert config.roles.get("guest").tools == ("grep",)


def test_roles_file_needs_the_wrapper(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MONOAGENS_TEST_KEY", "x")
    write_folder(tmp_path, roles={"guest": {"name": "guest"}})
    with pytest.raises(ConfigError, match="roles"):
        load_config(tmp_path / "config.yaml", require_key=False)


def test_missing_roles_file_is_an_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MONOAGENS_TEST_KEY", "x")
    write_folder(tmp_path, roles=False)
    with pytest.raises(ConfigError, match="no roles"):
        load_config(tmp_path / "config.yaml", require_key=False)


def test_privacy_levels_clamp_invalid_values(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MONOAGENS_TEST_KEY", "x")
    config = yaml.safe_load(yaml.safe_dump(BASE))
    config["engine"]["memory"]["privacy"] = {
        "enabled": "false",
        "default_level": 9,
        "ask_level": -1,
        "group_max_level": 2,
        "dm_max_level": 4,
        "auditor": True,
    }
    write_folder(tmp_path, config=config)

    loaded = load_config(tmp_path / "config.yaml", require_key=False)

    assert loaded.memory.privacy.enabled is False
    assert loaded.memory.privacy.default_level == 2
    assert loaded.memory.privacy.ask_level == 4


def test_approval_defaults_to_command_patterns(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MONOAGENS_TEST_KEY", "x")
    write_folder(tmp_path)

    loaded = load_config(tmp_path / "config.yaml", require_key=False)

    assert loaded.approval.sensitive_tools == ()
    assert loaded.approval.sensitive_commands == DEFAULT_SENSITIVE_COMMANDS


def test_approval_sensitive_commands_override_and_disable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MONOAGENS_TEST_KEY", "x")
    for value, expected in (([], ()), (["rm *"], ("rm *",))):
        config = yaml.safe_load(yaml.safe_dump(BASE))
        config["engine"]["approval"] = {"sensitive_commands": value}
        write_folder(tmp_path, config=config)

        loaded = load_config(tmp_path / "config.yaml", require_key=False)

        assert loaded.approval.sensitive_tools == ()
        assert loaded.approval.sensitive_commands == expected


def test_load_env_rejects_paths_outside_the_working_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MONOAGENS_ENV_TEST", raising=False)
    (tmp_path / ".env").write_text("MONOAGENS_ENV_TEST=ok\n", encoding="utf-8")

    load_env()
    assert os.environ["MONOAGENS_ENV_TEST"] == "ok"

    for bad in ("../.env", "/etc/passwd", "sub/.env"):
        with pytest.raises(ConfigError):
            load_env(bad)
