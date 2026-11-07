"""ADR-0010: one body's crash restarts that body alone; the others keep serving."""

import asyncio
from types import SimpleNamespace
from typing import Any, cast

from monoagens.engine.jobs.supervisor import Supervisor


class Body:
    def __init__(self, name: str, *, crash: bool) -> None:
        self.name = name
        self.crash = crash
        self.runs = 0
        self.cancelled = False

    async def run(self, agent) -> None:
        self.runs += 1
        if self.crash:
            raise AttributeError("module 'discord' has no attribute 'ext'")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class FakeLease:
    def frozen(self) -> bool:
        return False

    def renew(self) -> bool:
        return True


class FakeCognition:
    def status(self) -> str:
        return "off"

    async def stop(self) -> None:
        return None


def test_a_crashed_gateway_restarts_alone() -> None:
    crashed = Body("discord", crash=True)
    healthy = Body("whatsapp", crash=False)

    class Agent:
        runtime = SimpleNamespace(cognition="off")

        def start_reaper(self) -> None:
            return None

        async def stop_reaper(self) -> None:
            return None

    supervisor = Supervisor.__new__(Supervisor)
    supervisor.agent = Agent()
    supervisor.gateways = [crashed, healthy]
    supervisor.tick = 0.01
    supervisor.backoff = (0.01, 0.01)
    supervisor.stopping = False
    supervisor.lease = cast(Any, FakeLease())
    supervisor.cognition = cast(Any, FakeCognition())
    supervisor.automations = None

    async def run() -> None:
        task = asyncio.create_task(supervisor.serve())
        await asyncio.sleep(0.15)
        assert supervisor.stopping is False, "a crash must not stop the process"
        assert crashed.runs >= 2, "the crashed body restarts on its own"
        assert healthy.cancelled is False, "the healthy body keeps running"
        supervisor.stopping = True
        await asyncio.wait_for(task, 2.0)
        assert healthy.cancelled is True, "shutdown still cancels every body"

    asyncio.run(run())
