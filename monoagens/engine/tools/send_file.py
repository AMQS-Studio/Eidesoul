"""Tool send_file: send a workspace file or a forwarded attachment to a room (ADR-0001 §4.3).

A file that arrived with a message has a durable `att_…` handle; forwarding it goes through
`AttachmentStore`, so a body can forward the file to any room or body without reading the
archive. A workspace path is resolved through the caller's roots exactly like `read`.
"""


import logging
from pathlib import Path
from typing import Any

from ..inbound import (
    AUTHORIZATION,
    CONSENT,
    Action,
    Address,
    GatewayError,
    SendRefused,
    StandbyError,
    tool_action_id,
)
from ..registry import Handler
from ..stores.attachments import is_handle
from . import ToolContext, _resolve, body_of, record_send, resolve_address

log = logging.getLogger(__name__)


def _id_kwargs(found: str) -> dict[str, Any]:
    """An explicit action id only when an approval row already named one."""
    return {"id": found} if found else {}


def _origin(ctx: ToolContext) -> Address:
    return Address(str(getattr(ctx.gateway, "name", "") or ""), str(ctx.thread_id))


def _attachment_store(ctx: ToolContext):
    return getattr(ctx.history, "attachments", None)


def _handle_path(ctx: ToolContext, raw: str) -> tuple[Path | None, str]:
    store = _attachment_store(ctx)
    if store is None:
        return None, "error: attachment handles are not available"
    path = store.materialize(raw, ctx.config.workspace)
    if path is None:
        return None, f"error: unknown attachment handle: {raw}"
    return Path(path), ""


def _source(ctx: ToolContext, raw: str) -> tuple[Path | None, str]:
    if is_handle(raw):
        return _handle_path(ctx, raw)
    try:
        path = _resolve(ctx, raw)
    except PermissionError as exc:
        return None, f"error: {exc}"
    if not path.is_file():
        return None, f"error: file not found: {raw}"
    return path, ""


def _label(body, address: Address) -> str:
    name = str(getattr(body, "chat_name", lambda thread: "")(address.thread) or "")
    return name or address.thread


def _target(ctx: ToolContext, body, raw_chat: str) -> tuple[Address | None, str]:
    chat = str(raw_chat or "").strip()
    if not chat:
        return Address(str(getattr(body, "name", "") or ""), str(ctx.thread_id), "gateway"), ""
    return resolve_address(ctx, body, chat)


def _reason_refusal(address: Address, reason: str) -> str:
    if address.proof or reason:
        return ""
    return (
        "denied: an archived target needs no reason, a direct address does; "
        "pass the requester's reason"
    )


async def _local(
    ctx: ToolContext, path: Path, caption: str, reason: str, part: int = -1
) -> str:
    """The active room goes through the one delivery path (`Rich`, fallback, and outbox)."""
    action_id = tool_action_id(f"file:{part}") if part >= 0 else ""
    result = await ctx.agent.send_file(
        ctx.thread_id,
        path,
        caption,
        gateway=ctx.gateway,
        subject=str(ctx.person_id or ctx.sender),
        origin=_origin(ctx),
        reason=reason,
        action_id=action_id,
    )
    ok = not str(result).startswith("error:")
    label = _label(ctx.gateway, _origin(ctx))
    record_send(ctx, ctx.thread_id, label, "file", f"{path.name} — {result}", ok=ok)
    if not ok:
        return result
    ctx.sent.append(
        {"tool": "send_file", "target": ctx.thread_id, "kind": "file", "summary": path.name}
    )
    return f"ok: file sent: {path.name}"


async def _forward(
    ctx: ToolContext,
    address: Address,
    label: str,
    path: Path,
    caption: str,
    reason: str,
    part: int = -1,
) -> str:
    found = tool_action_id(f"file:{part}") if part >= 0 else tool_action_id()
    action = Action(
        address=address,
        capability="send_file",
        payload={"path": str(path), "caption": caption},
        approval_class=CONSENT if address.proof else AUTHORIZATION,
        **_id_kwargs(found),
    )
    try:
        receipt = await ctx.agent.dispatch(
            str(ctx.person_id or ctx.sender), address, action, origin=_origin(ctx), reason=reason
        )
    except StandbyError:
        return "error: delivery refused"
    except SendRefused as exc:
        return f"error: {exc}"
    except GatewayError as exc:
        return f"error: failed to send file: {exc}"
    if receipt.state != "sent":
        record_send(ctx, address.thread, label, "file", str(receipt.reason), ok=False)
        if receipt.state == "pending":
            return f"error: delivery needs approval ({receipt.reason})"
        return f"error: {receipt.reason or f'delivery is {receipt.state}'}"
    note = f"{path.name}{f' — {caption}' if caption else ''}"
    record_send(ctx, address.thread, label, "file", note)
    ctx.sent.append(
        {"tool": "send_file", "target": address.thread, "kind": "file", "summary": path.name}
    )
    return f"ok: file sent to {label}: {path.name}"


def _path_items(raw: object) -> list[str]:
    """`path` is one string or a list of paths/handles; blank entries are dropped."""
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    return [str(item).strip() for item in items if str(item).strip()]


async def _send_one(args: dict, ctx: ToolContext, raw: str, part: int = -1) -> str:
    path, refusal = _source(ctx, raw)
    if path is None:
        return refusal
    body = body_of(ctx, str(args.get("gateway") or ""))
    if body is None:
        return "error: the requested body is not available"
    address, refusal = _target(ctx, body, str(args.get("chat") or ""))
    if address is None:
        return refusal
    reason = str(args.get("reason") or "").strip()
    refusal = _reason_refusal(address, reason)
    if refusal:
        return refusal
    caption = str(args.get("caption") or "").strip()
    if body is ctx.gateway and address.thread == ctx.thread_id:
        return await _local(ctx, path, caption, reason, part)
    return await _forward(ctx, address, _label(body, address), path, caption, reason, part)


async def _send_file(args: dict, ctx: ToolContext) -> str:
    if ctx.agent is None or ctx.gateway is None:
        return "error: sending is not available"
    items = _path_items(args.get("path"))
    if not items:
        return "error: empty path"
    multi = len(items) > 1
    return "\n".join(
        [await _send_one(args, ctx, raw, index if multi else -1) for index, raw in enumerate(items)]
    )


TOOL = Handler(
    name="send_file",
    family="write",
    target="room",
    approval_class="consent",
    max_output_chars=4000,
    description=(
        "Send a file from public/ or scratch/, or forward an attachment by its `att_…` handle, "
        "to the active room, another archived room, or another body. chat = room/contact name "
        "or address (empty = the active room); gateway = the body name (empty = this body). A "
        "direct target the archive does not know needs a reason and is evaluated as "
        "authorization."
    ),
    fn=_send_file,
    aliases=("send",),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ],
                "description": (
                    "Workspace path (e.g. 'scratch/report.csv'), attachment handle "
                    "(e.g. 'att_...'), or a list of either to send several files in order."
                ),
            },
            "chat": {
                "type": "string",
                "description": "Room/contact name or address; empty = the active room.",
            },
            "caption": {"type": "string", "description": "Optional caption."},
            "gateway": {
                "type": "string",
                "description": "Target body name; empty = this body.",
            },
            "reason": {
                "type": "string",
                "description": "Why the requester wants this sent (cross-room and direct).",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
)
