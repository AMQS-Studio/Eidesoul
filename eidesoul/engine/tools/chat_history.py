"""Tool chat_history: search old message archives for this chat or another archived chat."""

from __future__ import annotations

from datetime import datetime, timedelta

from ..context import body_note, entry_tag
from ..registry import Handler
from . import ToolContext, _int

BODY_LIMIT = 600
CANDIDATE_LIMIT = 20


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
    if entry.media is not None:
        return f"{prefix}(attachment {entry.media.kind}: {entry.media.filename})"
    return "(no text)"


def _who(entry) -> str:
    ident = entry.sender_id if entry.sender_id and entry.sender_id != entry.sender_name else ""
    if ident:
        return f"{entry.sender_name or '-'} ({ident})"
    return entry.sender_name or entry.sender_id or "-"


def _target(raw: str, ctx: ToolContext) -> str | tuple[str, str]:
    """Resolve `(chat_id, name)` for another chat, or an `error: ...` text."""
    if ctx.gateway is None:
        return "error: no gateway available; only this chat can be read"
    target, candidates = ctx.gateway.resolve_target(raw)
    if target is None and candidates:
        listing = "\n".join(
            f"- {name} ({chat_id})" if name else f"- {chat_id}"
            for chat_id, name in candidates[:CANDIDATE_LIMIT]
        )
        return f'error: several chats match "{raw}"; use the full name or JID:\n{listing}'
    if target is None:
        return f'error: chat "{raw}" is not in the archive.'
    return target


def _remember_cross(ctx: ToolContext, results, chat_id: str) -> None:
    ctx.reads.add(chat_id)
    for entry in results[:50]:
        ctx.cross_reads.append(_body(entry)[:600])
        if entry.quote is not None:
            quote = " ".join((entry.quote.text or "").split())
            if quote:
                ctx.cross_reads.append(quote[:300])


async def _search(args: dict, ctx: ToolContext) -> str:
    if ctx.history is None:
        return "error: the history archive is unavailable"
    raw_chat = str(args.get("chat") or "").strip()
    chat_id = ctx.chat_id
    name = ""
    if raw_chat:
        resolved = _target(raw_chat, ctx)
        if isinstance(resolved, str):
            return resolved
        chat_id, name = resolved
    elif not chat_id:
        return "error: no active chat"
    try:
        before = _time(args.get("before"), end=True)
        after = _time(args.get("after"), end=False)
    except ValueError as exc:
        return f"error: {exc}"
    query = str(args.get("query") or "").strip()
    limit = max(1, min(_int(args, "limit", 20), 50))
    results = ctx.history.search(chat_id, query, before=before, after=after, limit=limit)
    cross = chat_id != ctx.chat_id
    title = name or ctx.history.chat_name(chat_id) or chat_id
    if cross and results:
        _remember_cross(ctx, results, chat_id)
    if cross:
        if not results:
            return (
                f'No messages in the archive for "{title}". Its content is unreadable; '
                "never send blind."
            )
        header = (
            f'Archive "{title}" ({chat_id}; internal cross-room read, never leak it):'
        )
    elif results:
        label = f' "{title}"' if title != chat_id else " this chat"
        header = (
            f"Archive{label} ({len(results)} hits, newest first):"
            if query
            else f"Latest messages in the archive for{label} ({len(results)}):"
        )
    else:
        return "No matching messages in this chat's archive."
    lines = [header]
    for entry in results:
        tag = entry_tag(entry)
        lines.append(f"[{entry.when:%Y-%m-%d %H:%M}] {_who(entry)} [{tag}]: {_body(entry)}")
        if entry.quote is not None:
            quote = " ".join((entry.quote.text or "").split())
            if quote:
                lines.append(f"  ↳ replying to {entry.quote.sender_name or '-'}: {quote[:300]}")
    if len(results) >= limit:
        lines.append(f"(limit {limit} hits; narrow the query or use before/after)")
    return "\n".join(lines)


TOOL = Handler(
    name="chat_history",
    family="read",
    description=(
        "Search an archived chat, newest first. Default: this chat's archive (outside the "
        "20-message payload). chat=<group/contact name or JID> reads another archived chat; read "
        "the target with this tool before sending anything to it."
    ),
    fn=_search,
    aliases=("history", "search_history"),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Text/name keywords; empty = latest messages."},
            "chat": {
                "type": "string",
                "description": (
                    "Other archived chat: group/contact name (partial ok) or JID. Empty = this chat."
                ),
            },
            "before": {"type": "string", "description": "Upper time bound, ISO YYYY-MM-DD or YYYY-MM-DDTHH:MM."},
            "after": {"type": "string", "description": "Lower time bound, ISO YYYY-MM-DD or YYYY-MM-DDTHH:MM."},
            "limit": {"type": "integer", "description": "Result count 1-50 (default 20)."},
        },
        "additionalProperties": False,
    },
)
