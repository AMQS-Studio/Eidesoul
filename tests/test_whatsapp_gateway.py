"""WhatsApp gateway contract: protobuf ingress, mentions, rendering, and send routing."""


import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from neonize.proto.Neonize_pb2 import Message as ProtoMessage
from support import build_config, database

from monoagens.engine.config import GatewaySettings
from monoagens.engine.inbound import GatewayError, Percept
from monoagens.engine.stores.history import HistoryStore
from monoagens.engine.turn.context import Message, WorkingMemory
from monoagens.gateways.whatsapp import directory, ingress
from monoagens.gateways.whatsapp.gateway import SENT_TTL, WhatsAppGateway

BOT = "628567770837"


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def send_message(self, jid, payload):
        self.calls.append(("message", payload))
        return SimpleNamespace(ID="sent-1")

    async def reply_message(self, payload, ev):
        self.calls.append(("reply", payload, ev))
        return SimpleNamespace(ID="sent-2")

    async def send_sticker(self, jid, path):
        self.calls.append(("sticker", Path(path).name))
        return SimpleNamespace(ID="sticker-1")

    async def send_image(self, jid, path, caption=None):
        self.calls.append(("image", Path(path).name, caption))
        return SimpleNamespace(ID="image-1")

    async def send_document(self, jid, path, caption=None, filename=None):
        self.calls.append(("document", Path(path).name, caption))
        return SimpleNamespace(ID="doc-1")


def make_gateway(tmp_path) -> WhatsAppGateway:
    config = build_config(tmp_path)
    db = database()
    history = HistoryStore(db)
    windows = WorkingMemory(store=history)
    gateway = WhatsAppGateway(
        config,
        db,
        history,
        windows,
        settings=GatewaySettings("whatsapp", True, {}),
    )
    gateway.me = SimpleNamespace(
        JID=SimpleNamespace(User=BOT), LID=SimpleNamespace(User="1234567890")
    )
    return gateway


def group_event(
    text: str,
    *,
    mentioned: tuple[str, ...] = (),
    participant: str = "",
    chat: str = "111@g.us",
    sender: str = "628222222222",
) -> ProtoMessage:
    ev = ProtoMessage()
    ev.Info.ID = "wamid-1"
    ev.Info.Timestamp = 1_700_000_000
    ev.Info.Pushname = "Budi"
    ev.Info.MessageSource.Chat.User, _, ev.Info.MessageSource.Chat.Server = chat.partition("@")
    ev.Info.MessageSource.Sender.User = sender
    ev.Info.MessageSource.Sender.Server = "s.whatsapp.net"
    ev.Message.extendedTextMessage.text = text
    if mentioned:
        ev.Message.extendedTextMessage.contextInfo.mentionedJID.extend(mentioned)
    if participant:
        ev.Message.extendedTextMessage.contextInfo.participant = participant
    return ev


def test_event_to_inbound_dm_and_person(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    ev = group_event("halo", chat="628222222222@s.whatsapp.net")

    inbound = ingress.event_to_inbound(ev, gateway)

    assert inbound is not None
    assert inbound.thread == "628222222222@s.whatsapp.net"
    assert inbound.hints.get("dm") is True
    assert inbound.actor == "628222222222"  # the core assigns the person id, not the gateway
    assert inbound.kind == "message"
    assert inbound.payload.text == "halo"
    assert inbound.payload.sender_name == "Budi"


def test_mention_and_reply_flags_come_from_payload(tmp_path) -> None:
    gateway = make_gateway(tmp_path)

    plain = ingress.event_to_inbound(group_event("@monoagens tolong"), gateway)
    asserted = ingress.event_to_inbound(
        group_event("pagi", mentioned=(f"{BOT}@s.whatsapp.net",)), gateway
    )
    reply = ingress.event_to_inbound(
        group_event("iya", participant=f"{BOT}@s.whatsapp.net"), gateway
    )

    assert plain.hints.get("mention") is False
    assert asserted.hints.get("mention") is True
    assert reply.hints.get("reply") is True


def test_lid_sender_maps_to_the_pn_address(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    directory.set_lid(gateway.db, "999@lid", "628999999999@s.whatsapp.net")
    ev = group_event("halo")
    ev.Info.MessageSource.Sender.Server = "lid"
    ev.Info.MessageSource.Sender.User = "999"

    inbound = ingress.event_to_inbound(ev, gateway)

    assert inbound is not None
    assert inbound.actor == "628999999999"
    assert inbound.payload.sender_address == "628999999999@s.whatsapp.net"
    assert inbound.payload.sender_id == "628999999999"


def test_quote_is_read_from_context_info(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    ev = group_event("iya")
    context = ev.Message.extendedTextMessage.contextInfo
    context.stanzaID = "quoted-1"
    context.quotedMessage.conversation = "mau kubantu?"

    inbound = ingress.event_to_inbound(ev, gateway)

    assert inbound is not None
    assert inbound.payload.quote is not None
    assert inbound.payload.quote.text == "mau kubantu?"


def test_command_body_strips_only_bot_mentions() -> None:
    from monoagens.engine.turn.commands import command_body

    assert command_body(f"@{BOT} /ping", "monoagens", {BOT}) == "/ping"
    assert command_body("@Budi /ping", "monoagens", {BOT}) == "@Budi /ping"
    assert command_body("@MonoAgens /status", "monoagens", set()) == "/status"


def test_render_converts_markdown_but_keeps_fence(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    text = "## Judul\n**halo**\n- satu\n<reasoning>diam</reasoning>\n```py\n- kode\n```"

    rendered = gateway.render(text)

    assert rendered.startswith("*Judul*\n*halo*\n• satu")
    assert "- kode" in rendered
    assert "```py" in rendered
    assert "reasoning" not in rendered


def test_prompt_section_include_handles(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    hints = gateway.prompt_section()
    assert "WhatsApp formatting" in hints
    assert BOT in hints
    assert "Your handles" in hints
    assert "mention=@" in hints


def test_send_text_resolves_mentions_and_quotes(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    client = FakeClient()
    gateway.client = client
    gateway.windows.append(
        "111@g.us",
        Message(
            id="a",
            sender_name="Ani",
            sender_id="628222222222",
            when=__import__("datetime").datetime.now(),
            text="halo",
            sender_address="628222222222@s.whatsapp.net",
        ),
    )

    asyncio.run(gateway.send_text("111@g.us", "@628222222222 halo"))

    _verb, payload = client.calls[0]
    assert payload.text == "@628222222222 halo"
    assert list(payload.contextInfo.mentionedJID) == ["628222222222@s.whatsapp.net"]

    raw = group_event("halo")
    inbound = Percept(
        source="whatsapp",
        thread="111@g.us",
        actor="1",
        kind="message",
        payload=Message("q", "Budi", "1", __import__("datetime").datetime.now(), "halo"),
        hints={"dm": False},
        raw=raw,
    )
    asyncio.run(gateway.send_text("111@g.us", "balasan", reply=inbound))
    assert client.calls[1][0] == "reply"


def test_send_file_routes_sticker_photo_document(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    client = FakeClient()
    gateway.client = client
    workspace = gateway.config.workspace
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "cat.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x")
    (workspace / "doc.txt").write_text("isi", encoding="utf-8")
    side = (100).to_bytes(3, "little")
    webp = bytearray(b"RIFF" + (100).to_bytes(4, "little") + b"WEBP" + b"VP8X")
    webp += (10).to_bytes(4, "little") + bytes([0x12, 0, 0, 0]) + side + side
    webp += bytes(20)
    (workspace / "sticker.webp").write_bytes(bytes(webp))

    assert asyncio.run(gateway.send_file("111@g.us", workspace / "sticker.webp")).startswith(
        "sticker-"
    )
    assert asyncio.run(gateway.send_file("111@g.us", workspace / "cat.png", "cap")).startswith(
        "image-"
    )
    assert asyncio.run(gateway.send_file("111@g.us", workspace / "doc.txt")).startswith("doc-")
    assert [call[0] for call in client.calls] == ["sticker", "image", "document"]


def test_send_file_rejects_outside_workspace(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    gateway.client = FakeClient()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(GatewayError, match="workspace"):
        asyncio.run(gateway.send_file("111@g.us", outside))


def test_resolve_target_never_falls_back_to_single_contact(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    directory._upsert_contact(gateway.db, "628999999999@s.whatsapp.net", "Budi", "")
    assert directory.contacts(gateway.db) == [("628999999999@s.whatsapp.net", "Budi")]

    target, candidates = gateway.resolve_target("tidak-ada@g.us")

    assert target is None
    assert candidates == []


def test_resolve_target_exact_and_unique_substring(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    directory.set_chat_name(gateway.db, "111@g.us", "Panitia Logistik")
    directory._upsert_contact(gateway.db, "628999999999@s.whatsapp.net", "Budi", "")

    assert gateway.resolve_target("111@g.us")[0] == ("111@g.us", "Panitia Logistik")
    assert gateway.resolve_target("logistik")[0][0] == "111@g.us"
    assert gateway.resolve_target("budi")[0][0] == "628999999999@s.whatsapp.net"


def test_self_message_is_manual_unless_the_bot_sent_it(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    ev = group_event("diketik owner", sender=BOT)
    ev.Info.MessageSource.IsFromMe = True

    manual = ingress.event_to_inbound(ev, gateway)
    assert manual is not None
    assert manual.payload.author == "manual_owner"
    assert manual.payload.sender_name == "Budi"

    gateway.remember_sent("wamid-1")
    bot = ingress.event_to_inbound(ev, gateway)
    assert bot is not None
    assert bot.payload.author == "bot"
    assert bot.payload.sender_name == gateway.config.soul.name


def test_remember_sent_keeps_only_live_ids(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    gateway.remember_sent("")
    gateway._sent["old"] = time.monotonic() - SENT_TTL - 1

    gateway.remember_sent("fresh")

    assert gateway.is_bot_message("fresh") is True
    assert gateway.is_bot_message("old") is False
    assert "old" not in gateway._sent


def test_self_jid_ignores_lid(tmp_path) -> None:
    gateway = make_gateway(tmp_path)

    assert ingress.self_jid(gateway.me) == f"{BOT}@s.whatsapp.net"
    assert ingress.self_jid(None) == ""


def seed_sender(gateway, chat: str = "111@g.us", **kwargs) -> Message:
    entry = Message(
        id=kwargs.pop("id", "a"),
        sender_name=kwargs.pop("sender_name", "Budi"),
        sender_id=kwargs.pop("sender_id", "628222222222"),
        when=__import__("datetime").datetime.now(),
        text=kwargs.pop("text", "halo"),
        sender_address=kwargs.pop("sender_address", "628222222222@s.whatsapp.net"),
        **kwargs,
    )
    gateway.windows.append(chat, entry)
    return entry


def test_resolve_mentions_never_fabricates_a_tag(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    seed_sender(gateway)
    seed_sender(gateway, id="b", sender_name="Ani", sender_id="628333333333",
                sender_address="628333333333@s.whatsapp.net")

    body, mentions = gateway._resolve_mentions("@628222222222 halo", "111@g.us")
    by_name, name_mentions = gateway._resolve_mentions("@Ani halo", "111@g.us")
    unknown, unknown_mentions = gateway._resolve_mentions("@+7 3041086251252 halo", "111@g.us")

    assert body == "@628222222222 halo"
    assert mentions == ["628222222222@s.whatsapp.net"]
    assert by_name == "@628333333333 halo"
    assert name_mentions == ["628333333333@s.whatsapp.net"]
    assert unknown == "@+7 3041086251252 halo"
    assert unknown_mentions == []


def test_resolve_mentions_leaves_ambiguous_names_alone(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    seed_sender(gateway, id="a", sender_name="Ani", sender_id="628222222222")
    seed_sender(gateway, id="b", sender_name="Ani", sender_id="628333333333",
                sender_address="628333333333@s.whatsapp.net")

    body, mentions = gateway._resolve_mentions("@Ani halo", "111@g.us")

    assert body == "@Ani halo"
    assert mentions == []


def test_sender_lid_with_alt_resolves_to_pn(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    ev = group_event("halo")
    ev.Info.MessageSource.Sender.Server = "lid"
    ev.Info.MessageSource.Sender.User = "123"
    ev.Info.MessageSource.SenderAlt.User = "628999999999"
    ev.Info.MessageSource.SenderAlt.Server = "s.whatsapp.net"

    inbound = ingress.event_to_inbound(ev, gateway)

    assert inbound is not None
    assert inbound.payload.sender_address == "628999999999@s.whatsapp.net"
    assert inbound.payload.sender_kind == "person"
    assert inbound.payload.sender_id == "628999999999"


def view_once_event() -> ProtoMessage:
    ev = group_event("")
    ev.Message.viewOnceMessageV2.message.conversation = "sekali lihat"
    return ev


def test_views_once_detects_the_wrapper_and_flags_the_inbound(tmp_path) -> None:
    gateway = make_gateway(tmp_path)

    inbound = ingress.event_to_inbound(view_once_event(), gateway)

    assert ingress.views_once(view_once_event().Message) is True
    assert ingress.views_once(group_event("halo").Message) is False
    assert inbound is not None
    assert inbound.payload.view_once is True
    assert inbound.payload.text == "sekali lihat"


def revoke_event(target: str, sender: str, *, pushname: str = "Budi") -> ProtoMessage:
    from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import ProtocolMessage

    ev = group_event("")
    ev.Info.ID = "revoke-1"
    ev.Info.Pushname = pushname
    ev.Info.MessageSource.Chat.User, _, ev.Info.MessageSource.Chat.Server = (
        "111@g.us".partition("@")
    )
    ev.Info.MessageSource.Sender.User = sender
    ev.Info.MessageSource.Sender.Server = "s.whatsapp.net"
    ev.Message.protocolMessage.type = ProtocolMessage.REVOKE
    ev.Message.protocolMessage.key.ID = target
    return ev


def test_revoke_target_reads_only_revoke_protocol_messages(tmp_path) -> None:
    gateway = make_gateway(tmp_path)

    revoked = ingress.revoke_target(revoke_event("t1", "628222222222"), gateway)

    assert revoked == ("t1", "628222222222@s.whatsapp.net")
    assert ingress.revoke_target(group_event("halo"), gateway) is None


STUBS: list[StubClient] = []


class StubClient:
    """NewAClient replacement: records handlers, then lets run() finish."""

    def __init__(self, *args, **kwargs) -> None:
        self.handlers: dict[object, object] = {}
        self.me = None
        STUBS.append(self)

    def event(self, kind):
        def register(fn):
            self.handlers[kind] = fn
            return fn

        return register

    async def connect(self) -> None:
        return None

    async def idle(self) -> None:
        return None

    async def stop(self) -> None:
        return None


def drive_run(tmp_path, monkeypatch):
    from monoagens.gateways.whatsapp import gateway as gateway_module

    gateway = make_gateway(tmp_path)
    monkeypatch.setattr(gateway_module, "NewAClient", StubClient)
    handled: list[Percept] = []

    async def handle(inbound, gw) -> None:
        handled.append(inbound)

    asyncio.run(gateway.run(SimpleNamespace(handle=handle)))
    return gateway, STUBS[-1], handled


def test_gateway_marks_revoked_rows_and_stays_silent(tmp_path, monkeypatch) -> None:
    gateway, client, handled = drive_run(tmp_path, monkeypatch)
    seed_sender(gateway, id="t1", text="rahasia")
    seed_sender(gateway, id="t2", text="isi lain", sender_name="Citra",
                sender_id="628333333333", sender_address="628333333333@s.whatsapp.net")

    asyncio.run(client.handlers[ProtoMessage](client, revoke_event("t1", "628222222222")))
    asyncio.run(
        client.handlers[ProtoMessage](client, revoke_event("t2", "628222222222", pushname="Budi"))
    )
    asyncio.run(client.handlers[ProtoMessage](client, revoke_event("hilang", "628222222222")))

    rows = {entry.id: entry for entry in gateway.windows.get("111@g.us")}
    assert rows["t1"].deleted is True
    assert rows["t1"].deleted_admin is False
    assert rows["t2"].deleted is True
    assert rows["t2"].deleted_admin is True
    assert handled == []
    assert "hilang" not in rows


def test_unreadable_stanza_becomes_a_placeholder_row(tmp_path, monkeypatch) -> None:
    from monoagens.gateways.whatsapp.gateway import UndecryptableMessageEv

    gateway, client, handled = drive_run(tmp_path, monkeypatch)
    ev = UndecryptableMessageEv()
    ev.Info.ID = "u1"
    ev.Info.Timestamp = 1_700_000_000
    ev.Info.Pushname = "Budi"
    ev.Info.MessageSource.Chat.User = "111"
    ev.Info.MessageSource.Chat.Server = "g.us"
    ev.Info.MessageSource.Sender.User = "628222222222"
    ev.Info.MessageSource.Sender.Server = "s.whatsapp.net"
    ev.IsUnavailable = True

    asyncio.run(client.handlers[UndecryptableMessageEv](client, ev))

    rows = [entry for entry in gateway.windows.get("111@g.us") if entry.id == "u1"]
    assert len(rows) == 1
    assert rows[0].unreadable is True
    assert rows[0].text == ""
    assert rows[0].sender_address == "628222222222@s.whatsapp.net"
    assert rows[0].sender_kind == "person"
    assert handled == []


def test_unreadable_stanza_ignores_available_and_broadcast(tmp_path) -> None:
    from monoagens.gateways.whatsapp.gateway import UndecryptableMessageEv

    gateway = make_gateway(tmp_path)
    ev = UndecryptableMessageEv()
    ev.Info.ID = "u2"
    ev.Info.MessageSource.Chat.User = "status"
    ev.Info.MessageSource.Chat.Server = "broadcast"
    ev.Info.MessageSource.Sender.User = "628222222222"

    assert ingress.unreadable_message(ev, gateway) is None


def test_the_whatsapp_gateway_is_core_rich_and_directory(tmp_path) -> None:
    from monoagens.engine.inbound import Directory, GatewayCore, Rich

    gateway = make_gateway(tmp_path)

    assert isinstance(gateway, GatewayCore)
    assert isinstance(gateway, Rich)
    assert isinstance(gateway, Directory)


def test_participants_proves_a_linked_dm_and_refuses_a_group(tmp_path) -> None:
    from monoagens.engine.access.identity import IdentityStore

    gateway = make_gateway(tmp_path)
    person = IdentityStore(gateway.db).resolve("whatsapp", "628999999999")

    assert gateway.participants("628999999999@s.whatsapp.net") == {person}
    assert gateway.participants("628111@s.whatsapp.net") is None
    assert gateway.participants("111@g.us") is None
