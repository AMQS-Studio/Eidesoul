"""CLI gateway contract: stdin -> Percept, output to stdout, /quit stops."""


import asyncio
import io

from support import build_config, database

from monoagens.engine.access.roles import Role
from monoagens.engine.config import GatewaySettings
from monoagens.engine.stores.history import HistoryStore
from monoagens.engine.turn.context import WorkingMemory

POLICY = {"cli": {"dm": True}}


class RecordingAgent:
    mode = "primary"

    def __init__(self) -> None:
        self.inbounds = []

    async def handle(self, inbound, gateway) -> None:
        self.inbounds.append(inbound)


def make_cli(tmp_path, *, gateways=None):
    from monoagens.gateways.cli.gateway import CliGateway

    config = build_config(
        tmp_path,
        roles={"member": Role("member", gateways=POLICY if gateways is None else gateways)},
    )
    db = database()
    gateway = CliGateway(
        config,
        db,
        HistoryStore(db),
        WorkingMemory(),
        settings=GatewaySettings(
            "cli",
            True,
            {"user": "local", "thread_id": "cli:local", "chat_name": "Terminal"},
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
    assert "roles" not in inbound.hints


def test_a_role_without_the_cli_policy_is_denied(tmp_path, monkeypatch) -> None:
    gateway = make_cli(tmp_path, gateways={})
    agent = RecordingAgent()
    monkeypatch.setattr("sys.stdin", io.StringIO("halo\n/quit\n"))

    asyncio.run(gateway.run(agent))

    assert agent.inbounds == []


def test_a_malformed_cli_policy_is_denied(tmp_path) -> None:
    gateway = make_cli(tmp_path, gateways={"cli": {"dm": "yes"}})
    assert gateway.access_refusal("local") == "denied"


def test_send_text_prints_with_prefix(tmp_path, capsys) -> None:
    gateway = make_cli(tmp_path)

    message_id = asyncio.run(gateway.send_text("cli:local", "halo dunia"))

    out = capsys.readouterr().out
    assert "MonoAgens: halo dunia" in out
    assert message_id.startswith("cli-")


def test_render_is_identity_and_hints_are_plain(tmp_path) -> None:
    gateway = make_cli(tmp_path)
    assert gateway.render("**tebal**") == "**tebal**"
    assert "plain text" in gateway.prompt_section()
    assert gateway.self_handles() == set()


def test_the_cli_is_core_only(tmp_path) -> None:
    from monoagens.engine.inbound import Directory, GatewayCore, PrivateThread, Rich

    gateway = make_cli(tmp_path)

    assert isinstance(gateway, GatewayCore)
    assert isinstance(gateway, PrivateThread)
    assert not isinstance(gateway, Rich)
    assert not isinstance(gateway, Directory)
    for absent in ("send_file", "send_sticker", "typing", "prepare_media", "participants"):
        assert not hasattr(gateway, absent), absent
    assert gateway.chat_name("cli:local") == "Terminal"
