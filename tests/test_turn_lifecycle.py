"""Turn lifecycle contract (ADR-0013, ADR-0011, ADR-0017).

Every test drives the real store: `turns`, `turn_steps`, and `outbound_messages` are the
records, not an in-memory stand-in.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

import pytest
from support import FakeGateway, FakeLLM, HumanizeConfig, inbound_message, make_agent

from eidesoul.engine.config import TurnConfig
from eidesoul.engine.i18n import t
from eidesoul.engine.memory.ledger import OutboundLedger

DM = "628111@s.whatsapp.net"
OTHER = "628222@s.whatsapp.net"


def handle(env, inbound, **kwargs):
    return asyncio.run(env.agent.handle(inbound, env.gateway, **kwargs))


class SlowLLM(FakeLLM):
    """A provider that takes real time, so locks, deadlines, and cancellation are observable."""

    def __init__(self, delay: float = 0.25, **kwargs) -> None:
        super().__init__(**kwargs)
        self.delay = delay
        self.active = 0
        self.peak = 0

    async def complete(self, system, user, **kwargs):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
            return await super().complete(system, user, **kwargs)
        finally:
            self.active -= 1

    async def run(self, system, content, specs, ctx, session=None, **kwargs):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
            return await super().run(system, content, specs, ctx, session, **kwargs)
        finally:
            self.active -= 1


def test_a_turn_is_checkpointed_and_delivered(tmp_path) -> None:
    env = make_agent(tmp_path)
    inbound = inbound_message("halo", thread=DM)

    handle(env, inbound)

    turn = env.agent.turns.get(inbound.payload.id)
    assert turn["status"] == "delivered"
    assert turn["step"] == "delivered"
    assert turn["planned_chunks"] == 1
    assert turn["delivered_chunks"] == 1
    assert turn["attempt"] == 1
    ledger = OutboundLedger(env.db)
    assert ledger.status(f"turn:{inbound.payload.id}", 0) == "sent"


def test_a_delivered_turn_is_never_processed_twice(tmp_path) -> None:
    env = make_agent(tmp_path)
    inbound = inbound_message("halo", thread=DM)

    handle(env, inbound)
    handle(env, inbound)

    assert len(env.gateway.sent) == 1
    assert env.llm.calls == 1


def test_a_mid_turn_failure_resumes_without_a_duplicate_send(tmp_path) -> None:
    from eidesoul.engine.inbound import GatewayError

    class FailingGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def send_text(self, thread_id, text, *, reply=None):
            self.calls += 1
            if self.calls == 2:
                raise GatewayError("boom")
            return await super().send_text(thread_id, text, reply=reply)

    gateway = FailingGateway()
    env = make_agent(
        tmp_path,
        answer="satu\n\n---\n\ndua",
        gateway=gateway,
        humanize=HumanizeConfig(enabled=True, min_delay=0, max_delay=0, between_delay=0),
    )
    inbound = inbound_message("halo", thread=DM, message_id="resume-1")

    handle(env, inbound)
    first = env.agent.turns.get("resume-1")
    assert first["status"] == "sending"
    assert first["delivered_chunks"] == 1

    env.agent.config = replace(
        env.agent.config,
        turns=replace(env.agent.config.turns, partial_delivery="resend"),
    )
    handle(env, inbound)

    turn = env.agent.turns.get("resume-1")
    assert turn["status"] == "delivered"
    assert [text for _, text, _ in env.gateway.sent] == ["satu", "dua"]
    assert turn["delivered_chunks"] == 2


def test_the_same_thread_waits_and_another_thread_runs(tmp_path) -> None:
    env = make_agent(tmp_path)
    slow = SlowLLM(delay=0.15)
    env.agent.llm = slow

    async def same_thread() -> None:
        await asyncio.gather(
            env.agent.handle(inbound_message("satu", thread=DM, message_id="a1"), env.gateway),
            env.agent.handle(inbound_message("dua", thread=DM, message_id="a2"), env.gateway),
        )

    asyncio.run(same_thread())
    assert slow.peak == 1

    slow.peak = 0

    async def different_threads() -> None:
        await asyncio.gather(
            env.agent.handle(inbound_message("satu", thread=DM, message_id="b1"), env.gateway),
            env.agent.handle(
                inbound_message("dua", thread=OTHER, message_id="b2"), env.gateway
            ),
        )

    asyncio.run(different_threads())
    assert slow.peak == 2


def test_a_standby_refuses_intake_and_sends_nothing(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.mode = "standby"

    actions = handle(env, inbound_message("halo", thread=DM))

    assert actions == []
    assert env.gateway.sent == []
    assert env.agent.turns.get("m-halo") is None
    result = asyncio.run(env.agent.send_text(DM, "halo", gateway=env.gateway))
    assert result.startswith("error:")
    assert env.gateway.sent == []
    assert env.agent.runtime.standby_refused >= 2


def test_a_stale_generation_is_refused(tmp_path) -> None:
    env = make_agent(tmp_path)

    class StaleLease:
        instance_id = "this"
        generation = 1

        @staticmethod
        def current_generation():
            return 2

    env.agent.lease = StaleLease()
    result = asyncio.run(env.agent.send_text(DM, "halo", gateway=env.gateway))

    assert result.startswith("error:")
    assert env.gateway.sent == []
    assert env.agent.runtime.standby_refused == 1


def test_the_turn_deadline_interrupts_once_with_one_notice(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = SlowLLM(delay=1.0)
    env.agent.config = replace(
        env.agent.config,
        turns=replace(env.agent.config.turns, timeout_seconds=0.05, min_call_seconds=0.0),
    )
    inbound = inbound_message("halo", thread=DM, message_id="slow-1")

    handle(env, inbound)
    handle(env, inbound)

    turn = env.agent.turns.get("slow-1")
    assert turn["status"] == "interrupted"
    assert turn["last_error"] == "timeout"
    notices = [text for _, text, _ in env.gateway.sent if "terlalu lama" in text]
    assert len(notices) == 1
    assert env.agent.runtime.turn_timeout_total == 1


def test_remaining_below_min_call_starts_no_llm_call(tmp_path, monkeypatch) -> None:
    from eidesoul.engine.llm import LLM, DeadlineExceeded

    monkeypatch.setenv("K", "x")
    env = make_agent(tmp_path)
    llm = LLM(env.config)
    with pytest.raises(DeadlineExceeded):
        llm._request_client(time.monotonic() - 1.0, 5.0)
    with pytest.raises(DeadlineExceeded):
        asyncio.run(llm.complete("s", "u", deadline=time.monotonic() - 1.0, min_call_seconds=5.0))


def test_stop_cancels_only_the_running_turn(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = SlowLLM(delay=1.0)
    lang = env.agent.soul.language

    async def run() -> None:
        turn = asyncio.create_task(
            env.agent.handle(
                inbound_message("halo", thread=DM, message_id="stop-1"), env.gateway
            )
        )
        await asyncio.sleep(0.05)
        await env.agent.handle(
            inbound_message("/stop", thread=DM, message_id="stop-cmd"), env.gateway
        )
        await asyncio.gather(turn, return_exceptions=True)

    asyncio.run(run())

    turn = env.agent.turns.get("stop-1")
    assert turn["status"] == "interrupted"
    assert turn["last_error"] == "user_stop"
    assert t("command.stop.cancelled", lang) in [text for _, text, _ in env.gateway.sent]
    assert env.gateway.sent and all(thread == DM for thread, _, _ in env.gateway.sent)


def test_stop_without_a_running_turn_answers_briefly(tmp_path) -> None:
    env = make_agent(tmp_path)
    lang = env.agent.soul.language

    handle(env, inbound_message("/stop", thread=DM, message_id="idle-stop"))

    assert t("command.stop.none", lang) in [text for _, text, _ in env.gateway.sent]


def test_shutdown_drains_notices_once_and_keeps_the_turn_unfinished(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = SlowLLM(delay=1.0)

    async def run() -> dict[str, str]:
        task = asyncio.create_task(
            env.agent.handle(
                inbound_message("halo", thread=DM, message_id="drain-1"), env.gateway
            )
        )
        await asyncio.sleep(0.05)
        env.agent.begin_drain()
        assert env.agent.runtime.phase == "draining"
        pending = await env.agent.drain(0.01)
        await asyncio.gather(task, return_exceptions=True)
        return pending

    pending = asyncio.run(run())
    assert pending["628111@s.whatsapp.net"] == "drain-1"
    asyncio.run(env.agent.shutdown_notices(pending, 1.0))
    asyncio.run(env.agent.shutdown_notices(pending, 1.0))

    notices = [text for _, text, _ in env.gateway.sent if "Koneksiku putus" in text]
    assert len(notices) == 1
    turn = env.agent.turns.get("drain-1")
    assert turn["status"] == "interrupted"
    assert turn["shutdown_epoch"] > 0


def test_the_outbound_ledger_is_idempotent(tmp_path) -> None:
    env = make_agent(tmp_path)
    ledger = OutboundLedger(env.db)

    assert ledger.begin("turn:x", 0, gateway="fake", thread_id=DM) == "intent"
    ledger.mark_sent("turn:x", 0, "provider-1")
    assert ledger.begin("turn:x", 0, gateway="fake", thread_id=DM) == "sent"
    assert ledger.count_sent("turn:x") == 1

    assert ledger.begin("turn:x", 1, gateway="fake", thread_id=DM) == "intent"
    ledger.mark_unknown("turn:x", 1)
    assert ledger.begin("turn:x", 1, gateway="fake", thread_id=DM) == "unknown"


def test_the_quiet_mechanism_is_gone(tmp_path) -> None:
    env = make_agent(tmp_path)

    assert not hasattr(env.agent, "quiet")
    assert "reply" not in {field for field in env.agent.config.__dataclass_fields__}


def test_turn_config_defaults_match_the_adrs() -> None:
    config = TurnConfig()

    assert config.timeout_seconds == 90
    assert config.min_call_seconds == 5
    assert config.partial_delivery == "skip"
    assert config.shutdown.grace_seconds == 25
    assert config.shutdown.drain_seconds == 15
    assert config.shutdown.notice_seconds == 5
    assert config.shutdown.grace_seconds < config.shutdown.stop_grace_period


def test_two_parallel_threads_keep_their_own_turn_rows(tmp_path) -> None:
    env = make_agent(tmp_path)

    async def run() -> None:
        await asyncio.gather(
            env.agent.handle(inbound_message("satu", thread=DM, message_id="p1"), env.gateway),
            env.agent.handle(
                inbound_message("dua", thread=OTHER, message_id="p2"), env.gateway
            ),
        )

    asyncio.run(run())

    assert env.agent.turns.get("p1")["thread_id"] == DM
    assert env.agent.turns.get("p2")["thread_id"] == OTHER
    rows = env.db.fetch_all(_turns_table().select())
    assert {row["status"] for row in rows} == {"delivered"}


def test_a_duplicate_message_does_not_fork_the_turn(tmp_path) -> None:
    env = make_agent(tmp_path)
    inbound = inbound_message("halo", thread=DM, message_id="dup-1")

    assert env.history.intake(DM, inbound.payload, "628111") is True
    assert env.history.intake(DM, inbound.payload, "628111") is False
    assert len(env.db.fetch_all(_turns_table().select())) == 1


def _turns_table():
    from eidesoul.engine.schema import turns

    return turns


def test_a_turn_row_requires_a_valid_status(tmp_path) -> None:
    from sqlalchemy.exc import IntegrityError

    env = make_agent(tmp_path)
    inbound = inbound_message("halo", thread=DM, message_id="bad-1")
    env.history.intake(DM, inbound.payload, "628111")

    with pytest.raises(IntegrityError), env.db.connect() as conn:
        conn.execute(
            _turns_table()
            .update()
            .where(_turns_table().c.id == "bad-1")
            .values(status="exploded")
        )


def test_time_is_bounded_by_the_deadline_not_per_call(tmp_path) -> None:
    env = make_agent(tmp_path)
    slow = SlowLLM(delay=0.2)
    env.agent.llm = slow
    env.agent.config = replace(
        env.agent.config,
        turns=replace(env.agent.config.turns, timeout_seconds=0.3, min_call_seconds=0.01),
    )
    started = time.monotonic()

    handle(env, inbound_message("halo", thread=DM, message_id="budget-1"))

    elapsed = time.monotonic() - started
    assert elapsed < 0.8
    assert env.agent.turns.get("budget-1")["status"] in {"delivered", "interrupted"}
