"""Agent contract: one autonomous turn without transport, approvals, memory, or bubbles."""


import asyncio
from dataclasses import replace

from support import (
    FakeGateway,
    FakeLLM,
    FakeMemory,
    build_config,
    database,
    default_soul,
    inbound_message,
    make_agent,
    role,
)

from monoagens.engine.config import ApprovalConfig
from monoagens.engine.schema import people, person_links, thread_people, threads
from monoagens.engine.soul import HumanizeConfig, render_persona
from monoagens.engine.tools import execute

DM = "628111@s.whatsapp.net"
GROUP = "123@g.us"
OTHER = "628222@s.whatsapp.net"


def handle(env, inbound):
    return asyncio.run(env.agent.handle(inbound, env.gateway))


def test_dm_reply_is_delivered_and_recorded(tmp_path) -> None:
    env = make_agent(tmp_path)

    inbound = inbound_message("halo", thread=DM)
    handle(env, inbound)

    assert env.gateway.sent
    thread_id, text, _reply = env.gateway.sent[0]
    assert thread_id == DM
    assert text == "halo juga"
    assert [item.capability for item in env.gateway.dispatched] == ["send_text"]
    assert env.gateway.dispatched[0].address.thread == DM
    ids = [item.id for item in env.windows.get(DM)]
    assert ids[0] == inbound.payload.id
    assert len(ids) == 2
    assert env.llm.calls == 1
    assert env.llm.decisions == 1


def test_the_bot_echo_does_not_mint_an_identity(tmp_path) -> None:
    env = make_agent(tmp_path)
    inbound = inbound_message("balasanku", sender="628567770837")
    echo = replace(inbound, payload=replace(inbound.payload, outgoing=True))

    handle(env, echo)

    assert env.db.fetch_all(people.select()) == []
    assert env.db.fetch_all(person_links.select()) == []


def test_group_without_call_is_ignored_without_llm(tmp_path) -> None:
    env = make_agent(tmp_path)

    handle(env, inbound_message("eh tiket bola", thread=GROUP, dm=False))

    assert env.gateway.sent == []
    assert env.llm.calls == 0


def test_group_mention_replies(tmp_path) -> None:
    env = make_agent(tmp_path)

    handle(
        env,
        inbound_message("monoagens tolong", thread=GROUP, dm=False, mention=True),
    )

    assert env.gateway.sent
    assert env.gateway.sent[0][0] == GROUP


def test_command_skips_decision_and_uses_handler(tmp_path) -> None:
    env = make_agent(tmp_path)

    handle(env, inbound_message("/ping", thread=DM))

    assert env.llm.decisions == 0
    assert "Pong" in env.gateway.sent[0][1]


def test_unknown_command_is_reported(tmp_path) -> None:
    env = make_agent(tmp_path)
    handle(env, inbound_message("/tidakada", thread=DM))
    assert "tidak dikenal" in env.gateway.sent[0][1]


def test_backlog_is_archived_not_answered(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.gateway.backlog_seconds = 1.0
    from datetime import datetime, timedelta

    inbound = inbound_message("lama", thread=DM)
    old = inbound.payload
    object.__setattr__(old, "when", datetime.now() - timedelta(minutes=10))

    handle(env, inbound)

    assert env.gateway.sent == []
    assert env.agent.backlog[DM] == 1


def test_stale_burst_is_not_answered_twice(tmp_path) -> None:
    env = make_agent(tmp_path)
    inbound = inbound_message("halo", thread=DM, message_id="same")

    handle(env, inbound)
    handle(env, inbound)

    assert len(env.gateway.sent) == 1
    assert env.agent.turns.get("same")["status"] == "delivered"


def test_auto_capture_runs_for_memory_role(tmp_path) -> None:
    """The turn no longer captures; intake enqueues the job and the worker claims it."""
    from datetime import datetime

    from support import build_config, database

    from monoagens.engine.jobs.cognition import CognitionWorker
    from monoagens.engine.memory.store import MemoryStore
    from monoagens.engine.stores.history import HistoryStore
    from monoagens.engine.turn.context import Message

    memory = FakeMemory()
    env = make_agent(tmp_path, memory=memory)

    handle(env, inbound_message("aku suka kopi tanpa gula", thread=DM))
    assert memory.captures == []

    config = build_config(tmp_path)
    db = database()
    store = MemoryStore(db, config)
    worker = CognitionWorker(db, config, store)
    history = HistoryStore(db, config.workspace, memories=store, job_sink=worker.queue)
    person = env.agent.identity.resolve("fake", "628111")
    message = Message(
        id="m-capture",
        sender_name="Budi",
        sender_id="628111",
        when=datetime.now(),
        text="aku suka kopi tanpa gula",
        sender_address="628111@s.whatsapp.net",
    )
    assert history.intake(DM, message, person, language="id") is True

    claimed = worker.queue.claim(worker="t", limit=1)
    assert [job.kind for job in claimed] == ["capture"]
    assert claimed[0].scope == f"person:{person}"
    assert claimed[0].payload_ref
    assert worker.queue.claim(worker="u", limit=1) == []


def test_memory_block_and_runtime_reach_prompt(tmp_path) -> None:
    memory = FakeMemory(block="Long-term memory (cross-chat):\n- [person] suka kopi")
    env = make_agent(tmp_path, memory=memory)

    inbound = inbound_message("halo", thread=DM)
    handle(env, inbound)

    person = env.agent.identity.resolve("fake", "628111")
    assert memory.context_calls
    assert memory.context_calls[0]["person_id"] == person
    assert "suka kopi" in env.llm.system
    assert "## Self" in env.llm.system
    assert "Reply in Indonesian" in env.llm.system
    assert "## Fake interface" in env.llm.system
    assert "WhatsApp" not in env.llm.system


def test_soul_examples_reach_the_system_prompt(tmp_path, monkeypatch) -> None:
    env = make_agent(
        tmp_path,
        answer="berat ya. capeknya di bagian mana?",
        soul=replace(
            default_soul(
                examples=({"user": "aku capek", "soul": "capeknya di bagian mana?"},)
            ),
            persona=render_persona(
                default_soul(
                    examples=({"user": "aku capek", "soul": "capeknya di bagian mana?"},)
                )
            ),
        ),
    )

    handle(env, inbound_message("aku capek banget, gak kuat", thread=DM))

    assert "## Dialogue examples" in env.llm.system
    assert "capeknya di bagian mana?" in env.llm.system


def test_humanize_splits_bubbles(tmp_path) -> None:
    env = make_agent(
        tmp_path,
        answer="satu\n\n---\n\ndua",
        humanize=HumanizeConfig(enabled=True, min_delay=0, max_delay=0, between_delay=0),
    )

    inbound = inbound_message("halo", thread=DM)
    handle(env, inbound)

    assert [text for _chat, text, _reply in env.gateway.sent] == ["satu", "dua"]


def test_shell_requires_approval_then_approve_executes(tmp_path) -> None:
    class ToolLLM(FakeLLM):
        async def run(self, system, content, specs, ctx, session=None, deadline=None, **kwargs):
            self.ctx = ctx
            self.call = await execute(
                {handler.name: handler for handler in specs},
                "shell",
                {"command": "printf monoagens-ok"},
                ctx,
            )
            return f"tool: {self.call}"

    env = make_agent(
        tmp_path,
        roles={
            "owner": role("owner", tools=("*",), commands=("*",)),
            "member": role("member", tools=("shell",), commands=("*",)),
        },
        approvals=ApprovalConfig(mode="yolo", sensitive_tools=("shell",)),
    )
    env.agent.llm = ToolLLM()

    handle(env, inbound_message("jalankan shell", thread=DM))

    reply = env.gateway.sent[0][1]
    assert "pending approval #1" in reply
    pending = env.agent.approvals.pending(DM, "628111")
    assert len(pending) == 1

    from monoagens.engine.commands.approve import COMMAND as APPROVE

    inbound = inbound_message("/approve 1", thread=DM)
    ctx = env.agent.command_context(
        DM, "628111", (env.config.roles.get("member"),), env.gateway, "1", inbound
    )
    result = asyncio.run(APPROVE.fn(ctx))

    assert "monoagens-ok" in result
    assert env.agent.approvals.get(1).status == "approved"


def test_sensitive_shell_command_waits_then_approve_executes(tmp_path) -> None:
    class ToolLLM(FakeLLM):
        async def run(self, system, content, specs, ctx, session=None, deadline=None, **kwargs):
            self.ctx = ctx
            self.call = await execute(
                {handler.name: handler for handler in specs},
                "shell",
                {"command": "printf monoagens-ok"},
                ctx,
            )
            return f"tool: {self.call}"

    env = make_agent(
        tmp_path,
        roles={
            "owner": role("owner", tools=("*",), commands=("*",)),
            "member": role("member", tools=("shell",), commands=("*",)),
        },
        approvals=ApprovalConfig(mode="yolo", sensitive_commands=("printf *",)),
    )
    env.agent.llm = ToolLLM()

    handle(env, inbound_message("jalankan shell", thread=DM))

    assert "pending approval #1" in env.gateway.sent[0][1]
    from monoagens.engine.commands.approve import COMMAND as APPROVE

    ctx = env.agent.command_context(
        DM, "628111", (env.config.roles.get("member"),), env.gateway, "1"
    )
    result = asyncio.run(APPROVE.fn(ctx))
    assert "monoagens-ok" in result
    assert env.agent.approvals.get(1).status == "approved"


def test_approve_rejects_other_sender(tmp_path) -> None:
    env = make_agent(tmp_path)
    async def create():
        return await env.agent.approvals.create(
            kind="tool",
            thread_id=DM,
            sender="999",
            tool="shell",
            args={"command": "echo x"},
            summary="shell",
            roles=("member",),
        )

    approval = asyncio.run(create())
    from monoagens.engine.commands.approve import COMMAND as APPROVE

    ctx = env.agent.command_context(
        DM, "628111", (env.config.roles.get("member"),), env.gateway, str(approval.id)
    )
    result = asyncio.run(APPROVE.fn(ctx))
    assert "pengirim lain" in result
    assert env.agent.approvals.get(approval.id).status == "pending"


def test_send_text_tool_wrapper_reports_ok(tmp_path) -> None:
    env = make_agent(tmp_path)
    result = asyncio.run(env.agent.send_text("lain", "halo", gateway=env.gateway))
    assert result.startswith("ok:")
    assert env.gateway.sent[0][0] == "lain"


def test_typing_events_around_turn(tmp_path) -> None:
    env = make_agent(tmp_path)
    inbound = inbound_message("halo", thread=DM)
    handle(env, inbound)
    assert env.gateway.typing_events[0] == (DM, True)
    assert env.gateway.typing_events[-1] == (DM, False)


def test_partial_send_leaves_the_turn_resumable(tmp_path) -> None:
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
    inbound = inbound_message("halo", thread=DM, message_id="burst-1")

    handle(env, inbound)

    assert gateway.calls == 2
    turn = env.agent.turns.get("burst-1")
    assert turn["status"] == "sending"
    assert turn["delivered_chunks"] == 1


def _dm(env, thread_id: str, person_id: str, *, kind: str = "dm", extra: str = "") -> None:
    with env.db.connect() as conn:
        conn.execute(
            threads.insert().values(
                thread_id=thread_id,
                name="",
                reset_ts=0.0,
                kind=kind,
                gateway=env.gateway.name,
            )
        )
        conn.execute(people.insert().values(person_id=person_id, name="Budi"))
        conn.execute(
            thread_people.insert().values(
                thread_id=thread_id, person_id=person_id, last_seen=0.0, interactions=1
            )
        )
        if extra:
            conn.execute(people.insert().values(person_id=extra, name="Ani"))
            conn.execute(
                thread_people.insert().values(
                    thread_id=thread_id, person_id=extra, last_seen=0.0, interactions=1
                )
            )


def _dispute(
    store, subject: str, first: str = "aku suka kopi", second: str = "aku tidak suka kopi"
):
    left = store.write(
        first, scope="person", person_id=subject, thread_id=DM, owner_id=subject,
        subject_id=subject, level=2,
    )
    right = store.write(
        second, scope="person", person_id=subject, thread_id=DM, owner_id=subject,
        subject_id=subject, level=2, via="reflection",
    )
    assert store.mark_disputed(left, right) is True
    return [left, right]


def test_a_dispute_question_goes_to_the_dm_once_per_day(tmp_path) -> None:
    """ADR-0010 §4.7: one question per subject per day, in the subject's own DM."""
    from monoagens.engine.memory.store import MemoryStore

    store = MemoryStore(database(), build_config(tmp_path))
    env = make_agent(tmp_path, memory=store)
    _dm(env, DM, "628111")
    ids = _dispute(store, "628111")

    pair = {"ids": ids, "first": "aku suka kopi", "second": "aku tidak suka kopi"}
    assert asyncio.run(env.agent.privacy.ask_dispute("628111", pair)) == "ok: asked"
    assert [item[0] for item in env.gateway.sent] == [DM]
    assert "kopi" in env.gateway.sent[0][1]
    assert store.disputes_asked_today("628111") == 1

    assert asyncio.run(env.agent.privacy.ask_dispute("628111", pair)) == "skipped: budget"
    assert len(env.gateway.sent) == 1


def test_a_dispute_question_needs_a_single_member_dm(tmp_path) -> None:
    from monoagens.engine.memory.store import MemoryStore

    store = MemoryStore(database(), build_config(tmp_path))
    env = make_agent(tmp_path, memory=store)
    _dm(env, GROUP, "628111", kind="group", extra="628222")

    pair = {"ids": _dispute(store, "628111"), "first": "a", "second": "b"}
    assert asyncio.run(env.agent.privacy.ask_dispute("628111", pair)) == "skipped: no dm"
    assert env.gateway.sent == []


def test_questions_per_day_two_allows_two_pairs(tmp_path) -> None:
    from monoagens.engine.memory.store import MemoryStore

    store = MemoryStore(database(), build_config(tmp_path))
    env = make_agent(tmp_path, memory=store)
    env.agent.config = replace(
        env.config, reflection=replace(env.config.reflection, questions_per_day=2)
    )
    _dm(env, DM, "628111")

    first = _dispute(store, "628111", "aku suka teh", "aku tidak suka teh")
    second = _dispute(store, "628111", "aku suka kopi", "aku tidak suka kopi")
    one = {"ids": first, "first": "a", "second": "b"}
    two = {"ids": second, "first": "c", "second": "d"}
    assert asyncio.run(env.agent.privacy.ask_dispute("628111", one)) == "ok: asked"
    assert asyncio.run(env.agent.privacy.ask_dispute("628111", two)) == "ok: asked"
    assert len(env.gateway.sent) == 2
    assert store.disputes_asked_today("628111") == 2


def test_a_body_without_rich_or_directory_is_counted(tmp_path) -> None:
    """ADR-0014 §4.5: what a body cannot do is counted, not assumed away."""
    from support import FakeGateway

    from monoagens.engine.inbound import Directory
    from monoagens.engine.tools import cross_refusal

    env = make_agent(tmp_path, gateway=FakeGateway())
    assert not isinstance(env.gateway, Directory)

    asyncio.run(
        env.agent.deliver_file(DM, tmp_path / "a.txt", gateway=env.gateway)
    )
    assert env.agent.runtime.rich_missing_total >= 1

    ctx = env.agent.tool_context(
        DM, "628111", (env.config.roles.get("member"),), env.gateway, person_id="628111"
    )
    assert cross_refusal(ctx, OTHER) == "denied: room"
    assert env.agent.runtime.directory_missing_total == 1


def test_status_shows_the_gateway_capability_counters(tmp_path) -> None:
    from monoagens.engine.access.roles import Role
    from monoagens.engine.commands.status import COMMAND as STATUS

    admin = Role("administrator", tools=("*",), commands=("*",), memory_admin=True)
    env = make_agent(tmp_path, roles={"administrator": admin}, default_role="administrator")
    env.agent.runtime.gateway_core_ok = 1
    env.agent.runtime.rich_missing_total = 2
    env.agent.runtime.directory_missing_total = 3
    env.agent.runtime.participants_unknown_total = 4

    ctx = env.agent.command_context(
        DM, "628111", (env.config.roles.get("administrator"),), env.gateway, "1"
    )
    card = asyncio.run(STATUS.fn(ctx))

    assert "Gateways: 1 conforming" in card or "Gateway: 1 sesuai kontrak" in card
    assert "Rich surface missing: 2" in card or "Permukaan rich tidak ada: 2" in card
    assert "Directory missing: 3" in card or "Direktori tidak ada: 3" in card
    assert "Participants unknown: 4" in card or "Peserta tidak diketahui: 4" in card


def test_soul_rule_promotion_receives_the_soul_timezone() -> None:
    """ADR-0008 §4.3: promotion groups rows by the soul's zone; the caller must pass it."""
    from types import SimpleNamespace

    from monoagens.engine.agent import Agent

    calls: list[dict] = []

    class Memory:
        @staticmethod
        def sync_soul_rules(rules):
            return []

        @staticmethod
        def promote_soul_rules(**kwargs):
            calls.append(kwargs)
            return 0

    fake = SimpleNamespace(
        soul=SimpleNamespace(rules=("stay calm",), timezone="Asia/Jakarta"),
        memory=Memory(),
    )

    assert Agent._sync_soul_rules(fake) == "ok"
    assert calls == [{"tz": "Asia/Jakarta"}]
