"""WhatsApp history sync: inline contacts, group names, and WebMessageInfo ingest."""


import hashlib
import logging

from neonize.proto.waCompanionReg.WAWebProtobufsCompanionReg_pb2 import DeviceProps
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message as ProtoMessage
from neonize.proto.waHistorySync.WAWebProtobufsHistorySync_pb2 import HistorySync

from ...engine.context import AUTHOR_UNKNOWN, DEFAULT_VISIBILITY, Message
from . import directory
from .identity import normalize_jid
from .media import describe_media, event_time, message_text, quote_from_proto

log = logging.getLogger(__name__)

HISTORY_DAYS = 730
HISTORY_SIZE_MB = 5000
STORAGE_QUOTA_MB = 10240


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _read_varint(data: bytes, index: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, index
        shift += 7


def _replace_message_field(data: bytes, field: int, payload: bytes) -> bytes:
    """Replace a length-delimited field without dropping the others (unknown proto field)."""
    out = bytearray()
    index = 0
    found = False
    while index < len(data):
        start = index
        key, index = _read_varint(data, index)
        number, wire = key >> 3, key & 7
        if wire == 0:
            _, index = _read_varint(data, index)
        elif wire == 1:
            index += 8
        elif wire == 2:
            length, index = _read_varint(data, index)
            index += length
        elif wire == 5:
            index += 4
        else:
            raise ValueError(f"unknown wire type: {wire}")
        if number == field:
            found = True
            out += _varint((field << 3) | 2) + _varint(len(payload)) + payload
        else:
            out += data[start:index]
    if not found:
        out += _varint((field << 3) | 2) + _varint(len(payload)) + payload
    return bytes(out)


def history_props(require_full: bool) -> DeviceProps:
    """DeviceProps with history sync quotas; supportGroupHistory is field 15."""
    config = DeviceProps.HistorySyncConfig(
        fullSyncDaysLimit=HISTORY_DAYS,
        fullSyncSizeMbLimit=HISTORY_SIZE_MB,
        storageQuotaMb=STORAGE_QUOTA_MB,
        inlineInitialPayloadInE2EeMsg=True,
        recentSyncDaysLimit=HISTORY_DAYS,
        supportCallLogHistory=False,
        supportBotUserAgentChatHistory=True,
        supportCagReactionsAndPolls=True,
        supportBizHostedMsg=True,
        supportRecentSyncChunkMessageCountTuning=True,
        supportHostedGroupMsg=True,
        supportFbidBotChatHistory=True,
        supportAddOnHistorySyncMigration=True,
        supportMessageAssociation=True,
    )
    props = DeviceProps(
        os="Neonize",
        platformType=DeviceProps.SAFARI,
        requireFullSync=require_full,
        historySyncConfig=config,
    )
    raw_config = config.SerializeToString() + b"\x78\x01"
    return DeviceProps.FromString(
        _replace_message_field(props.SerializeToString(), 5, raw_config)
    )


def _decode_ref(value) -> object | None:
    if not value:
        return None
    try:
        return ProtoMessage.FromString(bytes(value))
    except Exception:
        return None


def _ingest_sync_contacts(db, sync: HistorySync) -> None:
    for mapping in sync.phoneNumberToLidMappings:
        directory.set_lid(db, mapping.lidJID, mapping.pnJID)
    for push in sync.pushnames:
        directory._upsert_contact(db, push.ID, "", push.pushname)
    for contact in sync.inlineContacts:
        name = contact.fullName or contact.firstName
        directory._upsert_contact(db, contact.pnJID, name, "")
        directory._upsert_contact(db, contact.lidJID, name, "")
    directory.refresh_sender_names(db)


def _sync_item(db, web, fallback_chat: str, fallback_ts: int = 0, self_jid: str = ""):
    key = web.key
    thread_id = key.remoteJID or fallback_chat
    if not thread_id or not web.message.ListFields():
        return None
    msg_id = key.ID or (
        "hist-"
        + hashlib.sha1(web.SerializeToString(), usedforsecurity=False).hexdigest()[:24]
    )
    text = message_text(web.message)
    media = describe_media(web.message, msg_id)
    quote = quote_from_proto(web.message, msg_id)
    if not text.strip() and media is None and quote is None:
        return None
    # History sync cannot tell who typed a `fromMe` line: do not guess bot/manual.
    author = AUTHOR_UNKNOWN
    from_me = bool(key.fromMe)
    if from_me:
        sender_address = self_jid
    elif thread_id.endswith("@g.us"):
        sender_address = key.participant or web.participant or ""
    else:
        sender_address = thread_id
    sender_id = normalize_jid(sender_address) if sender_address else ""
    sender = (
        directory.contact_name(db, sender_address) if sender_address else ""
    ) or (web.pushName or "").strip() or sender_id
    return (
        thread_id,
        Message(
            id=msg_id,
            sender_name=sender,
            sender_id=sender_id,
            when=event_time(int(web.messageTimestamp or fallback_ts or 0)),
            text=text,
            media=media,
            quote=quote,
            outgoing=from_me,
            sender_address=sender_address,
            sender_kind="person" if sender_address.endswith("@s.whatsapp.net") else "",
            author=author,
            visibility=DEFAULT_VISIBILITY,
        ),
    )


def ingest_sync(db, history, sync: HistorySync, self_jid: str = "") -> tuple[int, set[str], bool]:
    """Store one HistorySync event; return (count, threads, done)."""
    if sync is None:
        return 0, set(), False
    _ingest_sync_contacts(db, sync)
    thread_ids: set[str] = set()
    total = 0
    batch: list[tuple[str, Message]] = []
    for conversation in sync.conversations:
        fallback_chat = conversation.ID or ""
        fallback_ts = int(conversation.conversationTimestamp or 0)
        name = (conversation.name or conversation.displayName or "").strip()
        if name and fallback_chat:
            directory.set_chat_name(db, fallback_chat, name)
        for item in conversation.messages:
            parsed = _sync_item(db, item.message, fallback_chat, fallback_ts, self_jid)
            if parsed is None:
                continue
            thread_id, message = parsed
            batch.append((thread_id, message))
            thread_ids.add(thread_id)
            if len(batch) >= 500:
                total += history.add_many(batch)
                batch.clear()
    if batch:
        total += history.add_many(batch)
    done = bool(
        sync.syncType in (HistorySync.FULL, HistorySync.INITIAL_BOOTSTRAP) and sync.progress >= 100
    )
    if done:
        mark_full_sync(db)
    return total, thread_ids, done


def needs_full_sync(db) -> bool:
    from sqlalchemy import select

    from ...engine.schema import state

    found = db.fetch_one(select(state.c.value).where(state.c.key == "wa_full_sync_done"))
    return found is None or str(found["value"]) != "1"


def mark_full_sync(db) -> None:
    import time

    from ...engine.schema import state

    with db.connect() as conn:
        db.upsert(
            conn,
            state,
            [{"key": "wa_full_sync_done", "value": "1", "updated_at": time.time()}],
            ["key"],
            update={"value": "1", "updated_at": time.time()},
        )
