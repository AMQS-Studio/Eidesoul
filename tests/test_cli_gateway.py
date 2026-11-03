"""CLI gateway contract: stdin -> Percept, output to stdout, /quit stops."""


import asyncio
import io

from support import build_config, database

from eidesoul.engine.config import GatewaySettings
from eidesoul.engine.stores.history import HistoryStore
from eidesoul.engine.turn.context import WorkingMemory


class RecordingAgent:
    mode = "primary"

    def __init__(self) -> None:
        self.inbounds = []

    async def handle(self, inbound, gateway) -> None:
        self.inbounds.append(inbound)


def make_cli(tmp_path):
    from eidesoul.gateways.cli.gateway import CliGateway

    config = build_config(tmp_path)
    db = database()
    gateway = CliGateway(
        config,
        db,
        HistoryStore(db),
        WorkingMemory(),
        settings=GatewaySettings(
            "cli",
            True,
            {"role": "owner", "user": "local", "thread_id": "cli:local", "chat_name": "Terminal"},
        ),
    )
    return gateway


def test_stdin_line_becomes_inbound_and_quit_stops(tmp_path, monkeypatch) -> None:
    gateway = make_cli(tmp_path)
    agent = RecordingAgent()
    monkeypatch.setattr("sys.stdin", io.StringIO("halo\n/quit\n"))

    asyncio.run(gateway.run(agent))

    assert len(agent.inbounds) == 1
    inbound = agent.inbounds[0]
    assert inbound.thread == "cli:local"
    assert inbound.payload.text == "halo"
    assert inbound.hints.get("dm") is True
    assert inbound.hints.get("roles") == ("owner",)


def test_send_text_prints_with_prefix(tmp_path, capsys) -> None:
    gateway = make_cli(tmp_path)

    message_id = asyncio.run(gateway.send_text("cli:local", "halo dunia"))

    out = capsys.readouterr().out
    assert "Eidesoul: halo dunia" in out
    assert message_id.startswith("cli-")


def test_render_is_identity_and_hints_are_plain(tmp_path) -> None:
    gateway = make_cli(tmp_path)
    assert gateway.render("**tebal**") == "**tebal**"
    assert "plain text" in gateway.prompt_section()
    assert gateway.self_handles() == set()


def test_the_cli_is_core_only(tmp_path) -> None:
    from eidesoul.engine.inbound import Chat, Directory, GatewayCore, Rich

    gateway = make_cli(tmp_path)

    assert isinstance(gateway, GatewayCore)
    assert isinstance(gateway, Chat)
    assert not isinstance(gateway, Rich)
    assert not isinstance(gateway, Directory)
    for absent in ("send_file", "send_sticker", "typing", "prepare_media", "participants"):
        assert not hasattr(gateway, absent), absent
    assert gateway.chat_name("cli:local") == "Terminal"
