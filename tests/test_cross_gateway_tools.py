"""ADR-0001 §4.2/§4.3: the core tools cross bodies, and the envelope keeps every attachment."""

import asyncio
from datetime import datetime

from sqlalchemy import select
from support import RichDirectoryFakeGateway, make_agent, role, specs

from monoagens.engine.access.relay import RelayStore
from monoagens.engine.config import ApprovalConfig
from monoagens.engine.schema import message_media
from monoagens.engine.tools import execute
from monoagens.engine.turn.context import Media, Message

PERSON = "per_owner"
WA = "628999999999@s.whatsapp.net"
CONSOLE_THREAD = "local"


def owner():
    return role("owner", tools=("*",), commands=("*",))


def build(tmp_path):
    console = RichDirectoryFakeGateway(name="console", db=None, targets={}, members={PERSON})
    whatsapp = RichDirectoryFakeGateway(
        name="whatsapp", db=None, targets={"budi": (WA, "Budi")}, members={PERSON}
    )
    env = make_agent(
        tmp_path,
        gateway=console,
        gateways=(console, whatsapp),
        roles={"owner": owner()},
        default_role="owner",
        # The cross-room gate is ADR-0007's test; this file is about the cross-body seam.
        approvals=ApprovalConfig(mode="yolo", confirm_cross_gateway=False),
    )
    whatsapp.db = env.db
    whatsapp.chat_name = lambda thread: "Budi" if thread == WA else str(thread)
    ctx = env.agent.tool_context(
        CONSOLE_THREAD, PERSON, (owner(),), console, person_id=PERSON
    )
    return env, console, whatsapp, ctx


def run(env, name: str, args: dict, ctx) -> str:
    return asyncio.run(execute(specs(env), name, args, ctx))


def attachment_message() -> Message:
    return Message(
        id="wa-att",
        sender_name="Budi",
        sender_id="628999999999",
        when=datetime.now(),
        text="dua lampiran",
        attachments=(
            Media(kind="image", mimetype="image/png", filename="a.png", size=3, ref=b"abc"),
            Media(kind="file", mimetype="text/plain", filename="b.txt", size=2, ref=b"de"),
        ),
        sender_address=WA,
    )


def test_send_message_crosses_from_console_to_whatsapp(tmp_path) -> None:
    env, _console, whatsapp, ctx = build(tmp_path)

    result = run(
        env,
        "send_message",
        {"chat": "budi", "gateway": "whatsapp", "text": "halo dari console", "reason": "test"},
        ctx,
    )

    assert result == "ok: text sent to Budi"
    assert [(thread, text) for thread, text, _reply in whatsapp.sent] == [
        (WA, "halo dari console")
    ]
    assert ctx.sent
    assert ctx.sent[0]["kind"] == "text"


def test_send_message_needs_a_reason_for_an_unarchived_direct_target(tmp_path) -> None:
    env, _console, whatsapp, ctx = build(tmp_path)

    denied = run(
        env,
        "send_message",
        {"chat": "620000000000@s.whatsapp.net", "gateway": "whatsapp", "text": "halo"},
        ctx,
    )
    assert denied.startswith("denied:")
    assert whatsapp.sent == []


def test_a_multi_attachment_message_persists_every_row(tmp_path) -> None:
    env, _console, _whatsapp, _ctx = build(tmp_path)
    message = attachment_message()

    assert env.history.intake(WA, message, PERSON, language="id", gateway="whatsapp") is True

    rows = env.db.fetch_all(
        select(message_media.c.seq, message_media.c.kind).where(
            message_media.c.thread_id == WA,
            message_media.c.message_id == "wa-att",
        )
    )
    assert sorted((int(row["seq"]), str(row["kind"])) for row in rows) == [
        (0, "media"),
        (1, "media"),
    ]
    stored = env.history.find(WA, "wa-att")
    assert stored is not None
    assert [item.filename for item in stored.attachments] == ["a.png", "b.txt"]
    assert stored.media is stored.attachments[0]

    handles = env.history.attachments.of_message(WA, "wa-att")
    assert [item.seq for item in handles] == [0, 1]
    assert all(item.id.startswith("att_") for item in handles)


def test_send_file_forwards_an_attachment_handle(tmp_path) -> None:
    env, _console, whatsapp, ctx = build(tmp_path)
    env.history.intake(WA, attachment_message(), PERSON, language="id", gateway="whatsapp")
    handle = env.history.attachments.of_message(WA, "wa-att")[0]

    result = run(
        env,
        "send_file",
        {
            "path": handle.id,
            "chat": "budi",
            "gateway": "whatsapp",
            "caption": "lampiran",
            "reason": "test",
        },
        ctx,
    )

    assert result.startswith("ok: file sent to Budi")
    assert len(whatsapp.files) == 1
    _thread, path, caption = whatsapp.files[0]
    assert path.is_file()
    assert path.read_bytes() == b"abc"
    assert caption == "lampiran"


def test_thread_history_reads_the_other_body_thread_with_handles(tmp_path) -> None:
    env, _console, _whatsapp, ctx = build(tmp_path)
    env.history.intake(WA, attachment_message(), PERSON, language="id", gateway="whatsapp")

    text = run(env, "thread_history", {"chat": "budi", "gateway": "whatsapp"}, ctx)

    assert "dua lampiran" in text
    assert "att_" in text
    assert WA in ctx.reads


def test_relay_grant_list_revoke_round_trips(tmp_path) -> None:
    env, _console, _whatsapp, _ctx = build(tmp_path)
    ctx = env.agent.tool_context(
        CONSOLE_THREAD, PERSON, (owner(),), env.gateway, person_id=PERSON, approved=True
    )
    store = RelayStore(env.db)

    granted = run(
        env, "relay", {"action": "grant", "to": "per_friend", "mode": "summary", "scope": WA}, ctx
    )
    assert granted.startswith("ok: relay granted to per_friend")
    assert store.get(task_id=f"scope:{WA}", from_person=PERSON, to_person="per_friend")

    listed = run(env, "relay", {"action": "list"}, ctx)
    assert "per_friend" in listed
    assert "summary" in listed

    revoked = run(env, "relay", {"action": "revoke", "to": "per_friend", "scope": WA}, ctx)
    assert revoked.startswith("ok: relay grant to per_friend")
    assert run(env, "relay", {"action": "list"}, ctx).startswith("No relay grant")
