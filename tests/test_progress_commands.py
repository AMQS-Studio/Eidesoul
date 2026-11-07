"""ADR-0010 §4.1: `/thinking` and `/steps`, the deterministic stream id, and the debouncer."""


import asyncio
import time

import pytest
import yaml
from support import (
    FakeGateway,
    RichDirectoryFakeGateway,
    inbound_message,
    make_agent,
    role,
)

from monoagens.engine.access.roles import Role
from monoagens.engine.i18n import t
from monoagens.engine.turn.progress import _progress_callbacks, stream_id
from monoagens.gateways.base import BaseGateway, EditDebouncer


class ProgressFakeGateway(FakeGateway, BaseGateway):
    """A fake body with the shared progress-flag surface every real body inherits."""

    name = "fake"


def admin_env(tmp_path, gateway=None):
    administrator = Role(
        "administrator", tools=("*",), commands=("*",), protected=True, priority=100
    )
    return make_agent(
        tmp_path,
        gateway=gateway or ProgressFakeGateway(),
        roles={"administrator": administrator},
        default_role="administrator",
    )


class ProgressRichFakeGateway(RichDirectoryFakeGateway, BaseGateway):
    """The rich fake plus the shared progress-flag surface."""

    name = "fake"


def member_env(tmp_path):
    return make_agent(
        tmp_path,
        gateway=ProgressRichFakeGateway(),
        roles={"member": role("member", tools=("read",), commands=("*",))},
        default_role="member",
    )


def sent_text(env) -> str:
    return env.gateway.sent[-1][1]


def test_thinking_without_arguments_reports_the_current_state(tmp_path) -> None:
    env = admin_env(tmp_path)

    asyncio.run(env.agent.handle(inbound_message("/thinking"), env.gateway))

    text = sent_text(env)
    assert "fake" in text
    assert t("common.off", env.config.soul.language) in text


def test_thinking_on_persists_to_config_and_applies_live(tmp_path) -> None:
    env = admin_env(tmp_path)

    asyncio.run(env.agent.handle(inbound_message("/thinking on"), env.gateway))

    assert env.gateway.progress_enabled("show_thinking") is True
    body = yaml.safe_load(env.config.paths.config.read_text(encoding="utf-8"))
    assert body["gateways"]["fake"]["show_thinking"] is True
    assert t("common.on", env.config.soul.language) in sent_text(env)


def test_steps_off_persists_and_turns_the_stream_off(tmp_path) -> None:
    env = admin_env(tmp_path)

    asyncio.run(env.agent.handle(inbound_message("/steps off"), env.gateway))

    assert env.gateway.progress_enabled("show_steps") is False
    body = yaml.safe_load(env.config.paths.config.read_text(encoding="utf-8"))
    assert body["gateways"]["fake"]["show_steps"] is False


def test_a_bad_argument_reports_the_usage_and_changes_nothing(tmp_path) -> None:
    env = admin_env(tmp_path)

    asyncio.run(env.agent.handle(inbound_message("/steps maybe"), env.gateway))

    assert not env.config.paths.config.exists()
    assert "on" in sent_text(env)


def test_a_member_cannot_switch_the_stream(tmp_path) -> None:
    env = member_env(tmp_path)

    asyncio.run(env.agent.handle(inbound_message("/thinking on"), env.gateway))

    assert env.gateway.progress_enabled("show_thinking") is False
    assert not env.config.paths.config.exists()
    assert sent_text(env) == t("command.thinking.denied", env.config.soul.language)


def test_an_operator_setting_is_the_fallback_until_a_command_overrides_it() -> None:
    class Body(BaseGateway):
        name = "body"

    body = Body()
    body.settings = {"show_steps": True, "show_thinking": False}
    assert body.progress_enabled("show_steps") is True
    assert body.progress_enabled("show_thinking") is False

    body.set_progress("show_thinking", True)
    body.set_progress("show_steps", False)

    assert body.progress_enabled("show_thinking") is True
    assert body.progress_enabled("show_steps") is False
    with pytest.raises(ValueError, match="unknown progress flag"):
        body.set_progress("show_everything", True)


def test_progress_actions_share_one_stream_and_keep_unique_ids() -> None:
    class Body(BaseGateway):
        name = "body"

    body = Body()
    body.settings = {"show_thinking": True, "show_steps": True, "interim_updates": True}
    seen = []

    async def dispatch(subject, address, action, **kwargs):
        seen.append(action)

    callbacks = _progress_callbacks(dispatch, body, "chat", "turn-1")
    asyncio.run(callbacks["send_thinking"]("hmm"))
    asyncio.run(callbacks["send_step"]({"tool": "read", "status": "executed"}))
    asyncio.run(callbacks["send_interim"]("still working"))

    assert stream_id("turn-1") == "turn:turn-1:progress"
    assert {action.stream_id for action in seen} == {"turn:turn-1:progress"}
    assert {action.replace_id for action in seen} == {"turn:turn-1:progress"}
    assert len({action.id for action in seen}) == 3


def test_a_disabled_stream_is_not_registered() -> None:
    class Body(BaseGateway):
        name = "body"

    body = Body()
    body.settings = {"show_thinking": False, "show_steps": False, "interim_updates": False}

    assert _progress_callbacks(None, body, "chat", "t") == {}


def test_the_debouncer_applies_now_then_coalesces_the_latest() -> None:
    async def scenario() -> list[str]:
        calls: list[str] = []
        debouncer = EditDebouncer(0.05)

        async def first() -> None:
            calls.append("first")

        async def second() -> None:
            calls.append("second")

        async def third() -> None:
            calls.append("third")

        assert await debouncer.submit("chat", first) is True
        assert await debouncer.submit("chat", second) is False
        assert await debouncer.submit("chat", third) is False
        await asyncio.sleep(0.15)
        return calls

    assert asyncio.run(scenario()) == ["first", "third"]


def test_the_debouncer_forgets_a_thread() -> None:
    async def scenario() -> list[str]:
        calls: list[str] = []
        debouncer = EditDebouncer(0.05)

        async def apply() -> None:
            calls.append("applied")

        assert await debouncer.submit("chat", apply) is True
        debouncer.forget("chat")
        assert debouncer.due("chat") is True
        await debouncer.flush()
        return calls

    assert asyncio.run(scenario()) == ["applied"]


def test_the_debouncer_reports_due_by_its_clock() -> None:
    now = {"value": 0.0}
    debouncer = EditDebouncer(1.5, clock=lambda: now["value"])
    assert debouncer.due("chat") is True
    debouncer.mark("chat")
    now["value"] = 1.0
    assert debouncer.due("chat") is False
    now["value"] = 1.5
    assert debouncer.due("chat") is True


def test_the_gateway_owns_one_debouncer() -> None:
    class Body(BaseGateway):
        name = "body"

    body = Body()
    assert body.edit_debouncer() is body.edit_debouncer()
    assert body.edit_debouncer().window == pytest.approx(1.5)
    assert time.monotonic() > 0
