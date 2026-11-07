"""Turn lifecycle contract (ADR-0003, ADR-0001, ADR-0003).

Every test drives the real store: `turns`, `turn_steps`, and `outbound_messages` are the
records, not an in-memory stand-in.
"""


import asyncio
import time
from dataclasses import replace

import pytest
from sqlalchemy import select
from support import FakeGateway, FakeLLM, HumanizeConfig, inbound_message, make_agent

from monoagens.engine.config import TurnConfig
from monoagens.engine.i18n import t
from monoagens.engine.inbound import Address
from monoagens.engine.memory.ledger import OutboundLedger
from monoagens.engine.schema import turns
from monoagens.engine.stores.turns import turn_identity

DM = "628111@s.whatsapp.net"
OTHER = "628222@s.whatsapp.net"


def handle(env, inbound, **kwargs):
    return asyncio.run(env.agent.handle(inbound, env.gateway, **kwargs))


def turn_row(env, message_id: str, thread: str = DM):
    """The namespaced turn identity is the key; `message_id` is the raw transport id."""
    return env.agent.turns.get(turn_identity(env.gateway.name, thread, message_id))


def turn_scope(env, message_id: str, thread: str = DM) -> str:
    return f"turn:{turn_identity(env.gateway.name, thread, message_id)}"


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

    turn = turn_row(env, inbound.payload.id)
    assert turn["status"] == "delivered"
    assert turn["step"] == "delivered"
    assert turn["planned_chunks"] == 1
    assert turn["delivered_chunks"] == 1
    assert turn["attempt"] == 1
    ledger = OutboundLedger(env.db)
    assert ledger.status(f"{turn_scope(env, inbound.payload.id)}:0", 0) == "sent"


def test_a_delivered_turn_is_never_processed_twice(tmp_path) -> None:
    env = make_agent(tmp_path)
    inbound = inbound_message("halo", thread=DM)

    handle(env, inbound)
    handle(env, inbound)

    assert len(env.gateway.sent) == 1
    assert env.llm.calls == 1


def test_a_mid_turn_failure_resumes_without_a_duplicate_send(tmp_path) -> None:
    from monoagens.engine.inbound import GatewayError

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
    first = turn_row(env, "resume-1")
    assert first["status"] == "sending"
    assert first["delivered_chunks"] == 1

    env.agent.config = replace(
        env.agent.config,
        turns=replace(env.agent.config.turns, partial_delivery="resend"),
    )
    handle(env, inbound)

    turn = turn_row(env, "resume-1")
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
    assert turn_row(env, "m-halo") is None
    result = asyncio.run(
        env.agent.send_text(
            DM, "halo", gateway=env.gateway, origin=Address(env.gateway.name, DM)
        )
    )
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
    result = asyncio.run(
        env.agent.send_text(
            DM, "halo", gateway=env.gateway, origin=Address(env.gateway.name, DM)
        )
    )

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

    turn = turn_row(env, "slow-1")
    assert turn["status"] == "interrupted"
    assert turn["last_error"] == "timeout"
    notices = [text for _, text, _ in env.gateway.sent if "terlalu lama" in text]
    assert len(notices) == 1
    assert env.agent.runtime.turn_timeout_total == 1


def test_remaining_below_min_call_starts_no_llm_call(tmp_path, monkeypatch) -> None:
    from monoagens.engine.llm import LLM, DeadlineExceeded

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

    turn = turn_row(env, "stop-1")
    assert turn["status"] == "interrupted"
    assert turn["last_error"] == "user_stop"
    assert t("command.stop.cancelled", lang) in [text for _, text, _ in env.gateway.sent]
    assert env.gateway.sent
    assert all(thread == DM for thread, _, _ in env.gateway.sent)


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
        for _ in range(200):
            if env.agent.coordinator.active_task(DM) is not None:
                break
            await asyncio.sleep(0.005)
        env.agent.begin_drain()
        assert env.agent.runtime.phase == "draining"
        pending = await env.agent.drain(0.01)
        await asyncio.gather(task, return_exceptions=True)
        return pending

    pending = asyncio.run(run())
    assert pending[DM] == turn_identity(env.gateway.name, DM, "drain-1")
    asyncio.run(env.agent.shutdown_notices(pending, 1.0))
    asyncio.run(env.agent.shutdown_notices(pending, 1.0))

    notices = [text for _, text, _ in env.gateway.sent if "Koneksiku putus" in text]
    assert len(notices) == 1
    turn = turn_row(env, "drain-1")
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

    assert config.timeout_seconds == 300
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

    assert turn_row(env, "p1")["thread_id"] == DM
    assert turn_row(env, "p2", OTHER)["thread_id"] == OTHER
    rows = env.db.fetch_all(_turns_table().select())
    assert {row["status"] for row in rows} == {"delivered"}


def test_a_duplicate_message_does_not_fork_the_turn(tmp_path) -> None:
    env = make_agent(tmp_path)
    inbound = inbound_message("halo", thread=DM, message_id="dup-1")

    assert env.history.intake(DM, inbound.payload, "628111", language="id") is True
    assert env.history.intake(DM, inbound.payload, "628111", language="id") is False
    assert len(env.db.fetch_all(_turns_table().select())) == 1


def _turns_table():
    from monoagens.engine.schema import turns

    return turns


def test_a_turn_row_requires_a_valid_status(tmp_path) -> None:
    from sqlalchemy.exc import IntegrityError

    env = make_agent(tmp_path)
    inbound = inbound_message("halo", thread=DM, message_id="bad-1")
    env.history.intake(DM, inbound.payload, "628111", language="id")

    with pytest.raises(IntegrityError), env.db.connect() as conn:
        conn.execute(
            _turns_table()
            .update()
            .where(_turns_table().c.id == turn_identity("", DM, "bad-1"))
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
    assert turn_row(env, "budget-1")["status"] in {"delivered", "interrupted"}


def backlog(env, thread: str, count: int, *, action: bool = False) -> list[str]:
    """One message + turn per row, all claimable (an expired lease) and never processed."""
    ids: list[str] = []
    prefix = "action" if action else "chat"
    for index in range(count):
        message = inbound_message(
            f"pesan {index}", thread=thread, message_id=f"{thread}-{prefix}-{index}"
        )
        env.history.intake(
            thread,
            message.payload,
            message.actor,
            category="action" if action else "chat",
            gateway=env.gateway.name,
        language="id",
        )
        ids.append(turn_identity(env.gateway.name, thread, message.payload.id))
    return ids


def turns_state(env, thread: str) -> dict[str, tuple[str, int]]:
    rows = env.db.fetch_all(
        select(turns.c.id, turns.c.status, turns.c.attempt)
        .where(turns.c.thread_id == thread)
        .order_by(turns.c.created_at)
    )
    return {str(row["id"]): (str(row["status"]), int(row["attempt"])) for row in rows}


def record_resumes(env) -> list[str]:
    """Replace the turn runner so a pass is observable without processing anything."""

    async def fake_handle(inbound, gateway, *, claimed=None):
        return []

    started: list[str] = []

    def spawn(coro) -> None:
        coro.close()
        started.append("closed")

    env.agent.handle = fake_handle
    env.agent.spawn = spawn
    return started


def test_the_backlog_pass_is_paced_and_round_robin(tmp_path) -> None:
    """ADR-0003 §11: a minute's budget, one row per chat per lap, the rest released."""
    env = make_agent(tmp_path)
    threads = ["a@s.whatsapp.net", "b@s.whatsapp.net", "c@s.whatsapp.net"]
    for thread in threads:
        backlog(env, thread, 5)
    env.agent.config = replace(
        env.config,
        turns=replace(
            env.config.turns,
            max_resume_per_chat=2,
            max_resume_per_minute=4,
            backlog_policy="per_message",
        ),
    )

    started = record_resumes(env)
    assert asyncio.run(env.agent.recover()) == 4
    assert len(started) == 4
    assert len(env.agent._resume_times) == 4

    states = {thread: turns_state(env, thread) for thread in threads}
    running = {
        thread: [key for key, value in states[thread].items() if value[0] == "running"]
        for thread in threads
    }
    # Round-robin: the two oldest rows of `a`, one of `b`, one of `c`.
    assert [len(running[thread]) for thread in threads] == [2, 1, 1]
    first = turn_identity(env.gateway.name, threads[0], f"{threads[0]}-chat-0")
    second = turn_identity(env.gateway.name, threads[0], f"{threads[0]}-chat-1")
    assert sorted(running[threads[0]]) == sorted([first, second])
    assert running[threads[1]] == [
        turn_identity(env.gateway.name, threads[1], f"{threads[1]}-chat-0")
    ]
    assert running[threads[2]] == [
        turn_identity(env.gateway.name, threads[2], f"{threads[2]}-chat-0")
    ]
    assert all(
        value == ("received", 0)
        for thread in threads
        for key, value in states[thread].items()
        if value[0] != "running"
    )


def test_the_next_minute_takes_the_next_batch(tmp_path) -> None:
    env = make_agent(tmp_path)
    thread = "a@s.whatsapp.net"
    backlog(env, thread, 5)
    env.agent.config = replace(
        env.config,
        turns=replace(
            env.config.turns,
            max_resume_per_chat=2,
            max_resume_per_minute=2,
            backlog_policy="per_message",
        ),
    )
    record_resumes(env)

    assert asyncio.run(env.agent.recover()) == 2
    assert asyncio.run(env.agent.recover()) == 0  # the minute is spent: claims are released
    states = turns_state(env, thread)
    assert [value[1] for value in states.values()].count(0) == 3  # attempt was given back
    env.agent._resume_times.clear()  # one minute later
    assert asyncio.run(env.agent.recover()) == 2


def test_consolidation_expires_the_tail_but_never_an_action(tmp_path) -> None:
    env = make_agent(tmp_path)
    thread = "a@s.whatsapp.net"
    ids = backlog(env, thread, 3)
    action_ids = backlog(env, thread, 1, action=True)
    env.agent.config = replace(
        env.config,
        turns=replace(
            env.config.turns,
            max_resume_per_chat=1,
            max_resume_per_minute=4,
            backlog_policy="per_message_then_consolidate",
        ),
    )
    record_resumes(env)

    assert asyncio.run(env.agent.recover()) == 2  # one chat row and the action row

    states = turns_state(env, thread)
    assert states[ids[0]][0] == "running"
    assert states[action_ids[0]][0] == "running"  # the action turn is never consolidated
    assert [states[ids[1]][0], states[ids[2]][0]] == ["expired", "expired"]
    assert env.agent.backlog[thread] == 2


def test_a_turn_resumed_from_drafted_reviews_again_before_sending(tmp_path, monkeypatch) -> None:
    """ADR-0003 §4.2: a crash after `drafted` must re-run review, never send the raw draft."""
    from monoagens.engine import guard

    env = make_agent(tmp_path)
    inbound = inbound_message("halo", thread=DM, message_id="review-1")
    original = env.agent.privacy.review
    calls = {"review": 0, "leak": 0}

    async def review(*args, **kwargs):
        calls["review"] += 1
        if calls["review"] == 1:
            raise RuntimeError("crash after drafted")
        return await original(*args, **kwargs)

    def leak(reply, sources):
        calls["leak"] += 1
        return False

    monkeypatch.setattr(env.agent.privacy, "review", review)
    monkeypatch.setattr(guard, "cross_chat_leak", leak)

    with pytest.raises(RuntimeError, match="crash after drafted"):
        handle(env, inbound)

    turn = turn_row(env, "review-1")
    assert turn["step"] == "drafted"
    assert turn["draft_text"] == "halo juga"
    assert not turn["reviewed_text"]
    assert calls == {"review": 1, "leak": 0}

    handle(env, inbound)

    turn = turn_row(env, "review-1")
    assert turn["status"] == "delivered"
    assert turn["step"] == "delivered"
    assert turn["reviewed_text"] == "halo juga"
    assert calls == {"review": 2, "leak": 1}
    assert [text for _, text, _ in env.gateway.sent] == ["halo juga"]


def test_a_turn_resumed_from_reviewed_never_reviews_again(tmp_path, monkeypatch) -> None:
    """The `reviewed` checkpoint is the gate: resume sends that text unchanged."""
    from monoagens.engine import guard

    env = make_agent(tmp_path)
    inbound = inbound_message("halo", thread=DM, message_id="review-2")
    original_review = env.agent.privacy.review
    original_send = env.agent.delivery.send_reply
    calls = {"review": 0, "leak": 0}
    crashed = {"done": False}

    async def review(*args, **kwargs):
        calls["review"] += 1
        return await original_review(*args, **kwargs)

    def leak(reply, sources):
        calls["leak"] += 1
        return False

    async def send_reply(*args, **kwargs):
        if not crashed["done"]:
            crashed["done"] = True
            raise RuntimeError("crash before send")
        return await original_send(*args, **kwargs)

    monkeypatch.setattr(env.agent.privacy, "review", review)
    monkeypatch.setattr(guard, "cross_chat_leak", leak)
    monkeypatch.setattr(env.agent.delivery, "send_reply", send_reply)

    with pytest.raises(RuntimeError, match="crash before send"):
        handle(env, inbound)

    turn = turn_row(env, "review-2")
    assert turn["step"] == "reviewed"
    assert turn["reviewed_text"] == "halo juga"
    assert calls == {"review": 1, "leak": 1}

    handle(env, inbound)

    turn = turn_row(env, "review-2")
    assert turn["status"] == "delivered"
    assert calls == {"review": 1, "leak": 1}
    assert [text for _, text, _ in env.gateway.sent] == ["halo juga"]


def test_a_progress_emission_goes_through_the_door_with_a_deterministic_id(tmp_path) -> None:
    """ADR-0001 §4.2: progress is delivery, so it takes the door, the origin, and a stable id."""
    from monoagens.engine.turn.progress import _progress_callbacks

    env = make_agent(tmp_path)
    env.gateway.settings = {"interim_updates": True}
    calls: list[tuple] = []
    original = env.agent.dispatch

    async def spy(subject, address, action, *, origin, reason=""):
        calls.append((subject, address, action, origin, reason))
        return await original(subject, address, action, origin=origin, reason=reason)

    env.agent.dispatch = spy
    callbacks = _progress_callbacks(env.agent.dispatch, env.gateway, DM, "turn-7")

    asyncio.run(callbacks["send_interim"]("sedang menulis"))
    asyncio.run(callbacks["send_interim"]("lagi"))

    assert [item[2].id for item in calls] == [
        "turn:turn-7:progress:0",
        "turn:turn-7:progress:1",
    ]
    for _subject, address, action, origin, _reason in calls:
        assert address == origin
        assert address.thread == DM
        assert action.capability == "send_interim"
