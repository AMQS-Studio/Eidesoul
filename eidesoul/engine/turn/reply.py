"""Reply text shaping: the body dialect, bubbles, chunking, and the send plan.

The transport dialect stays in the body (`render`); the outbound path itself lives in
`delivery.py` (ADR-0031 §4.2, ADR-0037 §4.3): this module only decides what the bubbles are.
"""


import re

from ..i18n import t

BLOCK = re.compile(r"<(reasoning|thought|tool_call)>.*?</\1>", re.IGNORECASE | re.DOTALL)
FENCE = re.compile(r"(```.*?```)", re.DOTALL)
FENCE_START = "```"
BOLD = re.compile(r"\*\*(.+?)\*\*")
BULLET = re.compile(r"^[-*]\s+", re.MULTILINE)
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
BUBBLE = re.compile(r"(?m)^\s*-{3,}\s*$")  # a "---" line alone = new bubble; the model's choice
BLANK = re.compile(r"\n\s*\n")
LIST_LINE = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)]|\d\ufe0f?\u20e3)\s")
CASUAL_BUBBLE_MAX = 300


def strip_blocks(text: str) -> str:
    return BLOCK.sub("", text or "")


def inline_breaks(text: str) -> str:
    """'one --- two' -> a real bubble break; a linear scan, no backtracking on a space run."""
    out: list[str] = []
    index = 0
    size = len(text)
    while index < size:
        space = index
        while space < size and text[space] in " \t":
            space += 1
        if space == index:
            out.append(text[index])
            index += 1
            continue
        dashes = space
        while dashes < size and text[dashes] == "-":
            dashes += 1
        after = dashes
        while after < size and text[after] in " \t":
            after += 1
        if dashes - space >= 3 and after > dashes:
            out.append("\n---\n")
            index = after
            continue
        out.append(text[index:space])
        index = space
    return "".join(out)


def _heading(line: str) -> str:
    """'## Title' -> '*Title*'; other lines are returned as-is."""
    marks = len(line) - len(line.lstrip("#"))
    rest = line[marks:]
    if not 1 <= marks <= 6 or rest[:1] not in (" ", "\t") or len(rest) < 2:
        return line
    body = rest.lstrip(" \t")
    return f"*{body}*"


def clean_prose(chunk: str) -> str:
    """Markdown -> body dialect (a rich body renders it; a plain body keeps identity)."""
    chunk = "\n".join(_heading(line) for line in chunk.split("\n"))
    chunk = BOLD.sub(r"*\1*", chunk)
    chunk = BULLET.sub("• ", chunk)
    return LINK.sub(r"\1 (\2)", chunk)


def _chunks(text: str, limit: int) -> list[str]:
    parts: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")
    if rest.strip():
        parts.append(rest.strip())
    return [part for part in parts if part]


def split_text(text: str, limit: int = 10000) -> list[str]:
    cleaned = re.sub(r"\n{3,}", "\n\n", strip_blocks(text).strip())
    return _chunks(cleaned, limit) if cleaned else []


def human_chunks(text: str, *, max_parts: int = 3) -> list[str]:
    """Text -> bubbles as-is; the model marks boundaries with a `---` line."""
    body = strip_blocks(str(text or "")).strip()
    if not body:
        return []
    parts: list[str] = []
    current = ""
    for piece in FENCE.split(body):
        if not piece.strip():
            continue
        if piece.lstrip().startswith(FENCE_START):
            current = f"{current}\n{piece}".strip() if current else piece.strip()
            continue
        piece = inline_breaks(piece)
        for index, part in enumerate(BUBBLE.split(piece)):
            chunk = part.strip()
            if not chunk:
                continue
            if index == 0:
                current = f"{current}\n{chunk}".strip() if current else chunk
                continue
            if current:
                parts.append(current)
            current = chunk
    if current:
        parts.append(current)
    if len(parts) == 1 and FENCE_START not in body:
        paragraphs = [part.strip() for part in BLANK.split(parts[0]) if part.strip()]
        casual = (
            len(paragraphs) > 1
            and not LIST_LINE.search(parts[0])
            and all(len(part) <= CASUAL_BUBBLE_MAX for part in paragraphs)
        )
        if casual:
            parts = paragraphs
    cap = max(1, int(max_parts))
    if len(parts) > cap:
        parts = [*parts[: cap - 1], "\n\n".join(parts[cap - 1 :])]
    return parts


def plan_chunks(
    reply: str | None,
    *,
    gateway,
    language: str,
    is_command: bool,
    professional: bool,
    humanize,
) -> tuple[list[str], bool, bool]:
    """The reply as chunks, plus `pace` and `quote_all`: one policy for every caller."""
    if is_command or professional:
        chunks = split_text(gateway.render(reply or "")) or [t("agent.empty_reply", language)]
        return chunks, False, True
    planned = max(1, int(humanize.max_bubbles))
    chunks = [
        gateway.render(chunk) for chunk in human_chunks(reply or "", max_parts=planned)
    ] or [t("agent.empty_reply", language)]
    pace = bool(humanize.enabled)
    return chunks, pace, not pace
