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

from eidesoul.engine.config import ConfigError, GatewaySettings
from eidesoul.engine.inbound import (
    Chat,
    Directory,
    GatewayCore,
    Rich,
    capability_report,
    ensure_core,
    refusal,
)
from eidesoul.engine.registry import Handler, RegistryError
from eidesoul.engine.stores.history import HistoryStore
from eidesoul.engine.turn.context import WorkingMemory


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
    from eidesoul.engine.tools import execute

    return asyncio.run(execute(specs(env), name, args, ctx))


def test_gateway_tools_are_indexed_per_body(tmp_path) -> None:
    """ADR-0032 §4.6: the same name in two bodies is not a collision, it is two tools."""
    first = FakeGateway(name="a", tools=[handler("send")])
    second = FakeGateway(name="b", tools=[handler("send")])
    env = make_agent(tmp_path, gateways=[first, second])

    index = env.agent.tool_index
    assert set(index.specs("a")) >= {"send"}
    assert set(index.specs("b")) >= {"send"}
    assert index.specs("a")["send"] is not index.specs("b")["send"]
    assert index.resolve("b__send", "a")[0] == "send"
    assert env.agent.tools.get("send") is None
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
        return await env.agent.send_file("chat", note, "lampiran", gateway=gateway)

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
    asyncio.run(env.agent.send_file("chat", note, gateway=gateway))

    assert gateway.files
    assert gateway.files[0][1].name == "catatan.txt"
    assert ("chat", True) in gateway.typing_events
    assert gateway.typing_events[-1] == ("chat", False)


def test_without_directory_a_cross_thread_read_is_refused(tmp_path) -> None:
    from eidesoul.gateways.whatsapp.tools.chat_history import TOOL as CHAT_HISTORY

    gateway = FakeGateway(tools=[CHAT_HISTORY])
    env = make_agent(
        tmp_path,
        gateway=gateway,
        roles={"owner": role("owner", tools=("chat_history",), commands=("*",))},
        default_role="owner",
    )

    result = call_tool(env, "chat_history", {"chat": "budi"}, thread="111@g.us")

    assert result.startswith("error:")
    assert "no directory" in result


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

        def prompt_hints(self):
            return ""

    with pytest.raises(ConfigError, match="self_handles"):
        ensure_core(Broken())


def test_build_gateways_logs_capabilities_and_refuses_a_broken_body(
    tmp_path, monkeypatch, caplog
) -> None:
    from eidesoul import gateways as gateway_package

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

        def prompt_hints(self):
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
    assert report["boot"] == {"chat": True, "rich": True, "directory": True}

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
        "core": {"chat": True, "rich": False, "directory": False},
        "rich": {"chat": True, "rich": True, "directory": False},
        "directory": {"chat": True, "rich": False, "directory": True},
    }


def test_the_whatsapp_catalog_owns_the_archive_tool(tmp_path) -> None:
    from test_whatsapp_gateway import make_gateway

    from eidesoul.engine.tools import REGISTRY as CORE

    gateway = make_gateway(tmp_path)
    names = {item.name for item in gateway.tools()}
    assert "chat_history" in names
    assert "chat_history" not in CORE.names()
    root = Path(__file__).resolve().parents[1]
    assert not (root / "eidesoul" / "engine" / "tools" / "chat_history.py").exists()
    assert (root / "eidesoul" / "gateways" / "whatsapp" / "tools" / "chat_history.py").is_file()


def test_core_imports_without_neonize() -> None:
    root = Path(__file__).resolve().parents[1]
    code = "import sys; sys.modules['neonize']=None; import eidesoul.engine; print('ok')"
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_core_gateway_meets_only_core() -> None:
    assert isinstance(FakeGateway(), GatewayCore)
    assert isinstance(FakeGateway(), Chat)
    assert not isinstance(FakeGateway(), Rich)
    assert not isinstance(FakeGateway(), Directory)


def test_the_harness_body_completes_a_turn_and_returns_its_actions(tmp_path) -> None:
    from eidesoul.gateways.harness.gateway import HarnessGateway

    config = build_config(tmp_path)
    gateway = HarnessGateway(
        config,
        None,
        None,
        None,
        settings=GatewaySettings(
            "harness",
            True,
            {"task": "ringkas status", "thread": "harness:1", "actor": "harness"},
        ),
    )
    env = make_agent(tmp_path, gateway=gateway)

    actions = asyncio.run(gateway.run(env.agent))

    assert actions
    assert actions[0].capability == "send_text"
    assert gateway.actions == actions
    assert gateway.sent == [str(action.payload) for action in actions]
    assert "run" in env.agent.tool_index.names("harness")
    assert "inspect" in env.agent.commands.names()


def test_the_harness_tools_go_through_the_same_tool_door(tmp_path) -> None:
    from eidesoul.engine.tools import execute
    from eidesoul.gateways.harness.gateway import HarnessGateway

    config = build_config(tmp_path)
    gateway = HarnessGateway(
        config, None, None, None, settings=GatewaySettings("harness", True, {"task": "x"})
    )
    env = make_agent(tmp_path, gateway=gateway)
    owner = role("owner", tools=("*",), commands=("*",))
    ctx = env.agent.tool_context(gateway.thread, "harness", (owner,), gateway)

    result = asyncio.run(
        execute(specs(env), "run", {"command": "printf harness-ok"}, ctx)
    )

    assert result.startswith("exit=0")
    assert "harness-ok" in result

    denied = asyncio.run(
        execute(
            specs(env),
            "run",
            {"command": "printf no"},
            env.agent.tool_context(
                gateway.thread, "harness", (role("guest", tools=("read",)),), gateway
            ),
        )
    )
    assert denied.startswith("error: tool 'run' is not allowed")
