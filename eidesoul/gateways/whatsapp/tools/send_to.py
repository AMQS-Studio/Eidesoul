"""Tool send_to: send text/files to a group or DM that already exists in the archive."""

from __future__ import annotations

import logging

from ....engine.inbound import Directory, directory_refusal
from ....engine.registry import Handler
from ....engine.relay import RelayStore
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
        parts.extend(f"- {directory.label(thread_id, name)}" for thread_id, name in groups)
    if people:
        parts.append(f"Contacts in the archive ({len(people)}, newest first):")
        parts.extend(f"- {directory.label(thread_id, name)}" for thread_id, name in people)
    return "\n".join(parts) or "No groups or contacts in the archive yet."


def _record(
    ctx: ToolContext,
    target: str,
    target_name: str,
    kind: str,
    summary: str,
    *,
    relay=None,
    ok: bool = True,
) -> None:
    if ctx.ledger is None or not ctx.thread_id:
        return
    if relay is not None:
        # ADR-0023 §4.3: the ledger keeps the grant and the mode, never the message content.
        kind = "relay"
        summary = (
            f"relay {relay.id} mode={relay.mode} from={relay.from_person}"
            f"{'' if ok else ' failed'}"
        )
    elif ctx.thread_id == target:
        return
    try:
        source_name = ctx.gateway.chat_name(ctx.thread_id) if ctx.gateway else ""
        ctx.ledger.log_action(
            ctx.thread_id,
            target,
            kind,
            summary,
            source_name=source_name,
            target_name=target_name,
            request=ctx.request,
        )
    except Exception:
        log.exception("failed to log a cross-chat action to %s", target)


def _relay(ctx: ToolContext, thread_id: str, args: dict, text: str):
    """`(grant, "")` when a relay is permitted, `(None, "")` for a plain send, `(None, error)`."""
    task = str(args.get("relay_task") or "").strip()
    source = str(args.get("relay_from") or "").strip()
    if not task and not source:
        return None, ""
    if not task or not source:
        return None, "error: a relay needs both relay_task and relay_from"
    members = (
        ctx.gateway.participants(thread_id) if isinstance(ctx.gateway, Directory) else None
    )
    if not members or len(members) != 1:
        return None, "error: this body cannot prove the relay recipient; the relay is refused"
    grant, reason = RelayStore(ctx.agent.db).check(
        task_id=task, from_person=source, to_person=next(iter(members))
    )
    if grant is None:
        return None, f"error: relay refused: {reason}"
    if text and grant.mode == "anonymous":
        person = ctx.agent.identity.person(source) or {}
        name = str(person.get("name") or "").strip()
        if name and name.casefold() in text.casefold():
            return None, "error: an anonymous relay must not name its source"
    return grant, ""


async def _deliver_text(
    ctx: ToolContext, thread_id: str, name: str, text: str, grant
) -> tuple[str, dict | None]:
    result = await ctx.agent.send_text(thread_id, text, gateway=ctx.gateway)
    ok = not result.startswith("error:")
    _record(
        ctx, thread_id, name, "text", text if ok else f"failed — {result}", relay=grant, ok=ok
    )
    if not ok:
        return result, None
    summary = f"relay {grant.id} mode={grant.mode}" if grant is not None else text[:200]
    entry = {
        "tool": "send_to",
        "target": thread_id,
        "kind": "relay" if grant is not None else "text",
        "summary": summary,
    }
    return result, entry


async def _deliver_file(
    ctx: ToolContext, thread_id: str, name: str, path, caption: str, grant
) -> tuple[str, dict | None]:
    result = await ctx.agent.send_file(thread_id, path, caption, gateway=ctx.gateway)
    ok = not result.startswith("error:")
    summary = f"{path.name}{f' — {caption}' if caption else ''}"
    _record(
        ctx, thread_id, name, "file", summary if ok else f"failed — {result}", relay=grant, ok=ok
    )
    if not ok:
        return result, None
    entry = {
        "tool": "send_to",
        "target": thread_id,
        "kind": "relay" if grant is not None else "file",
        "summary": f"relay {grant.id} mode={grant.mode}" if grant is not None else summary,
    }
    return result, entry


def _target(ctx: ToolContext, raw_chat: str) -> tuple[tuple[str, str] | None, str]:
    target, candidates = ctx.gateway.resolve_target(raw_chat)
    if target is not None:
        return target, ""
    if candidates:
        listing = "\n".join(
            f"- {directory.label(thread_id, name)}"
            for thread_id, name in candidates[:LIST_LIMIT]
        )
        return None, (
            f'error: several targets match "{raw_chat}"; use the full name or JID:\n{listing}'
        )
    return None, f'error: target "{raw_chat}" is not in the archive.\n{_known(ctx)}'


def _cross_refusal(ctx: ToolContext, thread_id: str, name: str, raw_chat: str) -> str:
    if thread_id == ctx.thread_id:
        return ""
    if not ctx.trusted:
        return "error: cross-chat send is only for trusted roles"
    if thread_id not in ctx.reads:
        label = directory.label(thread_id, name)
        return (
            f'error: read the archive of {label} first with chat_history chat="{raw_chat}" '
            "before sending; blind sends are not allowed."
        )
    return ""


def _workspace_path(ctx: ToolContext, raw_path: str):
    if not raw_path:
        return None, ""
    path = _resolve(ctx, raw_path)
    if not path.is_file():
        return None, f"error: file not found: {raw_path}"
    return path, ""


async def _send_to(args: dict, ctx: ToolContext) -> str:
    if ctx.gateway is None or ctx.agent is None:
        return "error: gateway is not available"
    reason = directory_refusal(ctx.gateway)
    if reason:
        return f"error: {reason}"
    text = str(args.get("text") or "").strip()
    raw_path = str(args.get("path") or "").strip()
    if not text and not raw_path:
        return _known(ctx)
    raw_chat = str(args.get("chat") or "").strip()
    if not raw_chat:
        return "error: chat must be a group name, contact, or JID"
    target, target_error = _target(ctx, raw_chat)
    if target is None:
        return target_error
    thread_id, name = target
    grant, refusal = _relay(ctx, thread_id, args, text)
    if refusal:
        return refusal
    cross = _cross_refusal(ctx, thread_id, name, raw_chat)
    if cross:
        return cross
    path, path_error = _workspace_path(ctx, raw_path)
    if path_error:
        return path_error
    caption = str(args.get("caption") or "").strip()[:1024]
    results: list[str] = []
    entries: list[dict | None] = []
    if text:
        result, entry = await _deliver_text(ctx, thread_id, name, text, grant)
        results.append(result)
        entries.append(entry)
    if path is not None:
        result, entry = await _deliver_file(ctx, thread_id, name, path, caption, grant)
        results.append(result)
        entries.append(entry)
    ctx.sent.extend(entry for entry in entries if entry is not None)
    label = directory.label(thread_id, name)
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
        "chat_history chat=... before sending; blind sends are rejected. When forwarding another "
        "person's answer (a relay), pass relay_task and relay_from: the send is refused unless "
        "that person granted the relay (ADR-0023)."
    ),
    fn=_send_to,
    aliases=("kirim_ke",),
    parameters={
        "type": "object",
        "properties": {
            "chat": {
                "type": "string",
                "description": (
                    "Group/contact name, partial name, or JID @g.us/@s.whatsapp.net/@lid."
                ),
            },
            "text": {
                "type": "string",
                "description": "Text to send (optional when a path is given).",
            },
            "path": {
                "type": "string",
                "description": "File path inside the workspace (optional).",
            },
            "caption": {"type": "string", "description": "Optional file caption."},
            "relay_task": {
                "type": "string",
                "description": (
                    "Task id of the relay grant when forwarding another person's answer."
                ),
            },
            "relay_from": {
                "type": "string",
                "description": (
                    "Person id whose answer is being relayed; only that person can grant it."
                ),
            },
        },
        "additionalProperties": False,
    },
)
