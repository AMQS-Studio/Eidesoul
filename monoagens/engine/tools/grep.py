"""Tool grep: file-content search under the same roots as `read` (ADR-0003 §4.2)."""


import fnmatch
import re
from pathlib import Path

from .. import io
from ..registry import Handler
from . import ToolContext, _int, _resolve
from .read import _refusal

MODES = ("matches", "count", "files")
BINARY_PROBE = 8192
MAX_LIMIT = 200


def _relative(path: Path, ctx: ToolContext) -> str:
    try:
        return str(path.relative_to(ctx.config.workspace.resolve()))
    except ValueError:
        return str(path)


def _walk(root: Path, ctx: ToolContext, pattern: str) -> list[Path]:
    """Blocking walk under one root; offloaded by `_files` (AGENTS.md §2)."""
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.is_symlink() and not path.resolve().is_relative_to(root.resolve()):
            continue
        if pattern and not (
            fnmatch.fnmatch(_relative(path, ctx), pattern)
            or fnmatch.fnmatch(path.name, pattern)
        ):
            continue
        found.append(path)
    return found


async def _files(ctx: ToolContext, raw_path: str, pattern: str) -> list[Path]:
    """Deterministic file list; a symlink escaping the root is already rejected by `_resolve`."""
    if raw_path:
        target = _resolve(ctx, raw_path)
        if await io.run(target.is_file):
            return [target]
        if not await io.run(target.is_dir):
            return []
        roots = [target]
    else:
        roots = list(ctx.roots)
    found: list[Path] = []
    for root in roots:
        if not await io.run(root.is_dir):
            continue
        found.extend(await io.run(_walk, root, ctx, pattern))
    return sorted(dict.fromkeys(found))


def _probe(path: Path, max_bytes: int) -> bool:
    """Blocking size/binary probe; offloaded by `_searchable` (AGENTS.md §2)."""
    try:
        if path.stat().st_size > max_bytes:
            return False
        with path.open("rb") as handle:
            return b"\x00" not in handle.read(BINARY_PROBE)
    except OSError:
        return False


async def _searchable(path: Path, ctx: ToolContext) -> bool:
    if _refusal(path, ctx):
        return False
    return await io.run(_probe, path, ctx.config.read.grep.max_file_bytes)


def _text(path: Path) -> str:
    """Blocking file read; offloaded by `_match` (AGENTS.md §2)."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


async def _match(pattern: re.Pattern[str], path: Path) -> list[tuple[int, str]]:
    text = await io.run(_text, path)
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if pattern.search(line):
            found.append((number, line.strip()[:240]))
    return found


def _render(rows: list[tuple[Path, list[tuple[int, str]]]], ctx: ToolContext, mode: str) -> str:
    if mode == "count":
        lines = [f"{_relative(path, ctx)}: {len(hits)}" for path, hits in rows]
        total = sum(len(hits) for _, hits in rows)
        return "\n".join([f"{total} matches in {len(rows)} files", *lines])
    if mode == "files":
        return "\n".join([f"{len(rows)} files", *(_relative(path, ctx) for path, _ in rows)])
    lines: list[str] = []
    for path, hits in rows:
        for number, text in hits:
            lines.append(f"{_relative(path, ctx)}:{number}: {text}")
    return "\n".join(lines)


async def _collect(
    ctx: ToolContext,
    raw_path: str,
    pattern: re.Pattern[str],
    glob: str,
    limit: int,
    budget: int,
) -> tuple[list[tuple[Path, list[tuple[int, str]]]], bool]:
    rows: list[tuple[Path, list[tuple[int, str]]]] = []
    used = 0
    cut = False
    for path in await _files(ctx, raw_path, glob):
        if not await _searchable(path, ctx):
            continue
        hits = await _match(pattern, path)
        if not hits:
            continue
        seen = sum(len(item[1]) for item in rows)
        take = hits[: max(0, limit - seen)]
        if not take:
            cut = True
            break
        if len(take) < len(hits):
            cut = True
        rows.append((path, take))
        used += sum(len(text) + 12 for _, text in take)
        if used >= budget or sum(len(item[1]) for item in rows) >= limit:
            cut = True
            break
    return rows, cut


async def _grep(args: dict, ctx: ToolContext) -> str:
    raw = str(args.get("pattern") or "").strip()
    if not raw:
        return "error: pattern is empty"
    try:
        pattern = re.compile(raw)
    except re.error as exc:
        return f"error: invalid pattern: {exc}"
    mode = str(args.get("mode") or "matches").strip().lower()
    if mode not in MODES:
        return "error: mode must be matches, count, or files"
    glob = str(args.get("glob") or "").strip()
    limit = min(MAX_LIMIT, max(1, _int(args, "limit", ctx.config.read.grep.max_matches)))
    budget = int(ctx.config.read.grep.max_output_chars)
    rows, cut = await _collect(
        ctx, str(args.get("path") or ""), pattern, glob, limit, budget
    )
    if not rows:
        return "No matches."
    body = _render(rows, ctx, mode)
    if cut:
        body += f"\n(next: narrow path/glob or raise limit; limit={limit})"
    return body


TOOL = Handler(
    name="grep",
    target="file",
    path_arg="path",
    approval_class="none",
    scan=("path",),
    family="read",
    description=(
        "Search file contents with a regex under public/ (shared) and scratch/ (this chat); "
        "trusted roles search the whole workspace. mode=matches lists path:line:text, "
        "mode=count and mode=files summarize without line content. Search first with grep, "
        "then read a small range; do not read whole files to find one symbol."
    ),
    fn=_grep,
    max_output_chars=4_000,
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regular expression."},
            "path": {"type": "string", "description": "File or directory inside the workspace."},
            "glob": {"type": "string", "description": "Filter, e.g. *.py or scratch/**/*.md."},
            "mode": {
                "type": "string",
                "enum": ["matches", "count", "files"],
                "description": "matches (default), count, or files.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_LIMIT,
                "description": "Maximum matches (default from config, max 200).",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
)
