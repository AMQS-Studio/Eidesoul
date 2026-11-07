"""Discord ingress: normalized transport payloads -> `Percept` (ADR-0010 §4.4).

`transport.py` flattens a `discord.Message` or an `Interaction` into the plain mappings read
here, so this module never imports the SDK and its rules are testable without it.
"""


from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from ...engine.inbound import Percept
from ...engine.turn.context import AUTHOR_BOT, AUTHOR_UNKNOWN, Media, Message

COMPONENT_PREFIX = "cmd:"
ATTACHMENT_KINDS = {
    "image": ("image", "image/png"),
    "video": ("video", "video/mp4"),
    "audio": ("audio", "audio/mpeg"),
}


def thread_of(payload: Mapping) -> str:
    """A thread or forum post keeps its own address; a channel keeps the channel id."""
    thread_id = payload.get("thread_id")
    if thread_id:
        return str(thread_id)
    return str(payload.get("channel_id") or "")


def _media(payload: Mapping) -> tuple[Media, ...]:
    found: list[Media] = []
    for item in payload.get("attachments") or ():
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("filename") or "file")
        declared = str(item.get("content_type") or "").split(";")[0].strip().lower()
        kind, fallback = ATTACHMENT_KINDS.get(declared.split("/")[0], ("document", ""))
        found.append(
            Media(
                kind=kind,
                mimetype=declared or fallback or "application/octet-stream",
                filename=Path(name).name,
                size=int(item.get("size") or 0),
                ref=str(item.get("url") or "").encode("utf-8"),
            )
        )
    return tuple(found)


def _when(payload: Mapping) -> datetime:
    raw = str(payload.get("timestamp") or "")
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()
        except ValueError:
            pass
    return datetime.now(UTC).astimezone().replace(microsecond=0)


def _mentions_bot(payload: Mapping, self_id: str) -> bool:
    for item in payload.get("mentions") or ():
        if isinstance(item, Mapping) and str(item.get("id") or "") == str(self_id):
            return True
    return False


def _reply(payload: Mapping, self_id: str) -> tuple[bool, str]:
    """(replies to the bot, quoted message id) from the reference the transport flattened."""
    reference = payload.get("reference")
    if not isinstance(reference, Mapping):
        return False, ""
    author = str(reference.get("author_id") or "")
    return bool(self_id) and author == str(self_id), str(reference.get("message_id") or "")


def message_percept(payload: Mapping, *, self_id: str = "") -> Percept | None:
    """One Discord message as a `Percept`; None when it carries nothing to read."""
    author = payload.get("author")
    if not isinstance(author, Mapping):
        return None
    text = str(payload.get("content") or "")
    found = _media(payload)
    if not text.strip() and not found:
        return None
    thread = thread_of(payload)
    if not thread:
        return None
    replied, quote_id = _reply(payload, self_id)
    group = bool(payload.get("guild_id"))
    entry = Message(
        id=str(payload.get("id") or ""),
        sender_name=str(author.get("name") or ""),
        sender_id=str(author.get("id") or ""),
        when=_when(payload),
        text=text,
        attachments=found,
        author=AUTHOR_BOT if author.get("bot") else AUTHOR_UNKNOWN,
    )
    return Percept(
        source="discord",
        thread=thread,
        actor=str(author.get("id") or ""),
        kind="message",
        payload=entry,
        hints={
            "dm": not group,
            "group": group,
            "thread": bool(payload.get("thread_id")),
            "mention": _mentions_bot(payload, self_id),
            "reply": replied,
            "self": bool(author.get("bot")),
            "quote": quote_id,
        },
        raw=payload,
    )


def interaction_percept(payload: Mapping) -> tuple[Percept, str, str] | None:
    """(percept, interaction id, message id) for a component click; None otherwise."""
    data = payload.get("data")
    user = payload.get("user")
    if not isinstance(data, Mapping) or not isinstance(user, Mapping):
        return None
    values = data.get("values")
    if isinstance(values, list) and values:
        custom_id = str(values[0])
    else:
        custom_id = str(data.get("custom_id") or "")
    if not custom_id.startswith(COMPONENT_PREFIX):
        return None
    thread = thread_of(payload)
    actor = str(user.get("id") or "")
    if not thread or not actor:
        return None
    group = bool(payload.get("guild_id"))
    message = payload.get("message") if isinstance(payload.get("message"), Mapping) else {}
    entry = Message(
        id=str((message or {}).get("id") or payload.get("id") or ""),
        sender_name=str(user.get("name") or ""),
        sender_id=actor,
        when=datetime.now(UTC).astimezone().replace(microsecond=0),
        text=custom_id[len(COMPONENT_PREFIX) :],
        sender_kind="person",
    )
    percept = Percept(
        source="discord",
        thread=thread,
        actor=actor,
        kind="message",
        payload=entry,
        hints={
            "dm": not group,
            "group": group,
            "thread": bool(payload.get("thread_id")),
            # The click is addressed to the bot by construction; RBAC still decides the row.
            "mention": True,
            "interactive": True,
        },
        raw=payload,
    )
    return percept, str(payload.get("id") or ""), str((message or {}).get("id") or "")
