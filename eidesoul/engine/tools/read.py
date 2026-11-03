"""Tool read: read a text file in the workspace with line numbers."""


import re

from ..registry import Handler
from . import ToolContext, _int, _resolve

# ADR-0015 §4.7: configuration and secrets have their own door (`settings`), never `read`.
SECRET_FILE = re.compile(r"(^|/)(\.env(\.[^/]*)?|[^/]*\.env|[^/]*\.pem|[^/]*\.key)$")
CONFIG_FILES = frozenset({"config.yaml", "roles.yaml", "soul.yaml"})


def _refusal(path, ctx: ToolContext) -> str:
    if SECRET_FILE.search(str(path)):
        return (
            "error: secret files are not readable through read; ask the operator "
            "(environment variables and credentials stay closed)"
        )
    name = path.name.lower()
    if name in CONFIG_FILES or path == ctx.config.paths.config:
        return (
            "error: host configuration is read through the settings tool "
            "(settings get/schema), not read"
        )
    return ""


async def _read(args: dict, ctx: ToolContext) -> str:
    path = _resolve(ctx, args.get("path", ""))
    refusal = _refusal(path, ctx)
    if refusal:
        return refusal
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
    body = "\n".join(f"{start + i}: {line}" for i, line in enumerate(chosen))
    if start - 1 + len(chosen) < len(lines):
        body += f"\n(next: start={start + len(chosen)})"
    return body


TOOL = Handler(
    name="read",
    family="read",
    description=(
        "Read a text file under public/ (shared) or scratch/ (this chat); any other "
        "workspace path is private and only trusted roles may read it. Use grep first to "
        "find a symbol; configuration and secrets are read through settings, not read."
    ),
    fn=_read,
    aliases=("cat",),
    max_output_chars=20_000,
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path inside the workspace (relative or absolute).",
            },
            "start": {"type": "integer", "description": "Start line (default 1)."},
            "limit": {"type": "integer", "description": "Number of lines (default 400)."},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
)
