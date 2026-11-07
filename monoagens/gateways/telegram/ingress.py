"""Telegram ingress: Bot API updates -> `Percept`, the only place wire facts are read.

The functions are pure: a mapping in, a `Percept` out. That keeps the routing testable without
a bot token, a network, or the SDK, and it keeps every transport name in this package.
"""


from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from ...engine.inbound import Percept
from ...engine.turn.context import (
    AUTHOR_BOT,
    AUTHOR_UNKNOWN,
    DEFAULT_VISIBILITY,
    Media,
    Message,
)

CALLBACK_PREFIX = "cmd:"
MEDIA_FIELDS = {
    "voice": ("voice", "audio/ogg"),
    "audio": ("audio", "audio/mpeg"),
    "video": ("video", "video/mp4"),
    "photo": ("image", "image/jpeg"),
    "document": ("document", "application/octet-stream"),
    "sticker": ("sticker", "image/webp"),
    "animation": ("video", "video/mp4"),
}


def thread_of(chat: Mapping, message: Mapping | None = None) -> str:
    """`<chat_id>` or `<chat_id>:<message_thread_id>` for a forum topic (ADR-0010 §4.3)."""
    chat_id = str(chat.get("id") or "")
    topic = (message or {}).get("message_thread_id")
    return f"{chat_id}:{topic}" if topic else chat_id


def actor_of(message: Mapping) -> str:
    return str((message.get("from") or {}).get("id") or "")


def when_of(message: Mapping) -> datetime:
    raw = int(message.get("date") or 0)
    if raw <= 0:
        return datetime.now(UTC).astimezone().replace(microsecond=0)
    return datetime.fromtimestamp(raw, UTC).astimezone()


def _text_of(message: Mapping) -> str:
    return str(message.get("text") or message.get("caption") or "")


def _entities(message: Mapping) -> list[Mapping]:
    found = message.get("entities") or message.get("caption_entities") or ()
    return [item for item in found if isinstance(item, Mapping)] if found else []


def mentioned(message: Mapping, username: str) -> bool:
    """A real mention entity, a `text_mention`, or an `@name` token; never a guess from content."""
    handle = str(username or "").lstrip("@").casefold()
    for entity in _entities(message):
        if str(entity.get("type") or "") in ("mention", "text_mention", "bot_command"):
            return True
    return bool(handle) and f"@{handle}" in _text_of(message).casefold()


def replied_to_bot(message: Mapping, self_id: str) -> bool:
    reply = message.get("reply_to_message") or {}
    if not isinstance(reply, Mapping):
        return False
    return bool(self_id) and str((reply.get("from") or {}).get("id") or "") == str(self_id)


def attachments(message: Mapping) -> tuple[Media, ...]:
    """Every attachment of one message, in a stable order; the photo array is the largest size."""
    found: list[Media] = []
    for field_name, (kind, mimetype) in MEDIA_FIELDS.items():
        content = message.get(field_name)
        if not content:
            continue
        items = content if isinstance(content, list) else [content]
        chosen = items[-1] if field_name == "photo" else items[0]
        if not isinstance(chosen, Mapping):
            continue
        file_id = str(chosen.get("file_id") or "")
        if not file_id:
            continue
        name = str(chosen.get("file_name") or "") or f"{kind}-{file_id[:12]}"
        found.append(
            Media(
                kind=kind,
                mimetype=str(chosen.get("mime_type") or mimetype),
                filename=Path(name).name,
                size=int(chosen.get("file_size") or 0),
                ref=file_id.encode("utf-8"),
            )
        )
    return tuple(found)


def message_percept(
    message: Mapping, *, self_id: str = "", username: str = ""
) -> Percept | None:
    """One Telegram message as a `Percept`; None when it carries nothing to read."""
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    if not isinstance(chat, Mapping) or not isinstance(sender, Mapping):
        return None
    text = _text_of(message)
    found = attachments(message)
    if not text.strip() and not found:
        return None
    private = str(chat.get("type") or "") == "private"
    thread = thread_of(chat, message)
    bot = bool(sender.get("is_bot"))
    name = " ".join(
        part
        for part in (str(sender.get("first_name") or ""), str(sender.get("last_name") or ""))
        if part
    ).strip() or str(sender.get("username") or "")
    entry = Message(
        id=str(message.get("message_id") or ""),
        sender_name=name,
        sender_id=actor_of(message),
        when=when_of(message),
        text=text,
        attachments=found,
        outgoing=False,
        author=AUTHOR_BOT if bot else AUTHOR_UNKNOWN,
        visibility=DEFAULT_VISIBILITY,
    )
    return Percept(
        source="telegram",
        thread=thread,
        actor=actor_of(message),
        kind="message",
        payload=entry,
        hints={
            "dm": private,
            "group": not private,
            "topic": bool(message.get("message_thread_id")),
            "mention": mentioned(message, username),
            "reply": replied_to_bot(message, self_id),
            "self": bot,
        },
        raw=message,
    )


def command_percept(
    command: str,
    *,
    chat: Mapping,
    message: Mapping,
    sender: Mapping,
) -> Percept | None:
    """A button press or a slash command as the exact chat command the engine reads."""
    actor = str(sender.get("id") or "")
    if not actor:
        return None
    private = str(chat.get("type") or "") == "private"
    name = " ".join(
        part
        for part in (str(sender.get("first_name") or ""), str(sender.get("last_name") or ""))
        if part
    ).strip() or str(sender.get("username") or "")
    entry = Message(
        id=str(message.get("message_id") or ""),
        sender_name=name,
        sender_id=actor,
        when=datetime.now(UTC).astimezone().replace(microsecond=0),
        text=str(command),
        sender_kind="person",
    )
    return Percept(
        source="telegram",
        thread=thread_of(chat, message),
        actor=actor,
        kind="message",
        payload=entry,
        hints={
            "dm": private,
            "group": not private,
            "topic": bool(message.get("message_thread_id")),
            # The click is addressed to the bot by construction; RBAC still decides the row.
            "mention": True,
            "interactive": True,
        },
        raw=message,
    )


def callback_percept(update: Mapping) -> tuple[Percept, str, Mapping] | None:
    """(percept, callback id, message) for a `callback_query`; None for anything else."""
    query = update.get("callback_query")
    if not isinstance(query, Mapping):
        return None
    data = str(query.get("data") or "")
    if not data.startswith(CALLBACK_PREFIX):
        return None
    message = query.get("message") or {}
    chat = message.get("chat") if isinstance(message, Mapping) else None
    sender = query.get("from")
    if not isinstance(message, Mapping) or not isinstance(chat, Mapping):
        return None
    if not isinstance(sender, Mapping):
        return None
    percept = command_percept(
        data[len(CALLBACK_PREFIX) :],
        chat=chat,
        message=message,
        sender=sender,
    )
    if percept is None:
        return None
    return percept, str(query.get("id") or ""), message
