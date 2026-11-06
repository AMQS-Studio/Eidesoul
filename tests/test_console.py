"""Console contract: stdin -> one percept, replies print, `/quit` exits, errors are logged."""


import asyncio
import io
import logging
import threading

from monoagens.admin.console import ADDRESS, THREAD, Console
from monoagens.engine.inbound import Address


class RecordingAgent:
    mode = "primary"

    def __init__(self, *, fail: bool = False) -> None:
        self.inbounds = []
        self.fail = fail

    async def handle(self, inbound, gateway) -> None:
        self.inbounds.append((inbound, gateway))
        if self.fail:
            raise RuntimeError("turn exploded")
        await gateway.send_text(inbound.thread, "balasan")


def test_stdin_line_reaches_handle_and_quit_exits(monkeypatch, capsys) -> None:
    console = Console(user="local")
    agent = RecordingAgent()
    monkeypatch.setattr("sys.stdin", io.StringIO("halo\n/quit\n"))

    asyncio.run(console.run(agent))

    assert len(agent.inbounds) == 1
    inbound, gateway = agent.inbounds[0]
    assert gateway is console
    assert inbound.thread == THREAD == "console:local"
    assert console.address == ADDRESS == Address("console", "local")
    assert inbound.payload.sender_address == "local"
    assert inbound.payload.text == "halo"
    assert inbound.hints.get("dm") is True
    assert "MonoAgens: balasan" in capsys.readouterr().out


def test_stdin_eof_ends_the_session_instead_of_parking_it(monkeypatch) -> None:
    # I.1.3: a closed stdin must end the session; waiting for `stop()` used to park forever.
    console = Console()
    agent = RecordingAgent()
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    async def scenario() -> None:
        task = asyncio.create_task(console.run(agent))
        await asyncio.wait_for(task, 1.0)

    asyncio.run(scenario())
    assert agent.inbounds == []
    assert console.stopped is True


def test_a_failed_turn_is_logged_and_the_console_continues(monkeypatch, caplog) -> None:
    caplog.set_level(logging.ERROR)
    console = Console()
    agent = RecordingAgent(fail=True)
    monkeypatch.setattr("sys.stdin", io.StringIO("halo\n/quit\n"))

    asyncio.run(console.run(agent))

    assert len(agent.inbounds) == 1
    assert "console turn failed" in caplog.text


def test_stop_releases_a_blocked_stdin_reader(monkeypatch) -> None:
    console = Console()
    agent = RecordingAgent()
    gate = threading.Event()

    class BlockingStdin:
        @staticmethod
        def readline():
            gate.wait(10)
            return ""

    monkeypatch.setattr("sys.stdin", BlockingStdin())

    async def scenario() -> None:
        task = asyncio.create_task(console.run(agent))
        await asyncio.sleep(0.05)
        await console.stop()
        try:
            await asyncio.wait_for(task, 0.5)
        finally:
            gate.set()

    asyncio.run(scenario())
