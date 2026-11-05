"""ADR-0003 §4.3 delivery idempotency: deterministic ids, crash evidence, and reconciliation.

Every test drives the real stores: the outbox rows and the `turn_steps` rows are the contract, not
an in-memory stand-in.
"""


import asyncio
from types import SimpleNamespace

from sqlalchemy import select
from support import RichDirectoryFakeGateway, inbound_message, make_agent, role, specs

from monoagens.engine.inbound import GatewayError, bind_tool_action, reset_tool_action
from monoagens.engine.memory.ledger import OutboundLedger
from monoagens.engine.schema import outbound_messages, turn_steps
from monoagens.engine.stores.turns import STEP_INTERRUPTED, turn_identity
from monoagens.engine.tools import execute, resolve_spec
from monoagens.engine.turn.steps import SideEffectAlreadyRan, TurnSteps

DM = "628111@s.whatsapp.net"
PERSON = "per_test"


def owner():
    return role("owner", tools=("*",), commands=("*",))


def setup(tmp_path, *, gateway=None):
    gateway = gateway or RichDirectoryFakeGateway(
        db=None, targets={DM.casefold(): (DM, "Chat")}, members={PERSON}
    )
    env = make_agent(
        tmp_path,
        gateway=gateway,
        roles={"owner": owner()},
        default_role="owner",
        answer="satu\n\n---\n\ndua",
    )
    inbound = inbound_message("halo", thread=DM, message_id="t-1", person_id=PERSON)
    env.history.intake(
        DM, inbound.payload, PERSON, language="id", gateway=env.gateway.name
    )
    tid = turn_identity(env.gateway.name, DM, "t-1")
    claimed = env.agent.turns.claim(tid, owner="test", generation=0, lease_seconds=60)
    assert claimed is not None
    return env, inbound, tid


def context(env, tid: str):
    return env.agent.tool_context(
        DM, PERSON, (owner(),), env.gateway, person_id=PERSON, turn_id=tid
    )


def test_a_resumed_text_send_never_repeats_a_delivered_chunk(tmp_path) -> None:
    class Flaky(RichDirectoryFakeGateway):
        def __init__(self) -> None:
            super().__init__(targets={DM.casefold(): (DM, "Chat")}, members={PERSON})
            self.calls = 0

        async def send_text(self, thread, text, *, reply=None):
            self.calls += 1
            if self.calls == 2:
                raise GatewayError("boom")
            return await super().send_text(thread, text, reply=reply)

    env, inbound, tid = setup(tmp_path, gateway=Flaky())

    async def first():
        return await env.agent.delivery.send_reply(
            turn_id=tid,
            thread_id=DM,
            gateway=env.gateway,
            inbound=inbound,
            chunks=["satu", "dua"],
            pace=False,
            quote_all=False,
            generation=0,
            partial="skip",
            humanize=env.agent.soul.voice.humanize,
        )

    _ids, _actions, stop = asyncio.run(first())
    assert stop.startswith("failed:")
    ledger = OutboundLedger(env.db)
    assert ledger.status(f"turn:{tid}:0", 0) == "sent"
    assert env.agent.turns.get(tid)["delivered_chunks"] == 1

    _second, _actions, stop = asyncio.run(
        env.agent.delivery.send_reply(
            turn_id=tid,
            thread_id=DM,
            gateway=env.gateway,
            inbound=inbound,
            chunks=["satu", "dua"],
            pace=False,
            quote_all=False,
            generation=0,
            partial="resend",
            humanize=env.agent.soul.voice.humanize,
        )
    )
    assert stop == ""
    assert [text for _, text, _ in env.gateway.sent] == ["satu", "dua"]


def test_a_failed_file_never_resends_the_delivered_text(tmp_path) -> None:
    env, _inbound, tid = setup(tmp_path)
    ctx = context(env, tid)

    token = bind_tool_action(f"turn:{tid}:tool:1")
    try:
        assert asyncio.run(
            execute(
                specs(env),
                "send_message",
                {"chat": DM.casefold(), "text": "laporan"},
                ctx,
            )
        ).startswith("ok:")
    finally:
        reset_tool_action(token)

    # The same step re-runs on a resume: the outbox says `sent`, so nothing leaves again.
    token = bind_tool_action(f"turn:{tid}:tool:1")
    try:
        again = asyncio.run(
            execute(specs(env), "send_message", {"chat": DM.casefold(), "text": "laporan"}, ctx)
        )
    finally:
        reset_tool_action(token)

    assert again.startswith("ok:")
    assert [text for _, text, _ in env.gateway.sent] == ["laporan"]
    rows = env.db.fetch_all(
        select(outbound_messages.c.scope_key, outbound_messages.c.status)
    )
    assert ("sent", f"turn:{tid}:tool:1") in {(row["status"], row["scope_key"]) for row in rows}


def test_a_crash_leaves_running_and_the_next_claim_interrupts_it(tmp_path) -> None:
    env, _inbound, tid = setup(tmp_path)
    writer = TurnSteps(env.agent).recorder(tid, "drafted")
    index = writer.begin("send_message", {"chat": DM}, target="room", approval_class="consent")
    assert index == 0

    claimed = env.agent.turns.claim(tid, owner="next", generation=0, lease_seconds=60)
    assert claimed is not None
    step = env.agent.turns.steps(tid)[0]
    assert step["status"] == STEP_INTERRUPTED
    assert step["finished_at"] is not None
    assert env.agent.turns.side_effect_done(tid) is True

    lookup = TurnSteps(env.agent).lookup(tid)
    try:
        lookup("send_message", {"chat": DM})
    except SideEffectAlreadyRan:
        pass
    else:  # pragma: no cover - the guard must fire
        raise AssertionError("an interrupted side-effecting tool was offered for replay")


def test_scope_reconciliation_covers_chunk_and_tool_rows(tmp_path) -> None:
    env, _inbound, tid = setup(tmp_path)
    ledger = OutboundLedger(env.db)
    for scope in (f"turn:{tid}", f"turn:{tid}:1", f"turn:{tid}:tool:1", "turn:other:x"):
        ledger.begin(scope, 0, gateway="fake", thread_id=DM)

    env.agent.turns.claim(tid, owner="next", generation=0, lease_seconds=60)

    rows = env.db.fetch_all(
        select(outbound_messages.c.scope_key, outbound_messages.c.status)
    )
    states = {row["scope_key"]: row["status"] for row in rows}
    assert states[f"turn:{tid}"] == "unknown"
    assert states[f"turn:{tid}:1"] == "unknown"
    assert states[f"turn:{tid}:tool:1"] == "unknown"
    assert states["turn:other:x"] == "intent"


def test_an_alias_resolves_to_the_canonical_handler(tmp_path) -> None:
    env, _inbound, tid = setup(tmp_path)
    found = resolve_spec(specs(env), "chat_history")
    assert found is not None
    assert found.name == "thread_history"

    writer = TurnSteps(env.agent).recorder(tid, "drafted")
    writer.begin(found.name, {"chat": DM}, target=found.target)
    writer(found.name, {"chat": DM}, "ok", 0.0, 0.0)
    assert env.agent.turns.steps(tid)[0]["tool"] == "thread_history"

    ctx = context(env, tid)
    env.history.add(DM, inbound_message("lama", thread=DM, message_id="m-old").payload)
    text = asyncio.run(
        execute(specs(env), "chat_history", {"chat": DM.casefold()}, ctx)
    )
    assert "lama" in text
    assert [row["step_index"] for row in env.db.fetch_all(select(turn_steps))] == [0]


def test_a_body_alias_becomes_a_body_tool(tmp_path) -> None:
    class Aliased(RichDirectoryFakeGateway):
        @classmethod
        def tool_aliases(cls):
            return {"whatsapp_chat_history": "thread_history"}

    env, _inbound, _tid = setup(
        tmp_path,
        gateway=Aliased(targets={DM.casefold(): (DM, "Chat")}, members={PERSON}),
    )

    assert "whatsapp_chat_history" in specs(env)
    assert specs(env)["whatsapp_chat_history"].name == "whatsapp_chat_history"
    assert "whatsapp_chat_history" not in env.agent.tools.by_name


def test_a_running_step_exists_before_the_tool_runs(tmp_path) -> None:
    """The row is the crash evidence: it exists while the body is still awaiting the tool."""
    env, _inbound, tid = setup(tmp_path)
    seen: list[str] = []

    async def inspect(args, ctx):
        rows = env.db.fetch_all(select(turn_steps.c.status))
        seen.extend(str(row["status"]) for row in rows)
        return "ok"

    from monoagens.engine.registry import Handler

    spec = Handler(
        name="send_message",
        description="test",
        fn=inspect,
        family="write",
        target="room",
        approval_class="none",
        parameters={"type": "object", "properties": {}},
    )
    writer = TurnSteps(env.agent).recorder(tid, "drafted")
    from monoagens.engine import llm as llm_module

    asyncio.run(
        llm_module.LLM._tools(
            None,
            [SimpleNamespace(id="c1", name="send_message", arguments="{}")],
            {"send_message": spec},
            context(env, tid),
            [],
            None,
            0,
            writer,
            None,
            None,
            tid,
        )
    )
    assert seen == ["running"]
    assert env.agent.turns.steps(tid)[0]["status"] == "executed"
