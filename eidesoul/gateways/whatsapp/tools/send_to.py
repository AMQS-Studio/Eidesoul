"""Tool send_to: send text/files to a group or DM that already exists in the archive."""

from __future__ import annotations

import logging

from ....engine.registry import Handler
from ....engine.tools import ToolContext, _resolve
from .. import directory

log = logging.getLogger(__name__)

LIST_LIMIT = 20


def _known(ctx: ToolContext) -> str:
    if ctx.gateway is None:
        return "error: gateway is not available"
    groups = directory.groups(ctx.gateway.db, "", LIST_LIMIT)
    people = directory.contacts(ctx.gateway.db, "", LIST_LIMIT)
    parts: list[str] = []
    if groups:
        parts.append(f"Groups in the archive ({len(groups)}, newest first):")
        parts.extend(f"- {directory.label(chat_id, name)}" for chat_id, name in groups)
    if people:
        parts.append(f"Contacts in the archive ({len(people)}, newest first):")
        parts.extend(f"- {directory.label(chat_id, name)}" for chat_id, name in people)
    return "\n".join(parts) or "No groups or contacts in the archive yet."


def _record(ctx: ToolContext, target: str, target_name: str, kind: str, summary: str) -> None:
    if ctx.ledger is None or not ctx.chat_id or ctx.chat_id == target:
        return
    try:
        source_name = ctx.gateway.chat_name(ctx.chat_id) if ctx.gateway else ""
        ctx.ledger.log_action(
            ctx.chat_id,
            target,
            kind,
            summary,
            source_name=source_name,
            target_name=target_name,
            request=ctx.request,
        )
    except Exception:
        log.exception("failed to log a cross-chat action to %s", target)


async def _send_to(args: dict, ctx: ToolContext) -> str:
    if ctx.gateway is None or ctx.agent is None:
        return "error: gateway is not available"
    text = str(args.get("text") or "").strip()
    raw_path = str(args.get("path") or "").strip()
    if not text and not raw_path:
        return _known(ctx)
    raw_chat = str(args.get("chat") or "").strip()
    if not raw_chat:
        return "error: chat must be a group name, contact, or JID"
    target, candidates = ctx.gateway.resolve_target(raw_chat)
    if target is None:
        if candidates:
            listing = "\n".join(
                f"- {directory.label(chat_id, name)}" for chat_id, name in candidates[:LIST_LIMIT]
            )
            return (
                f'error: several targets match "{raw_chat}"; '
                f"use the full name or JID:\n{listing}"
            )
        return f'error: target "{raw_chat}" is not in the archive.\n{_known(ctx)}'
    chat_id, name = target
    if chat_id != ctx.chat_id and not ctx.trusted:
        return "error: cross-chat send is only for trusted roles"
    if chat_id != ctx.chat_id and chat_id not in ctx.reads:
        label = directory.label(chat_id, name)
        return (
            f'error: read the archive of {label} first with chat_history chat="{raw_chat}" '
            "before sending; blind sends are not allowed."
        )
    path = None
    if raw_path:
        path = _resolve(ctx, raw_path)
        if not path.is_file():
            return f"error: file not found: {raw_path}"
    caption = str(args.get("caption") or "").strip()[:1024]
    results: list[str] = []
    if text:
        result = await ctx.agent.send_text(chat_id, text, gateway=ctx.gateway)
        results.append(result)
        ok = not result.startswith("error:")
        _record(ctx, chat_id, name, "text", text if ok else f"failed — {result}")
        if ok:
            ctx.sent.append(
                {"tool": "send_to", "target": chat_id, "kind": "text", "summary": text[:200]}
            )
    if path is not None:
        result = await ctx.agent.send_file(chat_id, path, caption, gateway=ctx.gateway)
        results.append(result)
        ok = not result.startswith("error:")
        summary = f"{path.name}{f' — {caption}' if caption else ''}"
        _record(ctx, chat_id, name, "file", summary if ok else f"failed — {result}")
        if ok:
            ctx.sent.append(
                {"tool": "send_to", "target": chat_id, "kind": "file", "summary": summary}
            )
    label = directory.label(chat_id, name)
    if any(result.startswith("error:") for result in results):
        return "\n".join([f"error: sending to {label} failed:", *results])
    return "\n".join([f"ok: sent to {label}", *results])


TOOL = Handler(
    name="send_to",
    family="write",
    description=(
        "Send text and/or a file to an existing group or direct-message (DM) chat in the archive. "
        "chat = group/contact name (a fragment is fine) or JID; an empty chat lists groups and "
        "contacts. Use send for the active chat. Read the target archive first with "
        "chat_history chat=... before sending; blind sends are rejected."
    ),
    fn=_send_to,
    aliases=("kirim_ke",),
    parameters={
        "type": "object",
        "properties": {
            "chat": {
                "type": "string",
                "description": "Group/contact name, partial name, or JID @g.us/@s.whatsapp.net/@lid.",
            },
            "text": {"type": "string", "description": "Text to send (optional when a path is given)."},
            "path": {"type": "string", "description": "File path inside the workspace (optional)."},
            "caption": {"type": "string", "description": "Optional file caption."},
        },
        "additionalProperties": False,
    },
)
