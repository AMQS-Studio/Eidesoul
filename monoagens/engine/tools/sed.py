"""Tool sed: surgical text edits with an exact snippet or an anchor-verified line range."""


import hashlib
import re
import zlib

from .. import io
from ..registry import Handler
from . import ToolContext, _int, _resolve_write
from .read import _refusal

# A short hex prefix may stand in for the anchor: the block's sha256 or crc32 digest.
ANCHOR_HEX = re.compile(r"^[0-9a-f]{6,64}$")
SHOWN_LINES = 40
MODE_HINT = (
    "error: pass either `old` (exact snippet) or `start_line` with `anchor` (line range)"
)


def _shown(path, workspace) -> str:
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)


def _line_numbers(text: str, old: str, limit: int = 5) -> str:
    """The first `limit` 1-indexed line numbers where `old` starts."""
    found: list[int] = []
    start = 0
    while len(found) < limit:
        index = text.find(old, start)
        if index < 0:
            break
        found.append(text.count("\n", 0, index) + 1)
        start = index + len(old)
    return ", ".join(str(item) for item in found)


def _numbered(lines: list[str], start: int, end: int) -> str:
    chosen = lines[start - 1 : end][:SHOWN_LINES]
    body = "\n".join(f"{start + index}: {line}" for index, line in enumerate(chosen))
    hidden = end - start + 1 - len(chosen)
    return body + (f"\n(more: {hidden})" if hidden > 0 else "")


def _snippet(text: str, old: str, new: str, count: int) -> tuple[str, str] | str:
    """Mode 1: an exact snippet; `count=1` refuses when two sites would be ambiguous."""
    total = text.count(old)
    if total == 0:
        return "error: old text not found; read the file first and copy the exact snippet"
    if count == 0:
        return text.replace(old, new), f"replaced all {total} matches"
    if count == 1 and total > 1:
        return (
            f"error: old text matches {total} times (lines {_line_numbers(text, old)}); "
            "add surrounding context, or pass count=0 to replace every match"
        )
    limit = min(count, total)
    return text.replace(old, new, limit), f"replaced {limit} of {total} matches"


def _anchor_ok(anchor: str, block: str) -> bool:
    """A substring of the block, or a short sha256/crc32 hex prefix of it."""
    if anchor in block:
        return True
    token = anchor.strip().lower()
    if not ANCHOR_HEX.match(token):
        return False
    raw = block.encode("utf-8")
    return (
        hashlib.sha256(raw).hexdigest().startswith(token)
        or format(zlib.crc32(raw) & 0xFFFFFFFF, "08x").startswith(token)
    )


def _split(text: str) -> tuple[list[str], bool]:
    """Lines split on `\\n` only, plus whether the file ends with a newline."""
    trailing = text.endswith("\n")
    lines = text.split("\n")
    if trailing:
        lines.pop()
    return lines, trailing


def _replacement(new: str) -> list[str]:
    return _split(new)[0] if new else []


def _range_edit(
    text: str, start: int, end: int, anchor: str, new: str
) -> tuple[str, str] | str:
    """Mode 2: replace `lines[start-1:end]` only when `anchor` proves those lines are there."""
    lines, trailing = _split(text)
    total = len(lines)
    if start < 1:
        return "error: start_line is 1-indexed; use 1 for the first line"
    if end < start:
        return f"error: end_line {end} is before start_line {start}"
    if start > total:
        return f"error: start_line {start} is past the end of the file ({total} lines)"
    if end > total:
        return f"error: line range {start}-{end} is past the end of the file ({total} lines)"
    block = "\n".join(lines[start - 1 : end])
    if not _anchor_ok(anchor, block):
        return (
            f"error: anchor does not match lines {start}-{end}; current content is:\n"
            + _numbered(lines, start, end)
        )
    replacement = _replacement(new)
    lines[start - 1 : end] = replacement
    updated = "\n".join(lines) + ("\n" if trailing else "")
    return updated, f"replaced lines {start}-{end} with {len(replacement)} lines"


async def _sed(args: dict, ctx: ToolContext) -> str:
    path = _resolve_write(ctx, args.get("path", ""))
    refusal = _refusal(path, ctx, "sed")
    if refusal:
        return refusal
    if await io.run(path.is_dir):
        return f"error: not a file: {args.get('path')}"
    if not await io.run(path.is_file):
        return f"error: file not found: {args.get('path')}"
    text = await io.run(path.read_text, encoding="utf-8")
    new = str(args.get("new", ""))
    old = str(args.get("old", ""))
    if old:
        result = _snippet(text, old, new, max(0, _int(args, "count", 1)))
    elif "start_line" in args:
        start = _int(args, "start_line", 1)
        end = _int(args, "end_line", start)
        anchor = str(args.get("anchor", ""))
        if not anchor.strip():
            return (
                "error: anchor is required with start_line; copy a word from the target "
                "lines or a short digest of them"
            )
        result = _range_edit(text, start, end, anchor, new)
    else:
        return MODE_HINT
    if isinstance(result, str):
        return result
    updated, detail = result
    await io.run(path.write_text, updated, encoding="utf-8")
    workspace = await io.run(ctx.config.workspace.resolve)
    return f"ok: updated {_shown(path, workspace)} ({detail})"


TOOL = Handler(
    name="sed",
    target="file",
    path_arg="path",
    approval_class="consent",
    scan=("path",),
    family="write",
    description=(
        "Edit an existing file without rewriting it whole. Mode 1: `old` -> `new`, refused "
        "when `old` matches more than once and count=1; count=0 replaces every match. Mode 2: "
        "`start_line`..`end_line` (1-indexed) plus `anchor`, a substring or digest that must "
        "match those lines first, so shifted lines are never corrupted. Same roots as write: "
        "public/ is read-only, scratch/ is this chat; secrets and host config stay closed."
    ),
    fn=_sed,
    aliases=("edit", "replace"),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path inside the workspace (relative or absolute).",
            },
            "old": {
                "type": "string",
                "description": "Exact snippet to replace (mode 1).",
            },
            "new": {
                "type": "string",
                "description": "Replacement text; an empty string deletes the span.",
            },
            "count": {
                "type": "integer",
                "description": "Matches to replace in mode 1; 0 replaces all (default 1).",
                "minimum": 0,
            },
            "start_line": {
                "type": "integer",
                "description": "First line to replace, 1-indexed (mode 2).",
                "minimum": 1,
            },
            "end_line": {
                "type": "integer",
                "description": "Last line to replace, inclusive (defaults to start_line).",
                "minimum": 1,
            },
            "anchor": {
                "type": "string",
                "description": (
                    "Required in mode 2: a substring of those lines, or a short sha256/crc "
                    "hex prefix of them."
                ),
            },
        },
        "required": ["path", "new"],
        "additionalProperties": False,
    },
)
