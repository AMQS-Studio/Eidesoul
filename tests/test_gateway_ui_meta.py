"""ADR-0010 §4.1: the interactive prompt is resolved at the body boundary, not in the engine.

`BaseGateway.ui_of` reads an explicit `meta["ui"]`/`meta["options"]` block, and otherwise turns
the engine's own `/approve #<id>` badge into the localized Approve/Deny pair. The engine keeps
`Action.payload` as the readable text and carries no UI vocabulary.
"""


import asyncio
from types import SimpleNamespace

import pytest
from support import FakeGateway, make_agent

from monoagens.engine.config import ApprovalConfig
from monoagens.engine.i18n import t
from monoagens.engine.inbound import Action, Address
from monoagens.gateways.base import BaseGateway
from monoagens.gateways.telegram.gateway import TelegramGateway


class Body(BaseGateway):
    """The smallest body that renders labels: a config with a reply language."""

    name = "body"

    def __init__(self, language: str = "en") -> None:
        self.config = SimpleNamespace(soul=SimpleNamespace(language=language))


def badge(payload: str, capability: str = "send_text") -> Action:
    return Action(
        address=Address("body", "chat"), capability=capability, payload=payload
    )


def test_the_engine_badge_becomes_the_localized_pair() -> None:
    action = badge('done\n\n🕓 Waiting: /approve 42 (or just reply "ok, send").')

    block = Body("id").ui_of(action)

    assert block["kind"] == "approval"
    assert block["approval_id"] == 42
    approve, deny = block["buttons"]
    assert approve == {
        "id": "approve:42",
        "label": t("approval.button_approve", "id"),
        "command": "/approve #42",
        "style": "primary",
    }
    assert deny == {
        "id": "deny:42",
        "label": t("approval.button_deny", "id"),
        "command": "/deny #42",
        "style": "danger",
    }


def test_a_hashed_badge_and_a_plain_badge_both_resolve() -> None:
    assert Body().approval_id(badge("/approve #7")) == 7
    assert Body().approval_id(badge("/approve 7")) == 7
    assert Body().approval_id(badge("/approve #7 extra")) == 7
    assert Body().approval_id(badge("no command here")) == 0


def test_only_a_text_action_carries_an_approval_badge() -> None:
    assert Body().approval_id(badge("/approve #7", capability="send_file")) == 0


def test_an_explicit_ui_block_wins() -> None:
    action = Action(
        address=Address("body", "chat"),
        capability="send_text",
        payload="Pick one",
        meta={
            "ui": {
                "kind": "choices",
                "title": "Models",
                "summary": "two are configured",
                "buttons": [
                    {"id": "m1", "label": "fast", "command": "/models fast"},
                    {"label": "slow", "command": "/models slow", "style": "secondary"},
                ],
            }
        },
    )

    block = Body().ui_of(action)

    assert block["kind"] == "choices"
    assert block["title"] == "Models"
    assert block["summary"] == "two are configured"
    assert [item["command"] for item in block["buttons"]] == [
        "/models fast",
        "/models slow",
    ]
    assert block["buttons"][1]["id"] == "/models slow"


def test_a_bare_options_list_is_understood() -> None:
    action = Action(
        address=Address("body", "chat"),
        capability="send_text",
        payload="Pick one",
        meta={"options": [{"label": "Retry", "command": "/retry"}]},
    )

    block = Body().ui_of(action)

    assert block["kind"] == "options"
    assert block["buttons"] == [
        {"id": "/retry", "label": "Retry", "command": "/retry", "style": ""}
    ]


def test_malformed_options_are_dropped() -> None:
    action = Action(
        address=Address("body", "chat"),
        capability="send_text",
        payload="Pick one",
        meta={
            "options": [
                {"label": "no command"},
                {"command": "/nameless"},
                "junk",
                {"label": "kept", "command": "/kept"},
            ]
        },
    )

    assert [item["command"] for item in Body().prompt_options(action)] == ["/kept"]


def test_an_action_without_a_prompt_has_no_block() -> None:
    body = Body()

    assert body.ui_of(None) == {}
    assert body.ui_of(badge("plain text")) == {}
    assert body.prompt_options(badge("plain text")) == []


def test_the_default_language_is_english() -> None:
    assert BaseGateway().language() == "en"


def test_the_engine_keeps_the_payload_and_adds_nothing(tmp_path) -> None:
    """`send_text` still carries the readable text; the block is the body's own reading."""
    env = make_agent(tmp_path)

    asyncio.run(
        env.agent.send_text(
            "chat",
            "halo",
            gateway=env.gateway,
            origin=Address(env.gateway.name, "chat"),
        )
    )

    action = env.gateway.dispatched[-1]
    assert action.payload == "halo"
    assert action.meta == {}


def test_a_badge_survives_the_engine_turn_path(tmp_path) -> None:
    """The badge the engine appends is what the body later reads as buttons."""
    from monoagens.engine.turn.approval_flow import ApprovalFlow

    env = make_agent(tmp_path)
    row = asyncio.run(
        env.agent.approvals.create(
            kind="tool",
            thread_id="chat",
            sender="628111",
            tool="shell",
            args={},
            payload={},
            summary="rm -rf scratch",
            approval_class="authorization",
        )
    )
    class BadgeHost(ApprovalFlow):
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(  # pyright: ignore[reportAttributeAccessIssue]
                approvals=env.agent.approvals,
                config=SimpleNamespace(approval=ApprovalConfig(natural_confirm=True)),
                soul=SimpleNamespace(language="en"),
            )

    host = BadgeHost()

    text = asyncio.run(host._approval_badge("done.", "chat", "628111", 0.0))

    assert isinstance(text, str)
    assert f"/approve {row.id}" in text
    block = Body().ui_of(badge(text))
    assert block["approval_id"] == row.id
    assert block["buttons"][0]["command"] == f"/approve #{row.id}"


class TelegramBody(TelegramGateway):
    """The real body's prompt resolution without its transport or its `__init__`."""

    def __init__(self) -> None:
        self.config = SimpleNamespace(soul=SimpleNamespace(language="en"))


def test_the_telegram_body_renders_the_badge_as_a_keyboard() -> None:
    markup = TelegramBody().ui_of(badge("waiting: /approve 9"))

    assert markup["kind"] == "approval"
    assert [item["command"] for item in markup["buttons"]] == [
        "/approve #9",
        "/deny #9",
    ]


def test_a_body_without_a_config_still_reads_an_explicit_block() -> None:
    class Bare(BaseGateway):
        name = "bare"

    action = Action(
        address=Address("bare", "t"),
        capability="send_text",
        payload="x",
        meta={"options": [{"label": "ok", "command": "/ok"}]},
    )

    assert [item["label"] for item in Bare().prompt_options(action)] == ["ok"]


def test_a_plain_fake_gateway_keeps_its_text() -> None:
    """A core-only body inherits nothing that rewrites its payload."""
    gateway = FakeGateway()
    assert not hasattr(gateway, "ui_of")


def test_an_unknown_language_falls_back_to_the_catalog_default() -> None:
    block = Body("xx").ui_of(badge("/approve #1"))
    assert block["buttons"][0]["label"] == t("approval.button_approve", "en")


@pytest.mark.parametrize("command", ["/approve #12", "/approve 12", "/approve   12"])
def test_the_badge_pattern_tolerates_spacing(command: str) -> None:
    assert Body().approval_id(badge(f"waiting {command}")) == 12
