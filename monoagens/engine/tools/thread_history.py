"""Tool thread_history: read another archived thread, on this body or another one (ADR-0001 §4.2).

The archive is shared; the thread and the body that owns it are named explicitly. Reading another
room is evidence-gated (the body proves membership, or the recorded presence does), and the read
is marked so the cross-room leak guard sees what left its own room.
"""


from datetime import datetime, timedelta

from ..inbound import refusal
from ..registry import Handler
from ..stores import rooms
from ..turn.context import body_note, entry_tag
from . import ToolContext, _count_capability, _int, body_of, cross_refusal, resolve_address

BODY_LIMIT = 600
CANDIDATE_LIMIT = 20
SEARCH_LIMIT = 50


def _time(value, *, end: bool) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid date: {value}") from exc
    if end and len(text) <= 10:
        parsed += timedelta(days=1)
        return parsed.timestamp() - 0.000001
    return parsed.timestamp()


def _body(entry) -> str:
    text = " ".join((entry.text or "").split())
    prefix = "[deleted] " if entry.deleted else ("[view-once] " if entry.view_once else "")
    if text:
        trimmed = text if len(text) <= BODY_LIMIT else text[: BODY_LIMIT - 1] + "…"
        return prefix + trimmed
    note = body_note(entry)
    if note:
        return prefix + note
    if entry.attachments:
        media = entry.attachments[0]
        return f"{prefix}(attachment {media.kind}: {media.filename})"
    return "(no text)"


def _who(entry) -> str:
    ident = entry.sender_id if entry.sender_id and entry.sender_id != entry.sender_name else ""
    if ident:
        return f"{entry.sender_name or '-'} ({ident})"
    return entry.sender_name or entry.sender_id or "-"


def _handles(ctx: ToolContext, thread_id: str, entry) -> list[str]:
    """The `att_…` handles of one archived message, for a later `send_file` forward."""
    if not entry.attachments:
        return []
    store = getattr(ctx.history, "attachments", None)
    if store is None:
        return []
    return [item.id for item in store.of_message(thread_id, entry.id)]


def _archived(ctx: ToolContext, thread_id: str) -> bool:
    return bool(thread_id) and bool(ctx.history.recent(thread_id, 1))


def _cross(ctx: ToolContext, body, thread_id: str) -> str:
    """The read is allowed only when the requester's presence in that room is proven."""
    if thread_id == ctx.thread_id and body is ctx.gateway:
        return ""
    if body is ctx.gateway:
        # The recorded-presence fallback and the body's members are one rule in the engine.
        return cross_refusal(ctx, thread_id)
    requester = str(ctx.person_id or ctx.sender)
    return refusal(body, thread_id, requester, recorded=ctx.history.rooms_of(requester))


def _target(ctx: ToolContext, body, raw_chat: str) -> tuple[str, str, str]:
    """`(thread_id, label, refusal)` for the chat the caller named."""
    chat = str(raw_chat or "").strip()
    if not chat:
        return str(ctx.thread_id), str(ctx.gateway.chat_name(ctx.thread_id) or ""), ""
    address, why = resolve_address(ctx, body, chat)
    if address is not None:
        return address.thread, str(body.chat_name(address.thread) or ""), ""
    if _archived(ctx, chat):
        # An opaque thread id the body's directory cannot label is still readable if archived.
        return chat, str(ctx.history.chat_name(chat) or ""), ""
    if why.startswith("error: several"):
        return "", "", why
    _count_capability(ctx, "miss_directory")
    return "", "", why


def _own_private(ctx: ToolContext, thread_id: str) -> bool:
    """True when the target is a private room the requester themselves wrote in.

    Continuity across a linked identity (ADR-0006 §4.4): a person asking about their own DM on
    another surface is reading their own conversation, not somebody else's room.
    """
    if not thread_id or thread_id == ctx.thread_id or ctx.history is None:
        return False
    who = str(ctx.person_id or ctx.sender or "").strip()
    if not who:
        return False
    return thread_id in rooms.private_rooms(ctx.history.db, who)


def _allowed_in_dm(ctx: ToolContext, thread_id: str) -> bool:
    """A room read from the sender's own private chat after `_cross` proved membership.

    The caller reached here only because `_cross` returned ""; from a direct chat that read is
    continuity with a room the sender belongs to, not a leak into a different audience.
    """
    return bool(ctx.direct) and thread_id != ctx.thread_id


def _remember_cross(ctx: ToolContext, results, thread_id: str, *, safe: bool = False) -> None:
    ctx.reads.add(thread_id)
    if safe:
        # A member's own room: no leak surface, so the reply may quote it normally.
        return
    for entry in results[:SEARCH_LIMIT]:
        ctx.cross_reads.append(_body(entry)[:600])
        if entry.quote is not None:
            quote = " ".join((entry.quote.text or "").split())
            if quote:
                ctx.cross_reads.append(quote[:300])


def _lines(
    ctx: ToolContext,
    results,
    thread_id: str,
    title: str,
    query: str,
    limit: int,
    *,
    own: bool = False,
    safe: bool = False,
) -> list[str]:
    cross = thread_id != ctx.thread_id
    if safe and results:
        kind = (
            "the sender's own private chat"
            if own
            else "a room the sender is a member of"
        )
        header = f'Archive "{title}" ({thread_id}; {kind}, answer normally):'
    elif cross and results:
        header = f'Archive "{title}" ({thread_id}; internal cross-room read, never leak it):'
    elif results:
        label = f' "{title}"' if title and title != thread_id else " this chat"
        header = (
            f"Archive{label} ({len(results)} hits, newest first):"
            if query
            else f"Latest messages in the archive for{label} ({len(results)}):"
        )
    else:
        return [f'No messages in the archive for "{title or thread_id}".']
    lines = [header]
    for entry in results:
        lines.append(
            f"[{entry.when:%Y-%m-%d %H:%M}] {_who(entry)} [{entry_tag(entry)}]: {_body(entry)}"
        )
        if entry.quote is not None:
            quote = " ".join((entry.quote.text or "").split())
            if quote:
                lines.append(f"  ↳ replying to {entry.quote.sender_name or '-'}: {quote[:300]}")
        handles = _handles(ctx, thread_id, entry)
        if handles:
            lines.append(f"  ↳ attachments: {', '.join(handles)}")
    if len(results) >= limit:
        lines.append(f"(limit {limit} hits; narrow the query or use before/after)")
    return lines


def _other_bodies(ctx: ToolContext, current) -> list:
    """Every other running body, in a stable order, for a thread the current one cannot name."""
    bodies = getattr(getattr(ctx, "agent", None), "gateways", None) or []
    return [item for item in bodies if item is not current]


def _find_elsewhere(ctx: ToolContext, current, chat: str) -> tuple[object | None, str, str]:
    """`(body, thread_id, title)` when exactly one other body knows the named chat."""
    found: list[tuple[object, str, str]] = []
    for other in _other_bodies(ctx, current):
        thread_id, title, _why = _target(ctx, other, chat)
        if thread_id:
            found.append((other, thread_id, title))
    return found[0] if len(found) == 1 else (None, "", "")


async def _history(args: dict, ctx: ToolContext) -> str:
    if ctx.history is None:
        return "error: the history archive is unavailable"
    named = str(args.get("gateway") or "").strip()
    body = body_of(ctx, named)
    if body is None:
        return "error: the requested body is not available"
    chat = str(args.get("chat") or "")
    thread_id, title, why = _target(ctx, body, chat)
    if not thread_id and chat.strip() and not named and not why.startswith("error: several"):
        # The model rarely names a body: a chat the current one cannot name may live on another.
        other, thread_id, title = _find_elsewhere(ctx, body, chat)
        if thread_id and other is not None:
            body = other
    if not thread_id:
        return why
    denial = _cross(ctx, body, thread_id)
    if denial:
        # ADR-0007 §4.6: an information-free signal, never an `error:` sentence.
        return denial
    try:
        before = _time(args.get("before"), end=True)
        after = _time(args.get("after"), end=False)
    except ValueError as exc:
        return f"error: {exc}"
    query = str(args.get("query") or "").strip()
    ceiling = int(ctx.config.memory.rooms.max_messages)
    limit = max(1, min(_int(args, "limit", 20), SEARCH_LIMIT, ceiling))
    results = ctx.history.search(thread_id, query, before=before, after=after, limit=limit)
    own = _own_private(ctx, thread_id)
    safe = own or _allowed_in_dm(ctx, thread_id)
    if thread_id != ctx.thread_id and results:
        _remember_cross(ctx, results, thread_id, safe=safe)
    return "\n".join(_lines(ctx, results, thread_id, title, query, limit, own=own, safe=safe))


TOOL = Handler(
    name="thread_history",
    family="read",
    target="room",
    approval_class="none",
    scan=("query", "chat"),
    description=(
        "Search an archived thread, newest first. Default: this thread's archive (outside the "
        "20-message payload). chat=<thread name or id> reads another archived thread, on any "
        "surface: when this body does not know the name, the other bodies are tried "
        "automatically (set gateway=<body name> to be explicit). Read the target here before "
        "sending anything to it; attachment handles (`att_…`) are listed with their messages."
    ),
    fn=_history,
    aliases=("chat_history", "history", "search_history"),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Text/name keywords; empty = latest messages.",
            },
            "chat": {
                "type": "string",
                "description": "Other archived thread: name (partial ok) or id; empty = this one.",
            },
            "gateway": {
                "type": "string",
                "description": "Body that owns the thread; empty = this body.",
            },
            "before": {
                "type": "string",
                "description": "Upper time bound, ISO YYYY-MM-DD or YYYY-MM-DDTHH:MM.",
            },
            "after": {
                "type": "string",
                "description": "Lower time bound, ISO YYYY-MM-DD or YYYY-MM-DDTHH:MM.",
            },
            "limit": {"type": "integer", "description": "Result count 1-50 (default 20)."},
        },
        "additionalProperties": False,
    },
)
