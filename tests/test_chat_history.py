"""Cross-room chat_history contract: read another chat's archive, mark it read, never leak."""


import asyncio
from datetime import datetime

from support import make_agent, role
from test_whatsapp_gateway import make_gateway

from eidesoul.engine.tools import execute
from eidesoul.engine.turn.context import Message
from eidesoul.gateways.whatsapp import directory

TARGET = "628999999999@s.whatsapp.net"


def message(mid: str, text: str, **kwargs) -> Message:
    return Message(
        id=mid,
        sender_name=kwargs.get("sender_name", "Budi"),
        sender_id=kwargs.get("sender_id", "628999999999"),
        when=datetime(2026, 1, 1, 10),
        text=text,
        author=kwargs.get("author", "unknown"),
        visibility=kwargs.get("visibility", 2),
    )


def env(tmp_path):
    gateway = make_gateway(tmp_path)
    directory._upsert_contact(gateway.db, TARGET, "Budi", "")
    # ADR-0007 §4.5: the requester must be provable in the target room before a cross read.
    gateway.participants = lambda thread: {"628111"} if thread == TARGET else None
    return make_agent(
        tmp_path,
        gateway=gateway,
        roles={
            "owner": role(
                "owner", tools=("chat_history", "send_to"), commands=("*",)
            )
        },
        default_role="owner",
    )


def context(env, thread_id: str = "111@g.us"):
    return env.agent.tool_context(
        thread_id, "628111", (env.config.roles.get("owner"),), env.gateway
    )


def run(env, args: dict, ctx):
    return asyncio.run(execute(env.agent.tools.by_name, "chat_history", args, ctx))


def test_reads_current_chat_by_default(tmp_path) -> None:
    setup = env(tmp_path)
    setup.history.add("111@g.us", message("a", "rapat jam tiga"))
    ctx = context(setup)

    text = run(setup, {}, ctx)

    assert "rapat jam tiga" in text
    assert "role=unknown vis=L2" in text
    assert ctx.reads == set()


def test_reads_another_chat_by_contact_name_and_marks_it(tmp_path) -> None:
    setup = env(tmp_path)
    setup.history.add(TARGET, message("b", "aib owner", author="manual_owner", visibility=3))
    ctx = context(setup)

    text = run(setup, {"chat": "budi"}, ctx)

    assert text.startswith(
        f'Archive "Budi" ({TARGET}; internal cross-room read, never leak it):'
    )
    assert "role=manual_owner vis=L3" in text
    assert "aib owner" in text
    assert ctx.reads == {TARGET}


def test_unreadable_target_never_marks_the_target_read(tmp_path) -> None:
    setup = env(tmp_path)
    ctx = context(setup)

    text = run(setup, {"chat": "budi"}, ctx)

    assert "Its content is unreadable" in text
    assert "never send blind" in text
    assert ctx.reads == set()


def test_missing_target_is_an_error(tmp_path) -> None:
    setup = env(tmp_path)
    ctx = context(setup)

    text = run(setup, {"chat": "tidak-ada@g.us"}, ctx)

    assert text.startswith("error:")
    assert "is not in the archive" in text
    assert ctx.reads == set()


def test_without_gateway_only_the_current_chat_is_readable(tmp_path) -> None:
    setup = env(tmp_path)
    ctx = setup.agent.tool_context(
        "111@g.us", "628111", (setup.config.roles.get("owner"),), None
    )

    text = run(setup, {"chat": "budi"}, ctx)

    assert text.startswith("error:")
    assert "no gateway available" in text


def test_deleted_rows_show_the_archived_text_with_a_marker(tmp_path) -> None:
    setup = env(tmp_path)
    setup.history.add("111@g.us", message("a", "rahasia owner"))
    assert setup.history.mark_deleted(
        "111@g.us", "a", "628999999999@s.whatsapp.net", "Ani"
    ) is True
    ctx = context(setup)

    text = run(setup, {}, ctx)

    assert "[deleted] rahasia owner" in text


def test_unreadable_rows_read_as_a_note(tmp_path) -> None:
    setup = env(tmp_path)
    setup.history.add(
        "111@g.us",
        Message(
            id="b",
            sender_name="Budi",
            sender_id="628999999999",
            when=datetime(2026, 1, 1, 10),
            text="",
            unreadable=True,
        ),
    )
    ctx = context(setup)

    text = run(setup, {}, ctx)

    assert "(message undecryptable on this device)" in text
    assert "role=unknown vis=L2, undecryptable" in text
