"""Tool contracts: argument validation (ADR-0024) and per-tool output caps (ADR-0021)."""


import asyncio

import pytest
from support import build_config, role

from eidesoul.engine.config import ConfigError, ShellConfig, _shell_config
from eidesoul.engine.registry import Handler, validate
from eidesoul.engine.tools import (
    FAMILIES,
    ToolContext,
    argument_errors,
    execute,
    truncate,
)
from eidesoul.engine.tools.shell import child_env

SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 2},
        "count": {"type": "integer", "minimum": 1, "maximum": 10},
        "mode": {"type": "string", "enum": ["a", "b"]},
    },
    "required": ["query"],
    "additionalProperties": False,
}


def handler(**kwargs) -> Handler:
    async def fn(args, ctx):
        return "ok"

    body = {
        "name": "probe",
        "description": "probe",
        "fn": fn,
        "family": "read",
        "parameters": SCHEMA,
    }
    body.update(kwargs)
    return Handler(**body)


def test_wrong_type_and_foreign_key_are_rejected_before_the_handler(tmp_path) -> None:
    called: list[str] = []

    async def spy(args, ctx):
        called.append("ran")
        return "ran"

    spec = handler(fn=spy)
    config = build_config(tmp_path)
    ctx = ToolContext(config, (role("member"),), ("*",))
    result = asyncio.run(execute({"probe": spec}, "probe", {"query": 1}, ctx))

    assert result.startswith("error: invalid arguments for probe:")
    assert "query must be string" in result
    assert called == []
    assert ctx.used == []


def test_foreign_key_rejected_and_additional_properties_can_open_it() -> None:
    assert "unexpected field extra" in argument_errors(SCHEMA, {"query": "ok", "extra": 1})[0]
    open_schema = {**SCHEMA, "additionalProperties": True}
    assert argument_errors(open_schema, {"query": "ok", "extra": 1}) == []


def test_required_enum_and_bounds_are_named() -> None:
    errors = argument_errors(SCHEMA, {"count": 99, "mode": "z"})
    assert any("query is required" in item for item in errors)
    assert any("count must be <= 10" in item for item in errors)
    assert any("mode must be one of" in item for item in errors)


def test_unsupported_keyword_fails_validation() -> None:
    spec = handler(
        parameters={
            "type": "object",
            "properties": {"q": {"type": "string", "format": "email"}},
        }
    )

    errors = validate(spec, "probe", "tool", FAMILIES)

    assert any("unsupported keyword" in item and "format" in item for item in errors)


def test_synchronous_handler_fails_validation() -> None:
    def sync(args, ctx):
        return "no"

    spec = Handler(name="probe", description="p", fn=sync, family="read", parameters=SCHEMA)

    errors = validate(spec, "probe", "tool", FAMILIES)

    assert any("fn must be async" in item for item in errors)


def test_truncate_always_carries_a_marker_and_a_next_hint() -> None:
    body = truncate("x" * 500, 200)

    assert len(body) <= 200
    assert "truncated" in body
    assert "next:" in body


def test_per_tool_cap_is_used_not_a_global_one(tmp_path) -> None:
    async def long(args, ctx):
        return "y" * 500

    spec = handler(fn=long, max_output_chars=200)
    config = build_config(tmp_path)
    ctx = ToolContext(config, (role("member"),), ("*",))

    result = asyncio.run(execute({"probe": spec}, "probe", {"query": "ok"}, ctx))

    assert len(result) <= 200
    assert "next:" in result


def test_shell_env_is_a_whitelist(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("PATH", "/bin")

    env = child_env(ShellConfig(env=("PATH",)))

    assert env == {"PATH": "/bin"}
    assert "OPENAI_API_KEY" not in env


def test_shell_env_wildcard_fails_boot() -> None:
    with pytest.raises(ConfigError):
        _shell_config({"shell": {"env": ["*"]}})
