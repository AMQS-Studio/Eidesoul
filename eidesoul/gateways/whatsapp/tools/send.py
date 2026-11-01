"""Tool send: send a workspace file to the active chat."""

from __future__ import annotations

import logging

from ....engine.registry import Handler
from ....engine.tools import ToolContext, _resolve

log = logging.getLogger(__name__)


async def _send(args: dict, ctx: ToolContext) -> str:
    if ctx.agent is None or ctx.gateway is None:
        return "error: sending is not available"
    raw = str(args.get("path") or "").strip()
    if not raw:
        return "error: empty path"
    path = _resolve(ctx, raw)
    if not path.is_file():
        return f"error: file not found: {raw}"
    caption = str(args.get("caption") or "").strip()[:1024]
    result = await ctx.agent.send_file(ctx.chat_id, path, caption, gateway=ctx.gateway)
    ok = result.startswith("ok:")
    note = f"{path.name}{f' — {caption}' if caption else ''}"
    if ok:
        ctx.sent.append(
            {"tool": "send", "target": ctx.chat_id, "kind": "file", "summary": path.name}
        )
    if ctx.ledger is not None:
        try:
            ctx.ledger.log_action(
                ctx.chat_id,
                ctx.chat_id,
                "file",
                note if ok else f"failed — {result}",
                source_name="chat",
                target_name=ctx.gateway.chat_name(ctx.chat_id),
                request=ctx.request,
            )
        except Exception:
            log.exception("failed to log the send action chat=%s", ctx.chat_id)
    return result


TOOL = Handler(
    name="send",
    family="write",
    description=(
        "Send a file from public/ or scratch/; trusted roles may send other workspace paths "
        "(sticker/photo/document chosen automatically)."
    ),
    fn=_send,
    aliases=("kirim_file",),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path inside the workspace."},
            "caption": {"type": "string", "description": "Optional caption (ignored for stickers)."},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
)
