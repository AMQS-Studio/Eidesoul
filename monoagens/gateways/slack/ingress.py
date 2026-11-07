"""Slack ingress: normalized events -> `Percept` (ADR-0010 §4.5).

`transport.py` flattens a Bolt event or a `block_actions` payload into the plain mappings read
here, so this module never imports `slack_bolt` and its rules are testable without it.
"""


from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from ...engine.inbound import Percept
from ...engine.turn.context import AUTHOR_BOT, AUTHOR_UNKNOWN, Media, Message

COMPONENT_PREFIX = "cmd:"
SUBTYPE_EDIT = "message_changed"


def mapping_of(value: object) -> Mapping:
    """The value when it is a mapping, else an empty one; keeps callers free of None checks."""
    return value if isinstance(value, Mapping) else {}


def thread_of(event: Mapping) -> str:
    """`<channel_id>:<thread_ts>` for a thread, `<channel_id>` otherwise (ADR-0010 §4.5)."""
    channel = str(event.get("channel") or "")
    thread = str(event.get("thread_ts") or "")
    return f"{channel}:{thread}" if thread else channel


def _media(files: object) -> tuple[Media, ...]:
    found: list[Media] = []
    if not isinstance(files, (list, tuple)):
        return ()
    for item in files:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or item.get("title") or "file")
        mimetype = str(item.get("mimetype") or "application/octet-stream")
        kind = mimetype.split("/")[0]
        if kind not in ("image", "video", "audio"):
            kind = "document"
        found.append(
            Media(
                kind=kind,
                mimetype=mimetype,
                filename=Path(name).name,
                size=int(item.get("size") or 0),
                ref=str(
                    item.get("url_private_download") or item.get("url_private") or ""
                ).encode("utf-8"),
            )
        )
    return tuple(found)


def _when(event: Mapping) -> datetime:
    raw = str(event.get("ts") or "")
    try:
        return datetime.fromtimestamp(float(raw.split(".")[0]), UTC).astimezone()
    except (ValueError, OverflowError, OSError):
        return datetime.now(UTC).astimezone().replace(microsecond=0)


def _mentions(event: Mapping, self_id: str) -> bool:
    text = str(event.get("text") or "")
    if self_id and f"<@{self_id}>" in text:
        return True
    for item in event.get("blocks") or ():
        if isinstance(item, Mapping) and self_id and f"<@{self_id}>" in str(item):
            return True
    return False


def message_percept(event: Mapping, *, self_id: str = "") -> Percept | None:
    """One Slack message event as a `Percept`; None when it carries nothing to read."""
    body = event.get("message") if event.get("subtype") == SUBTYPE_EDIT else event
    if not isinstance(body, Mapping):
        return None
    user = str(body.get("user") or event.get("user") or "")
    if not user:
        return None
    text = str(body.get("text") or "")
    files = _media(body.get("files"))
    if not text.strip() and not files:
        return None
    channel = str(event.get("channel") or "")
    if not channel:
        return None
    thread_ts = str(event.get("thread_ts") or body.get("thread_ts") or "")
    bot = bool(body.get("bot_id"))
    entry = Message(
        id=str(body.get("ts") or event.get("event_ts") or ""),
        sender_name=str(body.get("username") or user),
        sender_id=user,
        when=_when(body),
        text=text,
        attachments=files,
        author=AUTHOR_BOT if bot else AUTHOR_UNKNOWN,
    )
    return Percept(
        source="slack",
        thread=thread_of({**event, "thread_ts": thread_ts}),
        actor=user,
        kind="message",
        payload=entry,
        hints={
            "dm": str(event.get("channel_type") or "") == "im",
            "group": str(event.get("channel_type") or "") != "im",
            "thread": bool(thread_ts),
            "mention": _mentions(event, self_id),
            "reply": bool(self_id) and str(event.get("parent_user_id") or "") == self_id,
            "self": bot,
        },
        raw=event,
    )


def action_percept(payload: Mapping) -> tuple[Percept, str, str] | None:
    """(percept, response url, message ts) for a `block_actions` click; None otherwise."""
    actions = payload.get("actions")
    user = payload.get("user")
    channel = payload.get("channel")
    if not isinstance(actions, list) or not actions:
        return None
    if not isinstance(user, Mapping) or not isinstance(channel, Mapping):
        return None
    channel_id = str(channel.get("id") or "")
    channel_name = str(channel.get("name") or "")
    action = actions[0] if isinstance(actions[0], Mapping) else {}
    command = str(action.get("value") or action.get("action_id") or "")
    if command.startswith(COMPONENT_PREFIX):
        command = command[len(COMPONENT_PREFIX) :]
    if not command.startswith("/"):
        return None
    container = mapping_of(payload.get("container"))
    message_ts = str(container.get("message_ts") or "")
    thread_ts = str(container.get("thread_ts") or "")
    actor = str(user.get("id") or "")
    if not actor:
        return None
    entry = Message(
        id=message_ts,
        sender_name=str(user.get("name") or user.get("username") or actor),
        sender_id=actor,
        when=datetime.now(UTC).astimezone().replace(microsecond=0),
        text=command,
        sender_kind="person",
    )
    percept = Percept(
        source="slack",
        thread=thread_of({"channel": channel_id, "thread_ts": thread_ts}),
        actor=actor,
        kind="message",
        payload=entry,
        hints={
            "dm": channel_name == "directmessage",
            "group": channel_name != "directmessage",
            "thread": bool(thread_ts),
            # The click is addressed to the bot by construction; RBAC still decides the row.
            "mention": True,
            "interactive": True,
        },
        raw=payload,
    )
    response_url = str(payload.get("response_url") or "")
    return percept, response_url, message_ts
