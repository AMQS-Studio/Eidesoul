"""Gateway contract: three protocols, the boot check, degradation, and core isolation."""


import asyncio
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from support import (
    DirectoryFakeGateway,
    FakeGateway,
    RichDirectoryFakeGateway,
    RichFakeGateway,
    build_config,
    database,
    inbound_message,
    make_agent,
    role,
    specs,
)

from monoagens.engine.access.roles import Role
from monoagens.engine.config import ConfigError, GatewaySettings
from monoagens.engine.inbound import (
    Address,
    Directory,
    GatewayCore,
    Rich,
    capability_report,
    ensure_core,
    refusal,
)
from monoagens.engine.registry import Handler, RegistryError
from monoagens.engine.stores.history import HistoryStore
from monoagens.engine.turn.context import WorkingMemory


def handler(name: str) -> Handler:
    async def fn(args, ctx):
        return "ok"

    return Handler(
        name=name,
        description="test",
        fn=fn,
        family="read",
        parameters={"type": "object", "properties": {}},
    )


def call_tool(env, name: str, args: dict, *, thread: str = "chat"):
    ctx = env.agent.tool_context(
        thread, "628111", (env.config.roles.get("owner"),), env.gateway
    )
    from monoagens.engine.tools import execute

    return asyncio.run(execute(specs(env), name, args, ctx))


def test_gateway_tools_are_indexed_per_body(tmp_path) -> None:
    """ADR-0032 §4.6: the same name in two bodies is not a collision, it is two tools."""
    first = FakeGateway(name="a", tools=[handler("probe_tool")])
    second = FakeGateway(name="b", tools=[handler("probe_tool")])
    env = make_agent(tmp_path, gateways=[first, second])

    index = env.agent.tool_index
    assert set(index.specs("a")) >= {"probe_tool"}
    assert set(index.specs("b")) >= {"probe_tool"}
    assert index.specs("a")["probe_tool"] is not index.specs("b")["probe_tool"]
    assert index.resolve("b__probe_tool", "a")[0] == "probe_tool"
    assert env.agent.tools.get("probe_tool") is None
    assert index.other_bodies("a") == ("b",)


def test_a_duplicate_inside_one_body_still_raises(tmp_path) -> None:
    gateway = FakeGateway(name="a", tools=[handler("clash"), handler("clash")])
    with pytest.raises(RegistryError):
        make_agent(tmp_path, gateway=gateway)


def test_core_only_gateway_runs_a_full_text_turn(tmp_path) -> None:
    gateway = FakeGateway()
    env = make_agent(tmp_path, gateway=gateway)

    actions = asyncio.run(env.agent.handle(inbound_message("halo"), gateway))

    assert gateway.sent
    assert gateway.sent[0][1]
    assert [action.capability for action in actions] == ["send_text"]
    assert actions[0].address.thread == "chat"
    assert actions[0].approval_class == "consent"
    assert not hasattr(gateway, "typing")


def test_without_rich_typing_is_skipped_and_media_falls_back_to_text(tmp_path) -> None:
    gateway = FakeGateway()
    env = make_agent(tmp_path, gateway=gateway)
    assert not isinstance(gateway, Rich)
    (env.config.workspace / "public").mkdir(parents=True, exist_ok=True)
    note = env.config.workspace / "public" / "catatan.txt"
    note.write_text("isi laporan", encoding="utf-8")

    async def turn() -> str:
        await env.agent.handle(inbound_message("halo"), gateway)
        return await env.agent.send_file(
            "chat",
            note,
            "lampiran",
            gateway=gateway,
            origin=Address(gateway.name, "chat"),
        )

    result = asyncio.run(turn())

    assert result == "ok: file sent: catatan.txt"
    assert not hasattr(gateway, "files")
    fallback = gateway.sent[-1][1]
    assert "catatan.txt" in fallback
    assert "isi laporan" in fallback


def test_with_rich_a_file_is_a_file_and_typing_is_real(tmp_path) -> None:
    gateway = RichFakeGateway()
    env = make_agent(tmp_path, gateway=gateway)
    assert isinstance(gateway, Rich)
    assert not isinstance(gateway, Directory)
    (env.config.workspace / "public").mkdir(parents=True, exist_ok=True)
    note = env.config.workspace / "public" / "catatan.txt"
    note.write_text("isi laporan", encoding="utf-8")

    asyncio.run(env.agent.handle(inbound_message("halo"), gateway))
    asyncio.run(
        env.agent.send_file(
            "chat", note, gateway=gateway, origin=Address(gateway.name, "chat")
        )
    )

    assert gateway.files
    assert gateway.files[0][1].name == "catatan.txt"
    assert ("chat", True) in gateway.typing_events
    assert gateway.typing_events[-1] == ("chat", False)


def test_without_directory_a_cross_thread_read_is_refused(tmp_path) -> None:
    from monoagens.engine.tools.thread_history import TOOL as THREAD_HISTORY

    gateway = FakeGateway(tools=[THREAD_HISTORY])
    env = make_agent(
        tmp_path,
        gateway=gateway,
        roles={"owner": role("owner", tools=("thread_history",), commands=("*",))},
        default_role="owner",
    )

    result = call_tool(env, "thread_history", {"chat": "budi"}, thread="111@g.us")

    assert result.startswith("error:")
    assert "no directory" in result or "not in the archive" in result


def test_with_directory_membership_is_evidence_not_a_guess() -> None:
    unknown = DirectoryFakeGateway(members=None)
    known = DirectoryFakeGateway(members={"per_budi0001"})
    absent = DirectoryFakeGateway(members={"per_other0001"})

    assert "no directory" not in refusal(known, "111@g.us", "per_budi0001")
    assert refusal(known, "111@g.us", "per_budi0001") == ""
    assert "cannot prove" in refusal(unknown, "111@g.us", "per_budi0001")
    assert "not a participant" in refusal(absent, "111@g.us", "per_budi0001")
    assert "no directory" in refusal(FakeGateway(), "111@g.us", "per_budi0001")
    assert refusal(known, "111@g.us", "") == ""


def test_a_body_without_core_fails_at_boot_naming_the_method() -> None:
    class Broken:
        name = "broken"

        def tools(self):
            return []

        def commands(self):
            return []

        async def run(self, agent):
            return None

        async def stop(self):
            return None

        async def dispatch(self, action):
            return "x"

        def prompt_section(self):
            return ""

    with pytest.raises(ConfigError, match="self_handles"):
        ensure_core(Broken())


def test_build_gateways_logs_capabilities_and_refuses_a_broken_body(
    tmp_path, monkeypatch, caplog
) -> None:
    from monoagens import gateways as gateway_package

    class Buildable(RichDirectoryFakeGateway):
        def __init__(self, config, db, history, windows, *, settings=None):
            super().__init__(name=settings.name if settings else "buildable")

    class Incomplete:
        name = "incomplete"

        def __init__(self, config, db, history, windows, *, settings=None):
            self.name = settings.name if settings else "incomplete"

        def tools(self):
            return []

        def commands(self):
            return []

        async def run(self, agent):
            return None

        async def stop(self):
            return None

        async def dispatch(self, action):
            return "x"

        def prompt_section(self):
            return ""

    config = replace(
        build_config(tmp_path), gateways={"boot": GatewaySettings("boot", True)}
    )
    db = database()
    history = HistoryStore(db)
    windows = WorkingMemory(store=history)

    monkeypatch.setattr(gateway_package, "load_gateway", lambda name: Buildable)
    caplog.set_level("INFO")
    found = gateway_package.build_gateways(config, db=db, history=history, windows=windows)

    assert [item.name for item in found] == ["boot"]
    assert any("gateway_capabilities" in record.getMessage() for record in caplog.records)
    report = capability_report(found)
    assert report["boot"] == {
        "rich": True,
        "directory": True,
        "private_thread": True,
        "abort": False,
    }

    monkeypatch.setattr(gateway_package, "load_gateway", lambda name: Incomplete)
    with pytest.raises(ConfigError, match="self_handles"):
        gateway_package.build_gateways(config, db=db, history=history, windows=windows)


def test_capability_report_marks_the_optional_surfaces() -> None:
    report = capability_report(
        [
            FakeGateway(name="core"),
            RichFakeGateway(name="rich"),
            DirectoryFakeGateway(name="directory", members=None),
        ]
    )
    assert report == {
        "core": {"rich": False, "directory": False, "private_thread": True, "abort": False},
        "rich": {"rich": True, "directory": False, "private_thread": True, "abort": False},
        "directory": {
            "rich": False,
            "directory": True,
            "private_thread": True,
            "abort": False,
        },
    }


def test_the_archive_tool_is_core_and_the_body_keeps_compatibility_aliases(tmp_path) -> None:
    from test_whatsapp_gateway import make_gateway

    from monoagens.engine.tools import REGISTRY as CORE

    gateway = make_gateway(tmp_path)
    assert "thread_history" in CORE.names()
    assert gateway.tool_aliases()["whatsapp_chat_history"] == "thread_history"
    root = Path(__file__).resolve().parents[1]
    assert not (root / "monoagens" / "gateways" / "whatsapp" / "tools" / "chat_history.py").exists()


def test_core_imports_without_neonize() -> None:
    root = Path(__file__).resolve().parents[1]
    code = "import sys; sys.modules['neonize']=None; import monoagens.engine; print('ok')"
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_core_gateway_meets_only_core() -> None:
    assert isinstance(FakeGateway(), GatewayCore)
    assert not isinstance(FakeGateway(), Rich)
    assert not isinstance(FakeGateway(), Directory)


class RefusingGateway(FakeGateway):
    """A body whose policy says no; the refusal reply must come from the core."""

    def access_refusal(self, inbound) -> str:
        return "denied"


def test_a_refused_percept_gets_the_localized_refusal_from_core(tmp_path) -> None:
    from monoagens.engine.i18n import t

    gateway = RefusingGateway(name="fake")
    env = make_agent(tmp_path, gateway=gateway)
    inbound = inbound_message("halo", message_id="refused-1")

    actions = asyncio.run(env.agent.handle(inbound, gateway))

    assert actions == []
    assert env.agent.turns.get("refused-1") is None
    assert gateway.sent == [
        ("chat", t("agent.access_refused", env.config.soul.language), None)
    ]


def test_a_permitted_command_bypasses_the_gateway_refusal(tmp_path) -> None:
    from monoagens.engine.i18n import t

    gateway = RefusingGateway(name="fake")
    env = make_agent(
        tmp_path,
        gateway=gateway,
        roles={"member": Role("member", tools=("read",), commands=("*",))},
    )
    inbound = inbound_message("/whoami", message_id="command-1")

    asyncio.run(env.agent.handle(inbound, gateway))

    assert gateway.sent
    assert "Identitas" in gateway.sent[-1][1]
    assert all(
        text != t("agent.access_refused", env.config.soul.language)
        for _, text, _ in gateway.sent
    )



def test_every_shipped_body_meets_the_core_contract_and_resolves_prompts(tmp_path) -> None:
    """ADR-0010 §4.1-§4.5: all four bodies share one core surface and one prompt boundary."""
    from test_discord_gateway import FakeTransport as FakeDiscord
    from test_slack_gateway import FakeTransport as FakeSlack
    from test_telegram_gateway import FakeBot as FakeTelegram
    from test_whatsapp_gateway import make_gateway

    from monoagens.engine.inbound import Action, Address, GatewayCore, missing_core
    from monoagens.gateways.base import PROGRESS_FLAGS, BaseGateway
    from monoagens.gateways.discord.gateway import DiscordGateway
    from monoagens.gateways.slack.gateway import SlackGateway
    from monoagens.gateways.telegram.gateway import TelegramGateway

    config = build_config(tmp_path)
    db = database()
    history = HistoryStore(db)
    windows = WorkingMemory(store=history)
    config.workspace.mkdir(parents=True, exist_ok=True)
    bodies = [
        make_gateway(tmp_path),
        TelegramGateway(
            config,
            db,
            history,
            windows,
            settings=GatewaySettings("telegram", True, {}),
            transport=FakeTelegram(),
        ),
        DiscordGateway(
            config,
            db,
            history,
            windows,
            settings=GatewaySettings("discord", True, {}),
            transport=FakeDiscord(),
        ),
        SlackGateway(
            config,
            db,
            history,
            windows,
            settings=GatewaySettings("slack", True, {}),
            transport=FakeSlack(),
        ),
    ]
    badge = Action(
        address=Address("body", "chat"),
        capability="send_text",
        payload='done\n\n🕓 Waiting: /approve 42 (or just reply "ok, send").',
    )

    for body in bodies:
        assert isinstance(body, BaseGateway), body.name
        assert isinstance(body, GatewayCore), body.name
        assert missing_core(body) == [], body.name
        assert set(PROGRESS_FLAGS) <= set(body.default_settings()), body.name
        assert body.default_role_policy("administrator")["dm"] is True, body.name
        block = body.ui_of(badge)
        assert block["kind"] == "approval", body.name
        assert [item["command"] for item in block["buttons"]] == [
            "/approve #42",
            "/deny #42",
        ], body.name
        body.set_progress("show_steps", True)
        assert body.progress_enabled("show_steps") is True, body.name
        assert body.ui_of(Action(Address("body", "c"), "send_text", "plain")) == {}

    for body in bodies[1:]:
        with pytest.raises(ValueError, match="role policy needs"):
            body.validate_role_policy({"dm": True})
