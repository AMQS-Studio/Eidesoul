"""Runbook regressions: WhatsApp history dedupe, LID addresses, media, and group policy."""

from datetime import datetime
from types import SimpleNamespace

from neonize.proto.Neonize_pb2 import Message as ProtoMessage
from neonize.proto.waHistorySync.WAWebProtobufsHistorySync_pb2 import HistorySync
from support import build_config, database

from monoagens.engine.access.identity import IdentityStore
from monoagens.engine.access.roles import Role
from monoagens.engine.config import GatewaySettings
from monoagens.engine.stores.history import HistoryStore
from monoagens.engine.turn.context import Media, Message, WorkingMemory
from monoagens.gateways.whatsapp import directory, history_sync, ingress
from monoagens.gateways.whatsapp.gateway import WhatsAppGateway
from monoagens.gateways.whatsapp.policy import Access

BOT = "628567770837"


def make_gateway(tmp_path, *, roles=None) -> WhatsAppGateway:
    config = build_config(tmp_path, roles=roles or {"member": Role("member")})
    db = database()
    history = HistoryStore(db)
    gateway = WhatsAppGateway(
        config, db, history, WorkingMemory(store=history),
        settings=GatewaySettings("whatsapp", True, {}),
    )
    gateway.me = SimpleNamespace(
        JID=SimpleNamespace(User=BOT), LID=SimpleNamespace(User="1234567890")
    )
    return gateway


def group_event(text: str = "halo", *, sender: str = "628222222222") -> ProtoMessage:
    ev = ProtoMessage()
    ev.Info.ID = "wamid-1"
    ev.Info.Timestamp = 1_700_000_000
    ev.Info.Pushname = "Budi"
    ev.Info.MessageSource.Chat.User = "111"
    ev.Info.MessageSource.Chat.Server = "g.us"
    ev.Info.MessageSource.Sender.User = sender
    ev.Info.MessageSource.Sender.Server = "s.whatsapp.net"
    ev.Message.extendedTextMessage.text = text
    return ev


def archived(mid: str, *, mime: str = "image/jpeg") -> Message:
    media = Media(
        kind="image", mimetype=mime, filename=f"{mid}.jpg", size=3, ref=b"img"
    )
    return Message(
        id=mid, sender_name="Budi", sender_id="628222",
        when=datetime(2026, 1, 1, 10), text="", media=media,
    )


def test_normalize_address_collapses_lid_and_pn(tmp_path) -> None:
    gateway = make_gateway(tmp_path)

    assert gateway.normalize_address("+62 851-1124-2960") == "6285111242960"
    assert gateway.normalize_address("123@lid") == "123"
    assert gateway.normalize_address("123@s.whatsapp.net") == "123"
    assert gateway.normalize_address("456@g.us") == "456"


def test_paired_owner_links_phone_and_lid(tmp_path) -> None:
    gateway = make_gateway(
        tmp_path,
        roles={"administrator": Role("administrator", users=(BOT,)), "member": Role("member")},
    )

    person = gateway.link_paired_owner()

    assert person
    assert IdentityStore(gateway.db).resolve("whatsapp", BOT) == person
    assert IdentityStore(gateway.db).resolve("whatsapp", "1234567890") == person
    assert directory.pn_jid(gateway.db, "1234567890@lid") == f"{BOT}@s.whatsapp.net"


def test_mention_policy_honours_reply_to_bot() -> None:
    access = Access(dm=True, groups="mention", reply_to_bot=True)

    assert access.refusal({"group": True, "mention": True, "reply": False}) == ""
    assert access.refusal({"group": True, "mention": False, "reply": True}) == ""
    assert Access(dm=True, groups="mention", reply_to_bot=False).refusal(
        {"group": True, "mention": False, "reply": True}
    ) == "group_mention"


def test_history_store_dedupes_duplicate_rows_and_media() -> None:
    store = HistoryStore(database())

    count = store.add_many([("chat", archived("dup")), ("chat", archived("dup", mime="image/png"))])

    assert count == 1
    assert len(store.recent("chat", 10)) == 1
    assert store.find("chat", "dup").attachments[0].mimetype == "image/png"


def test_history_sync_dedupes_batch_before_insert() -> None:
    store = HistoryStore(database())
    sync = HistorySync()
    conversation = sync.conversations.add()
    conversation.ID = "628111@s.whatsapp.net"
    for _ in range(2):
        item = conversation.messages.add()
        item.message.key.remoteJID = conversation.ID
        item.message.key.ID = "dup-1"
        item.message.message.conversation = "halo"
        item.message.messageTimestamp = 1_700_000_000

    total, threads, _ = history_sync.ingest_sync(store.db, store, sync)

    assert total == 1
    assert threads == {conversation.ID}
    assert len(store.recent(conversation.ID, 10)) == 1


def test_inbound_media_populates_message_attachments(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    ev = group_event("")
    ev.Message.imageMessage.mimetype = "image/jpeg"
    ev.Message.imageMessage.fileLength = 3

    inbound = ingress.event_to_inbound(ev, gateway)

    assert inbound is not None
    assert len(inbound.payload.attachments) == 1
    assert inbound.payload.attachments[0].mimetype == "image/jpeg"
    gateway.history.add_many([(inbound.thread, inbound.payload)])
    kept = gateway.history.find(inbound.thread, "wamid-1")
    assert kept is not None
    assert len(kept.attachments) == 1


def test_inbound_lid_alt_is_persisted_for_later_resolution(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    ev = group_event("halo", sender="999")
    ev.Info.MessageSource.Sender.Server = "lid"
    ev.Info.MessageSource.SenderAlt.User = "628999999999"
    ev.Info.MessageSource.SenderAlt.Server = "s.whatsapp.net"

    inbound = ingress.event_to_inbound(ev, gateway)

    assert inbound is not None
    assert inbound.actor == "628999999999"
    assert directory.pn_jid(gateway.db, "999@lid") == "628999999999@s.whatsapp.net"
