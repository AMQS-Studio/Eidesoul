"""Cross-room guard contract: a verbatim quote from another room must never be sent."""


import asyncio
from datetime import datetime

from support import inbound_message, make_agent, role, specs
from test_whatsapp_gateway import make_gateway

from monoagens.engine import guard
from monoagens.engine.i18n import t
from monoagens.engine.tools import execute
from monoagens.engine.turn.context import Message
from monoagens.gateways.whatsapp import directory

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
        roles={"owner": role("owner", tools=("thread_history",), commands=("*",))},
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
        execute(specs(env), "thread_history", {"chat": "budi"}, ctx)
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

    text = asyncio.run(execute(specs(env), "thread_history", {}, ctx))

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


def own_private_env(tmp_path):
    """The same person, with their own Telegram-style DM archived beside this thread."""
    env = archive_env(tmp_path)
    env.history.add(
        "tg-999",
        Message(
            id="t1",
            sender_name="Budi",
            sender_id="628111",
            when=datetime(2026, 1, 1, 9),
            text=SPAN,
        ),
    )
    return env


def test_reading_the_senders_own_private_chat_is_not_a_cross_leak(tmp_path) -> None:
    env = own_private_env(tmp_path)
    ctx = context(env)

    text = asyncio.run(
        execute(specs(env), "thread_history", {"chat": "tg-999"}, ctx)
    )

    assert SPAN in text
    assert "own private chat" in text
    assert "never leak it" not in text
    # continuity across the linked identity: the reply may quote the person's own words
    assert ctx.cross_reads == []
    assert guard.cross_chat_leak(SPAN, ctx.cross_reads) is False
    assert ctx.reads == {"tg-999"}


def test_another_persons_private_chat_stays_guarded(tmp_path) -> None:
    env = archive_env(tmp_path)
    env.history.add(
        "tg-other",
        Message(
            id="t2",
            sender_name="Budi",
            sender_id="628999999999",
            when=datetime(2026, 1, 1, 9),
            text=SPAN,
        ),
    )
    # the requester never wrote there and the body cannot prove them: fail closed
    ctx = context(env, thread_id="628111@s.whatsapp.net")
    text = asyncio.run(
        execute(specs(env), "thread_history", {"chat": "tg-other"}, ctx)
    )

    assert text == "denied: room"
    assert SPAN not in text
    assert ctx.cross_reads == []


def test_the_turn_frame_carries_the_senders_linked_surfaces() -> None:
    from types import SimpleNamespace

    from monoagens.engine.turn.loop import _surfaces

    class Identity:
        def links(self, person_id: str):
            assert person_id == "per_aaaaaaaaaa"
            return [
                {"gateway": "console", "address": "local"},
                {"gateway": "telegram", "address": "12345"},
            ]

    runtime = SimpleNamespace(identity=Identity())
    direct = SimpleNamespace(actor="per_aaaaaaaaaa", hints={"dm": True})
    shared = SimpleNamespace(actor="per_aaaaaaaaaa", hints={"dm": False})

    assert "telegram (12345)" in _surfaces(runtime, direct)
    assert "12345" not in _surfaces(runtime, shared)


def test_a_missing_identity_link_lookup_never_blocks_the_turn() -> None:
    from types import SimpleNamespace

    from monoagens.engine.turn.loop import _surfaces

    class Broken:
        def links(self, person_id: str):
            raise RuntimeError("database is down")

    runtime = SimpleNamespace(identity=Broken())
    inbound = SimpleNamespace(actor="per_aaaaaaaaaa", hints={})
    assert _surfaces(runtime, inbound) == ""
