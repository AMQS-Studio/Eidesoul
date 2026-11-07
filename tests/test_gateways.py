"""Gateway extension contract: registration, the settings/policy hooks, and whatsapp's block."""


import importlib
from datetime import UTC, datetime

import pytest

from monoagens.engine.inbound import MESSAGE, Percept
from monoagens.engine.turn.context import Message
from monoagens.gateways import gateway_index, register_gateway, registered_gateways
from monoagens.gateways.base import BaseGateway, restore_gateways


@pytest.fixture(autouse=True)
def registered_fake_gateway():
    """Register the fake body only for this module, so discovery tests elsewhere stay clean."""
    snapshot = registered_gateways()
    register_gateway("fake_ops", extra="fake")(FakeGateway)
    try:
        yield
    finally:
        restore_gateways(snapshot)


def percept(**hints) -> Percept:
    return Percept(
        source="fake",
        thread="thread",
        actor="person",
        kind=MESSAGE,
        payload=Message(
            id="m1",
            sender_name="Person",
            sender_id="person",
            when=datetime(2026, 1, 1, tzinfo=UTC),
            text="hi",
        ),
        hints=hints,
    )


class FakeGateway(BaseGateway):
    """A body whose hooks are observable from a test."""

    name = "fake_ops"
    extra = "fake"

    @classmethod
    def default_settings(cls):
        return {**super().default_settings(), "channel": "ops"}

    @classmethod
    def default_role_policy(cls, role_name: str):
        return {"dm": role_name in {"administrator", "owner"}, "channel": "ops"}

    @classmethod
    def validate_role_policy(cls, block):
        super().validate_role_policy(block)
        if not isinstance(block.get("dm"), bool):
            raise ValueError("gateway 'fake_ops': dm must be a bool")

    def check_access(self, block, percept):
        if not block or block.get("dm") is not True:
            return False
        return bool(percept.hints.get("dm"))


def test_a_registered_gateway_is_discovered_with_its_defaults() -> None:
    info = gateway_index()["fake_ops"]

    assert info.source == "registered"
    assert info.extra == "fake"
    assert info.module == FakeGateway.__module__
    assert info.attribute == "FakeGateway"
    assert info.gateway_class is FakeGateway
    assert info.default_settings() == {**BaseGateway.default_settings(), "channel": "ops"}
    assert info.default_config()["channel"] == "ops"
    assert info.default_role_policy("administrator") == {"dm": True, "channel": "ops"}
    assert info.default_role_policy("guest") == {"dm": False, "channel": "ops"}


def test_the_registered_policy_hooks_are_honored() -> None:
    FakeGateway.validate_role_policy({"dm": True, "channel": "ops"})
    with pytest.raises(ValueError, match="dm must be a bool"):
        FakeGateway.validate_role_policy({"dm": "yes", "channel": "ops"})

    gateway = FakeGateway()
    allowed = {"dm": True, "channel": "ops"}
    assert gateway.check_access(allowed, percept(dm=True)) is True
    assert gateway.check_access(allowed, percept(group=True)) is False
    assert gateway.check_access(None, percept(dm=True)) is False


def test_a_duplicate_registration_under_another_class_is_refused() -> None:
    with pytest.raises(ValueError, match="already registered"):

        @register_gateway("fake_ops")
        class Other(BaseGateway):
            name = "fake_ops"


def test_base_defaults_declare_the_progress_flags_and_fail_closed() -> None:
    assert BaseGateway.default_settings() == {
        "enabled": True,
        "show_thinking": False,
        "show_steps": False,
        "interim_updates": False,
    }
    assert BaseGateway.default_config() == BaseGateway.default_settings()
    assert BaseGateway.default_role_policy("guest") == {}
    assert BaseGateway().check_access({}, percept(dm=True)) is False
    BaseGateway.validate_role_policy({})
    with pytest.raises(ValueError, match="mapping"):
        BaseGateway.validate_role_policy([])  # pyright: ignore[reportArgumentType]


def test_whatsapp_is_discovered_and_registered() -> None:
    pytest.importorskip("neonize")
    # The registry fixture restores a per-test snapshot, so re-run the decorator on the cached
    # module instead of assuming a previous test left it registered.
    module = importlib.reload(importlib.import_module("monoagens.gateways.whatsapp.gateway"))

    info = gateway_index()["whatsapp"]

    assert info.source == "builtin"
    assert info.attribute == "WhatsAppGateway"
    assert info.extra == "whatsapp"
    assert registered_gateways()["whatsapp"].gateway is module.WhatsAppGateway


def test_whatsapp_declares_its_own_settings_and_role_defaults() -> None:
    pytest.importorskip("neonize")
    from monoagens.gateways.whatsapp.gateway import WhatsAppGateway

    settings = WhatsAppGateway.default_settings()
    assert settings["session_db"] == ".monoagens/whatsapp.db"
    assert settings["backlog_seconds"] == 600.0
    for flag in ("show_thinking", "show_steps", "interim_updates"):
        assert settings[flag] is False
    assert WhatsAppGateway.default_role_policy("administrator") == {
        "dm": True,
        "groups": "all",
        "reply_to_bot": True,
    }
    assert WhatsAppGateway.default_role_policy("member")["groups"] == "mention"
    assert WhatsAppGateway.default_role_policy("guest") == {
        "dm": False,
        "groups": "none",
        "reply_to_bot": False,
    }


def test_whatsapp_policy_validation_rejects_a_bad_block() -> None:
    pytest.importorskip("neonize")
    from monoagens.gateways.whatsapp.gateway import WhatsAppGateway

    WhatsAppGateway.validate_role_policy({"dm": True, "groups": "mention", "reply_to_bot": True})
    with pytest.raises(ValueError, match="whatsapp"):
        WhatsAppGateway.validate_role_policy(
            {"dm": True, "groups": "everyone", "reply_to_bot": True}
        )
    with pytest.raises(ValueError, match="whatsapp"):
        WhatsAppGateway.validate_role_policy({"dm": "yes", "groups": "all", "reply_to_bot": True})


def test_whatsapp_check_access_answers_from_the_block_and_the_hints() -> None:
    pytest.importorskip("neonize")
    from monoagens.gateways.whatsapp.gateway import WhatsAppGateway

    gateway = object.__new__(WhatsAppGateway)
    block = {"dm": True, "groups": "mention", "reply_to_bot": True}

    assert gateway.check_access(block, percept(dm=True)) is True
    assert gateway.check_access(block, percept(group=True)) is False
    assert gateway.check_access(block, percept(group=True, mention=True)) is True
    assert gateway.check_access(None, percept(dm=True)) is False
