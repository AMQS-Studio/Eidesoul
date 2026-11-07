"""WhatsApp protobuf event -> Percept; the only place transport facts are read."""


import logging
import time

from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import ProtocolMessage

from ...engine.inbound import Percept
from ...engine.turn.context import (
    AUTHOR_BOT,
    AUTHOR_MANUAL,
    AUTHOR_UNKNOWN,
    DEFAULT_VISIBILITY,
    Message,
    Quote,
)
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


# ADR-0010 §4.2: the two reactions a pending prompt accepts as a decision.
APPROVE_REACTIONS = frozenset({"\U0001f44d", "\u2705"})
DENY_REACTIONS = frozenset({"\U0001f44e", "\u274c"})


def reaction_verb(emoji: str) -> str:
    """`approve`, `deny`, or "" for any other emoji."""
    value = str(emoji or "").strip()
    if value in APPROVE_REACTIONS:
        return "approve"
    if value in DENY_REACTIONS:
        return "deny"
    return ""


def edit_target(ev) -> tuple[str, str] | None:
    """(target message id, new text) for protocolMessage MESSAGE_EDIT; None otherwise."""
    protocol = getattr(ev.Message, "protocolMessage", None)
    if protocol is None or not protocol.ListFields():
        return None
    if protocol.type != ProtocolMessage.MESSAGE_EDIT or not protocol.key.ID:
        return None
    edited = getattr(protocol, "editedMessage", None)
    if edited is None:
        return None
    return str(protocol.key.ID), message_text(edited)


def _sender(ev, gateway) -> tuple[str, str, str]:
    """(sender address, sender digits, display name) exactly as `event_to_inbound` derives them."""
    source = ev.Info.MessageSource
    sender = f"{source.Sender.User}@{source.Sender.Server}"
    mapped = ""
    if sender.endswith("@lid"):
        mapped = directory.pn_jid(gateway.db, sender)
    address = mapped or sender
    digits = normalize_jid(mapped or source.Sender.User or source.Sender.RawAgent)
    name = sender_name(ev, digits, sender)
    return address, digits, name


def _command_percept(ev, gateway, command: str) -> Percept | None:
    """One reaction or click turned into the exact chat command the engine already reads."""
    source = ev.Info.MessageSource
    thread_id = f"{source.Chat.User}@{source.Chat.Server}"
    address, digits, name = _sender(ev, gateway)
    if not digits:
        return None
    known = directory.contact_name(gateway.db, address)
    message = Message(
        id=ev.Info.ID or f"event-{time.time_ns()}",
        sender_name=known or name,
        sender_id=digits,
        when=event_time(ev.Info.Timestamp),
        text=str(command),
        sender_address=address,
        sender_kind="person" if address.endswith(("@s.whatsapp.net", "@lid")) else "",
        author=AUTHOR_UNKNOWN,
    )
    group = is_group(thread_id)
    return Percept(
        source="whatsapp",
        thread=thread_id,
        actor=digits,
        kind="message",
        payload=message,
        hints={"dm": not group, "group": group, "interactive": True},
        raw=ev,
    )


def reaction_target(ev, gateway) -> Percept | None:
    """A 👍/👎 reaction on a pending prompt becomes `/approve #id` or `/deny #id`.

    ADR-0010 §4.2: Meta restricts native buttons on many accounts, so the reaction is the
    quick-confirm path, and it resolves to the same command the button would send.
    """
    reaction = getattr(ev.Message, "reactionMessage", None)
    if reaction is None or not reaction.ListFields():
        return None
    command = gateway.approval_command(
        getattr(reaction, "text", ""), str(getattr(reaction.key, "ID", "") or "")
    )
    return _command_percept(ev, gateway, command) if command else None


def _edited_mention(edited, bots: set[str]) -> tuple[bool, bool]:
    """(mentioned, replied) read from the edited payload, not the outer protocol envelope."""
    found = list(contexts(edited))
    mentioned_bot = any(
        normalize_jid(jid) in bots for context in found for jid in context.mentionedJID
    )
    replied = any(
        normalize_jid(context.participant) in bots for context in found if context.participant
    )
    return mentioned_bot, replied


def edit_to_inbound(ev, gateway, target_id: str, text: str) -> Percept | None:
    """The percept for an edited message, only when the edit is addressed to the bot."""
    protocol = getattr(ev.Message, "protocolMessage", None)
    edited = getattr(protocol, "editedMessage", None)
    if edited is None:
        return None
    source = ev.Info.MessageSource
    thread_id = f"{source.Chat.User}@{source.Chat.Server}"
    bots = gateway.self_handles()
    mentioned_bot, replied = _edited_mention(edited, bots)
    if not (mentioned_bot or replied):
        # An edit refreshes the window; it is a new turn only when it is addressed to the bot.
        return None
    address, digits, name = _sender(ev, gateway)
    known = directory.contact_name(gateway.db, address)
    message = Message(
        id=str(target_id),
        sender_name=known or name,
        sender_id=digits,
        when=event_time(ev.Info.Timestamp),
        text=str(text),
        sender_address=address,
        sender_kind="person" if address.endswith(("@s.whatsapp.net", "@lid")) else "",
        author=AUTHOR_UNKNOWN,
    )
    group = is_group(thread_id)
    return Percept(
        source="whatsapp",
        thread=thread_id,
        actor=digits,
        kind="message",
        payload=message,
        hints={
            "dm": not group,
            "group": group,
            "mention": mentioned_bot,
            "reply": replied,
            "edit": True,
        },
        raw=ev,
    )


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
        sender_kind="person" if sender_address.endswith(("@s.whatsapp.net", "@lid")) else "",
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
        alt_user = getattr(alt, "User", "")
        alt_server = getattr(alt, "Server", "")
        mapped = f"{alt_user}@{alt_server}" if alt_user and alt_server else ""
        if mapped:
            # Harvest the LID -> phone mapping the paired device just revealed.
            directory.set_lid(gateway.db, sender, mapped)
        mapped = mapped or directory.pn_jid(gateway.db, sender)
    sender_address = mapped or sender
    sender_digits = normalize_jid(mapped or source.Sender.User or source.Sender.RawAgent)
    message_id = ev.Info.ID or f"tmp-{time.time_ns()}"
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
        attachments=(media,) if media is not None else (),
        quote=quote(ev, gateway.windows, thread_id),
        outgoing=outgoing,
        sender_address=sender_address,
        sender_kind="person" if sender_address.endswith(("@s.whatsapp.net", "@lid")) else "",
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
