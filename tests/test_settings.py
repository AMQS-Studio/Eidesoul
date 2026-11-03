"""Settings contract: read/change documents, validation, mirror, audit, approval, reload."""


import asyncio

import yaml
from sqlalchemy import select
from support import make_runtime

from eidesoul.engine.schema import config_changes
from eidesoul.engine.tools import execute

CHAT = "cli:local"


def run(env, action: str, **args) -> str:
    ctx = env.agent.tool_context(
        CHAT, "local", (env.config.roles.default_role(),), env.gateway
    )
    return asyncio.run(
        execute(env.agent.tools.by_name, "settings", {"action": action, **args}, ctx)
    )


def test_schema_and_get(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)

    schema = run(env, "schema")
    assert "engine.llm.model" in schema
    assert "engine.memory.max_items" in schema

    assert run(env, "get", file="engine", path="default_role").strip() == "member"
    assert "Kamu Eidesoul" in run(env, "get", file="soul")


def test_set_soul_language_updates_runtime_and_mirror(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    assert env.agent.soul.language == "id"

    result = run(env, "set", file="soul", path="language", value="en")

    assert result.startswith("ok:")
    assert env.agent.soul.language == "en"
    mirror = yaml.safe_load(env.paths.soul_yaml.read_text(encoding="utf-8"))
    assert mirror["language"] == "en"
    assert env.agent.soul.language == "en"


def test_invalid_engine_value_does_not_change_document(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    before = env.documents.get("engine")

    result = run(env, "set", file="engine", path="approval.mode", value="bogus")

    assert result.startswith("error:")
    assert env.documents.get("engine") == before
    assert env.agent.config.approval.mode == "yolo"


def test_write_soul_changes_prompt_and_mirror(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    content = "name: Eidesoul\npersona: Persona baru yang tenang.\n"

    result = run(env, "write", file="soul", content=content)

    assert result.startswith("ok:")
    assert env.paths.soul_yaml.read_text(encoding="utf-8") == content
    assert "Persona baru yang tenang." in env.agent.soul.persona


def test_write_role_changes_permissions(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)

    content = 'name: member\ntools: ["read"]\ncommands: ["ping"]\n'
    result = run(env, "write", file="role", role="member", content=content)

    assert result.startswith("ok:")
    assert env.agent.config.roles.default_role().tools == ("read",)
    mirror = yaml.safe_load(env.paths.roles.read_text(encoding="utf-8"))
    assert mirror["roles"]["member"] == yaml.safe_load(content)


def test_sensitive_key_rejected(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    result = run(env, "set", file="engine", path="llm.api_key", value="secret")
    assert "sensitive" in result


def test_settings_change_waits_for_approval(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch)
    before = env.documents.get("soul")

    pending = run(env, "set", file="soul", path="language", value="en")

    assert "pending approval #1" in pending
    assert env.documents.get("soul") == before
    assert env.agent.soul.language == "id"

    summary = asyncio.run(env.agent.apply_settings_payload(
        env.agent.approvals.get(1).payload
    ))
    assert env.agent.soul.language == "en"
    assert "reloaded" in summary
    rows = env.db.fetch_all(select(config_changes))
    assert rows
    assert rows[-1]["file"] == "soul"


def test_restart_only_key_reports_restart(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    before = env.agent.config.workspace

    result = run(env, "set", file="host", path="workspace", value="/tmp/lain")

    assert "restart required" in result
    assert env.agent.config.workspace == before
    assert "workspace" in result


def test_gateway_edit_updates_gateways_mapping(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)

    result = run(env, "set", file="gateway", path="cli.chat_name", value="Terminal 2")

    assert result.startswith("ok:")
    raw = yaml.safe_load(env.paths.config.read_text(encoding="utf-8"))
    assert raw["gateways"]["cli"]["chat_name"] == "Terminal 2"
    assert "engine" in raw
    assert "soul_file" in raw
    assert "soul" not in raw


def test_host_rejects_reserved_sections(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)

    result = run(env, "set", file="host", path="engine.llm.model", value="x")

    assert "use file=engine" in result
    assert not env.paths.config.read_text(encoding="utf-8").count("model: x")


def test_registry_has_settings_tool(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch)
    assert "settings" in env.agent.tools.names()
