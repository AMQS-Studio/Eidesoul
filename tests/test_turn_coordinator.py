"""Turn coordinator contract: one thread's turn is cancelled without touching another.

ADR-0003 §7.1: `interrupt_thread` is the one implementation behind `/stop` and a barge-in. The
tests drive the real store, so the `turns` row (status, step, reason) is the record, and the
body's structural `abort()` is observed on the fake.
"""


import asyncio
from dataclasses import replace

from support import FakeLLM, RichDirectoryFakeGateway, inbound_message, make_agent

from monoagens.engine.soul import StyleConfig
from monoagens.engine.stores.turns import turn_identity
from monoagens.engine.turn.delivery import Delivery
from monoagens.engine.turn.intake import TurnIntake
from monoagens.engine.turn.loop import TurnRunner

DM = "628111@s.whatsapp.net"
OTHER = "628222@s.whatsapp.net"


class SlowLLM(FakeLLM):
    """A provider that takes real time, so a turn is observably in flight."""

    def __init__(self, delay: float = 0.6, **kwargs) -> None:
        super().__init__(**kwargs)
        self.delay = delay
        self.active = 0

    async def complete(self, system, user, **kwargs):
        self.active += 1
        try:
            await asyncio.sleep(self.delay)
            return await super().complete(system, user, **kwargs)
        finally:
            self.active -= 1

    async def run(self, system, content, specs, ctx, session=None, **kwargs):
        self.active += 1
        try:
            await asyncio.sleep(self.delay)
            return await super().run(system, content, specs, ctx, session, **kwargs)
        finally:
            self.active -= 1


class AbortableGateway(RichDirectoryFakeGateway):
    """A body that can stop its own stream, so the coordinator's abort is observable."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.aborted: list[str] = []

    async def abort(self, thread: str) -> None:
        self.aborted.append(thread)


class RecordingStyle:
    """Records what the turn completion hook fed the style profile."""

    def __init__(self) -> None:
        self.learned: list[tuple[str, str, str]] = []

    def learn(self, text: str, *, thread_id: str = "", person_id: str = "") -> None:
        self.learned.append((str(text), str(thread_id), str(person_id)))


def turn_row(env, message_id: str, thread: str = DM):
    return env.agent.turns.get(turn_identity(env.gateway.name, thread, message_id))


async def wait_for_step(env, message_id: str, step: str, thread: str = DM) -> None:
    """Wait until the turn reached a checkpoint; a slow model keeps it there."""
    for _ in range(1000):
        row = turn_row(env, message_id, thread)
        if row is not None and str(row["step"]) == step:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"turn {message_id} never reached {step}")


def with_style(env, style) -> None:
    """Rebuild the pipeline around a style store, the way the Agent does at boot."""
    runtime = replace(env.agent.turn_runtime, style=style)
    env.agent.turn_runtime = runtime
    env.agent.delivery = Delivery(runtime, env.agent.coordinator)
    env.agent.turn_runner = TurnRunner(
        runtime, env.agent.coordinator, env.agent.delivery, env.agent.steps
    )
    env.agent.intake = TurnIntake(
        runtime, env.agent.coordinator, env.agent.turn_runner, env.agent.delivery
    )


def enable_style(env) -> None:
    voice = replace(env.config.soul.voice, style=StyleConfig(enabled=True))
    soul = replace(env.config.soul, voice=voice)
    env.agent.config = replace(env.config, soul=soul)
    env.agent.soul = soul


def test_interrupt_thread_cancels_an_inflight_turn(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = SlowLLM(delay=0.6)
    inbound = inbound_message("halo", thread=DM, message_id="barge-1")

    async def run() -> list:
        task = asyncio.create_task(env.agent.handle(inbound, env.gateway))
        await wait_for_step(env, "barge-1", "decided")
        assert await env.agent.coordinator.interrupt_thread(DM, reason="barge_in") is True
        return await task

    assert asyncio.run(run()) == []

    turn = turn_row(env, "barge-1")
    assert turn["status"] == "interrupted"
    assert turn["step"] == "decided"
    assert turn["last_error"] == "barge_in"
    assert env.gateway.sent == []
    assert env.agent.coordinator.active_task(DM) is None


def test_interrupt_thread_aborts_the_body_once(tmp_path) -> None:
    gateway = AbortableGateway()
    env = make_agent(tmp_path, gateway=gateway)
    env.agent.llm = SlowLLM(delay=0.6)
    inbound = inbound_message("halo", thread=DM, message_id="barge-2")

    async def run() -> bool:
        task = asyncio.create_task(env.agent.handle(inbound, env.gateway))
        await wait_for_step(env, "barge-2", "decided")
        found = await env.agent.coordinator.interrupt_thread(DM)
        await task
        return found

    assert asyncio.run(run()) is True
    assert gateway.aborted == [DM]
    assert turn_row(env, "barge-2")["status"] == "interrupted"


def test_interrupt_thread_is_false_for_an_idle_thread(tmp_path) -> None:
    env = make_agent(tmp_path)

    async def run() -> tuple[bool, bool]:
        return (
            await env.agent.coordinator.interrupt_thread("idle-thread"),
            await env.agent.cancel_turn("idle-thread"),
        )

    assert asyncio.run(run()) == (False, False)


def test_two_threads_interrupt_independently(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = SlowLLM(delay=1.0)
    first = inbound_message("halo", thread=DM, message_id="two-1")
    second = inbound_message("halo", thread=OTHER, message_id="two-2")

    async def run() -> None:
        tasks = [
            asyncio.create_task(env.agent.handle(first, env.gateway)),
            asyncio.create_task(env.agent.handle(second, env.gateway)),
        ]
        await wait_for_step(env, "two-1", "decided", DM)
        await wait_for_step(env, "two-2", "decided", OTHER)

        assert await env.agent.coordinator.interrupt_thread(DM, reason="barge_in") is True
        assert turn_row(env, "two-1")["status"] == "interrupted"
        assert turn_row(env, "two-2", OTHER)["status"] == "running"
        assert env.agent.coordinator.active_task(OTHER) is not None

        assert await env.agent.coordinator.interrupt_thread(OTHER, reason="user_stop") is True
        await asyncio.gather(*tasks)

    asyncio.run(run())

    assert turn_row(env, "two-1")["last_error"] == "barge_in"
    assert turn_row(env, "two-2", OTHER)["last_error"] == "user_stop"
    assert env.agent.coordinator.active_task(DM) is None
    assert env.agent.coordinator.active_task(OTHER) is None


def test_style_learns_the_delivered_text_once(tmp_path) -> None:
    env = make_agent(tmp_path)
    style = RecordingStyle()
    with_style(env, style)
    enable_style(env)

    asyncio.run(env.agent.handle(inbound_message("halo", thread=DM), env.gateway))

    person = env.agent.identity.resolve("fake", "628111")
    assert style.learned == [("halo juga", DM, person)]


def test_style_is_not_learned_when_disabled(tmp_path) -> None:
    env = make_agent(tmp_path)
    style = RecordingStyle()
    with_style(env, style)

    asyncio.run(env.agent.handle(inbound_message("halo", thread=DM), env.gateway))

    assert style.learned == []
