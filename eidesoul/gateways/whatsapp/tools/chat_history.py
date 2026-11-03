"""Tool chat_history: search old message archives for this chat or another archived chat.

The archive belongs to the body that owns one; the engine keeps no archive tool (ADR-0000 §4.5).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ....engine.context import body_note, entry_tag
from ....engine.inbound import directory_refusal
from ....engine.registry import Handler
from ....engine.tools import ToolContext, _int, cross_refusal

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


def _count_refusal(ctx: ToolContext) -> None:
    """Record that the body had no `Directory` to resolve a cross-room target (ADR-0014 §4.5)."""
    runtime = getattr(getattr(ctx, "agent", None), "runtime", None)
    if runtime is not None and hasattr(runtime, "directory_missing_total"):
        runtime.directory_missing_total = int(runtime.directory_missing_total or 0) + 1


def _target(raw: str, ctx: ToolContext) -> str | tuple[str, str]:
    """Resolve `(thread_id, name)` for another chat, or an `error: ...` text."""
    if ctx.gateway is None:
        return "error: no gateway available; only this chat can be read"
    reason = directory_refusal(ctx.gateway)
    if reason:
        _count_refusal(ctx)
        return f"error: {reason}"
    target, candidates = ctx.gateway.resolve_target(raw)
    if target is None and candidates:
        listing = "\n".join(
            f"- {name} ({thread_id})" if name else f"- {thread_id}"
            for thread_id, name in candidates[:CANDIDATE_LIMIT]
        )
        return f'error: several threads match "{raw}"; use the full name or id:\n{listing}'
    if target is None:
        return f'error: chat "{raw}" is not in the archive.'
    thread_id, name = target
    denial = cross_refusal(ctx, thread_id)
    if denial:
        # ADR-0007 §4.6: an information-free signal, never an `error:` sentence.
        return denial
    return target


def _remember_cross(ctx: ToolContext, results, thread_id: str) -> None:
    ctx.reads.add(thread_id)
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
    thread_id = ctx.thread_id
    name = ""
    if raw_chat:
        resolved = _target(raw_chat, ctx)
        if isinstance(resolved, str):
            return resolved
        thread_id, name = resolved
    elif not thread_id:
        return "error: no active chat"
    try:
        before = _time(args.get("before"), end=True)
        after = _time(args.get("after"), end=False)
    except ValueError as exc:
        return f"error: {exc}"
    query = str(args.get("query") or "").strip()
    limit = max(
        1,
        min(
            _int(args, "limit", 20),
            50,
            int(ctx.config.memory.rooms.max_messages),
        ),
    )
    results = ctx.history.search(thread_id, query, before=before, after=after, limit=limit)
    cross = thread_id != ctx.thread_id
    title = name or ctx.history.chat_name(thread_id) or thread_id
    if cross and results:
        _remember_cross(ctx, results, thread_id)
    if cross:
        if not results:
            return (
                f'No messages in the archive for "{title}". Its content is unreadable; '
                "never send blind."
            )
        header = (
            f'Archive "{title}" ({thread_id}; internal cross-room read, never leak it):'
        )
    elif results:
        label = f' "{title}"' if title != thread_id else " this chat"
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
        "20-message payload). chat=<chat name or id> reads another archived chat; read "
        "the target with this tool before sending anything to it."
    ),
    fn=_search,
    aliases=("history", "search_history"),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Text/name keywords; empty = latest messages.",
            },
            "chat": {
                "type": "string",
                "description": (
                    "Other archived chat: chat name (partial ok) or id. Empty = this chat."
                ),
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
