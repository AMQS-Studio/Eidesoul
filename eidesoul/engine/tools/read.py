"""Tool read: read a text file in the workspace with line numbers."""

from __future__ import annotations

from ..registry import Handler
from . import ToolContext, _int, _resolve


async def _read(args: dict, ctx: ToolContext) -> str:
    path = _resolve(ctx, args.get("path", ""))
    if not path.is_file():
        return f"error: file not found: {args.get('path')}"
    start = max(1, _int(args, "start", 1))
    limit = max(1, _int(args, "limit", 400))
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return "(empty file)"
    chosen = lines[start - 1 : start - 1 + limit]
    if not chosen:
        return f"(no lines in range {start}-{start + limit - 1}; the file has {len(lines)} lines)"
    return "\n".join(f"{start + i}: {line}" for i, line in enumerate(chosen))


TOOL = Handler(
    name="read",
    family="read",
    description=(
        "Read a text file under public/ (shared) or scratch/ (this chat); any other "
        "workspace path is private and only trusted roles may read it."
    ),
    fn=_read,
    aliases=("cat",),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path inside the workspace (relative or absolute)."},
            "start": {"type": "integer", "description": "Start line (default 1)."},
            "limit": {"type": "integer", "description": "Number of lines (default 400)."},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
)
