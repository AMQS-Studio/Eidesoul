"""Cross-room guard contract: a verbatim quote from another room must never be sent."""


import asyncio
from datetime import datetime

from support import inbound_message, make_agent, role, specs
from test_whatsapp_gateway import make_gateway

from eidesoul.engine import guard
from eidesoul.engine.i18n import t
from eidesoul.engine.tools import execute
from eidesoul.engine.turn.context import Message
from eidesoul.gateways.whatsapp import directory

TARGET = "628999999999@s.whatsapp.net"
SPAN = "alpha beta gamma delta epsilon zeta eta theta"


def test_cross_chat_leak_detects_an_eight_word_span() -> None:
    reply = f"katanya begini: {SPAN} lalu selesai"

    assert guard.cross_chat_leak(reply, [SPAN]) is True
    assert guard.cross_chat_leak("alpha beta gamma delta epsilon zeta eta", [SPAN]) is False


def test_cross_chat_leak_ignores_a_paraphrase() -> None:
    reply = "Menurut obrolan itu, rencananya dibahas lagi minggu depan."

    assert guard.cross_chat_leak(reply, ["rapat anggaran dipindah ke hari kamis pagi"]) is False


def archive_env(tmp_path):
    gateway = make_gateway(tmp_path)
    directory._upsert_contact(gateway.db, TARGET, "Budi", "")
    # ADR-0007 §4.5: the requester must be provable in the target room.
    gateway.participants = lambda thread: {"628111"} if thread == TARGET else None
    env = make_agent(
        tmp_path,
        gateway=gateway,
        roles={"owner": role("owner", tools=("chat_history",), commands=("*",))},
        default_role="owner",
    )
    env.history.add(
        TARGET,
        Message(
            id="b1",
            sender_name="Budi",
            sender_id="628999999999",
            when=datetime(2026, 1, 1, 10),
            text=SPAN,
            author="manual_owner",
            visibility=3,
        ),
    )
    return env


def context(env, thread_id: str = "111@g.us"):
    return env.agent.tool_context(
        thread_id, "628111", (env.config.roles.get("owner"),), env.gateway
    )


def test_cross_room_read_fills_cross_reads(tmp_path) -> None:
    env = archive_env(tmp_path)
    ctx = context(env)

    text = asyncio.run(
        execute(specs(env), "chat_history", {"chat": "budi"}, ctx)
    )

    assert SPAN in text
    assert any(SPAN in item for item in ctx.cross_reads)
    assert ctx.reads == {TARGET}


def test_current_chat_read_does_not_fill_cross_reads(tmp_path) -> None:
    env = archive_env(tmp_path)
    env.history.add(
        "111@g.us",
        Message(
            id="c1",
            sender_name="Budi",
            sender_id="628111",
            when=datetime(2026, 1, 1, 10),
            text="rapat jam tiga",
        ),
    )
    ctx = context(env)

    text = asyncio.run(execute(specs(env), "chat_history", {}, ctx))

    assert "rapat jam tiga" in text
    assert ctx.cross_reads == []


def test_agent_replaces_a_verbatim_cross_room_reply(tmp_path) -> None:
    env = make_agent(tmp_path, answer=SPAN)

    async def run(system, content, specs, ctx, session=None, deadline=None, **kwargs):
        ctx.cross_reads.append(SPAN)
        return SPAN

    env.llm.run = run
    asyncio.run(env.agent.handle(inbound_message("halo", thread="chat"), env.gateway))

    assert env.gateway.sent[-1][1] == t("agent.cross_chat_blocked", env.config.soul.language)
