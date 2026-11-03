"""WhatsApp protobuf event -> Percept; the only place transport facts are read."""


import logging
from datetime import datetime

from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import ProtocolMessage

from ...engine.context import (
    AUTHOR_BOT,
    AUTHOR_MANUAL,
    AUTHOR_UNKNOWN,
    DEFAULT_VISIBILITY,
    Message,
    Quote,
)
from ...engine.inbound import Percept
from . import directory
from .identity import normalize_jid
from .media import contexts, describe_media, event_time, message_text, views_once

log = logging.getLogger(__name__)


def is_group(thread_id: str) -> bool:
    return thread_id.endswith("@g.us")


def self_handles(me) -> set[str]:
    if me is None:
        return set()
    values = {
        normalize_jid(getattr(getattr(me, "JID", None), "User", "") or ""),
        normalize_jid(getattr(getattr(me, "LID", None), "User", "") or ""),
    }
    return {value for value in values if value and any(ch.isdigit() for ch in value)}


def self_jid(me) -> str:
    """Own-account JID (not LID) for `fromMe` rows during history sync ingest."""
    jid = getattr(me, "JID", None)
    user = str(getattr(jid, "User", "") or "")
    server = str(getattr(jid, "Server", "") or "s.whatsapp.net")
    return f"{user}@{server}" if user else ""


def sender_name(ev, sender_digits: str, sender: str) -> str:
    return (ev.Info.Pushname or "").strip() or sender_digits or sender


def mentioned(ev, bots: set[str]) -> bool:
    """Transport fact only: a real mention in the payload, not a guess from the text."""
    return any(
        normalize_jid(jid) in bots
        for context in contexts(ev.Message)
        for jid in context.mentionedJID
    )


def replies_to_bot(ev, bots: set[str]) -> bool:
    return any(
        normalize_jid(context.participant) in bots
        for context in contexts(ev.Message)
        if context.participant
    )


def quote(ev, windows, thread_id: str) -> Quote | None:
    for context in contexts(ev.Message):
        quoted = context.quotedMessage
        if not quoted.ListFields():
            continue
        known = windows.find(thread_id, context.stanzaID)
        if known is not None:
            return Quote(
                id=known.id,
                sender_name=known.sender_name,
                sender_id=known.sender_id,
                text=known.text,
                media=known.media,
                when=known.when,
            )
        quoted_text = message_text(quoted)
        quoted_media = describe_media(quoted, context.stanzaID or f"{ev.Info.ID}-quote")
        if not quoted_text.strip() and quoted_media is None:
            continue
        participant = normalize_jid(context.participant) if context.participant else ""
        return Quote(
            id=context.stanzaID,
            sender_name=participant,
            sender_id=participant,
            text=quoted_text,
            media=quoted_media,
        )
    return None


def revoke_target(ev, gateway) -> tuple[str, str] | None:
    """(target message id, remover address) for protocolMessage REVOKE; None otherwise."""
    protocol = getattr(ev.Message, "protocolMessage", None)
    if protocol is None or not protocol.ListFields():
        return None
    if protocol.type != ProtocolMessage.REVOKE or not protocol.key.ID:
        return None
    sender = ev.Info.MessageSource.Sender
    address = f"{sender.User}@{sender.Server}"
    mapped = directory.pn_jid(gateway.db, address) if address.endswith("@lid") else ""
    return protocol.key.ID, mapped or address


def unreadable_message(ev, gateway) -> tuple[str, Message] | None:
    """Placeholder row for a stanza without <enc> (e.g. view-once from another device)."""
    source = ev.Info.MessageSource
    if not ev.Info.ID or source.Chat.Server == "broadcast":
        return None
    thread_id = f"{source.Chat.User}@{source.Chat.Server}"
    sender = f"{source.Sender.User}@{source.Sender.Server}"
    mapped = ""
    if sender.endswith("@lid"):
        mapped = directory.pn_jid(gateway.db, sender)
    sender_address = mapped or sender
    digits = normalize_jid(mapped or source.Sender.User or source.Sender.RawAgent)
    name = (
        directory.contact_name(gateway.db, sender_address)
        or (ev.Info.Pushname or "").strip()
        or digits
    )
    message = Message(
        id=ev.Info.ID,
        sender_name=name,
        sender_id=digits,
        when=event_time(ev.Info.Timestamp),
        text="",
        author=AUTHOR_MANUAL if source.IsFromMe else AUTHOR_UNKNOWN,
        unreadable=True,
        sender_address=sender_address,
        sender_kind="person" if sender_address.endswith("@s.whatsapp.net") else "",
    )
    return thread_id, message


def event_to_inbound(ev, gateway) -> Percept | None:
    source = ev.Info.MessageSource
    thread_id = f"{source.Chat.User}@{source.Chat.Server}"
    if thread_id.endswith("@broadcast"):
        return None
    sender = f"{source.Sender.User}@{source.Sender.Server}"
    mapped = ""
    if sender.endswith("@lid"):
        alt = getattr(source, "SenderAlt", None)
        mapped = f"{alt.User}@{alt.Server}" if getattr(alt, "User", "") else ""
        mapped = mapped or directory.pn_jid(gateway.db, sender)
    sender_address = mapped or sender
    sender_digits = normalize_jid(mapped or source.Sender.User or source.Sender.RawAgent)
    message_id = ev.Info.ID or f"tmp-{datetime.now().timestamp()}"
    text = message_text(ev.Message)
    media = describe_media(ev.Message, message_id)
    if not text.strip() and media is None:
        return None
    outgoing = bool(getattr(source, "IsFromMe", False))
    author = AUTHOR_UNKNOWN
    if outgoing:
        author = AUTHOR_BOT if gateway.is_bot_message(message_id) else AUTHOR_MANUAL
    name = sender_name(ev, sender_digits, sender)
    known = directory.contact_name(gateway.db, sender_address)
    if author == AUTHOR_BOT:
        known = gateway.config.soul.name
        name = gateway.config.soul.name
    message = Message(
        id=message_id,
        sender_name=known or name,
        sender_id=sender_digits,
        when=event_time(ev.Info.Timestamp),
        text=text,
        media=media,
        quote=quote(ev, gateway.windows, thread_id),
        outgoing=outgoing,
        sender_address=sender_address,
        sender_kind="person" if sender_address.endswith("@s.whatsapp.net") else "",
        author=author,
        visibility=DEFAULT_VISIBILITY,
        view_once=views_once(ev.Message),
        unreadable=False,
    )
    bots = gateway.self_handles()
    return Percept(
        source="whatsapp",
        thread=thread_id,
        actor=sender_digits,
        kind="message",
        payload=message,
        hints={
            "dm": not is_group(thread_id),
            "group": is_group(thread_id),
            "mention": mentioned(ev, bots),
            "reply": replies_to_bot(ev, bots),
            "self": bool(sender_digits and sender_digits in bots and not outgoing),
        },
        raw=ev,
    )
