"""Discord rendering: native embeds, components, and fence-aware chunking (ADR-0010 §4.4).

Pure functions only: an `Action.meta["ui"]` block becomes an `Embed` payload and a component
row, and a reply is split at Discord's 2000-character ceiling without cutting a code fence.
"""


import re
from collections.abc import Mapping

LIMIT = 2000
COMPONENT_PREFIX = "cmd:"
CUSTOM_ID_LIMIT = 100
LABEL_LIMIT = 80
TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
PRIMARY = 3
DANGER = 4
FENCE = re.compile(r"^\s*```")
BULLET = re.compile(r"^\s*[-*]\s+", re.MULTILINE)
STYLES = {"primary": PRIMARY, "danger": DANGER, "secondary": 2, "success": 3}


def embed(ui: object) -> dict | None:
    """`meta["ui"]` -> one `Embed` payload; None when the block carries no readable card."""
    if not isinstance(ui, Mapping):
        return None
    title = str(ui.get("title") or "").strip()
    summary = str(ui.get("summary") or "").strip()
    if not title and not summary:
        return None
    payload: dict = {}
    if title:
        payload["title"] = title[:TITLE_LIMIT]
    if summary:
        payload["description"] = summary[:DESCRIPTION_LIMIT]
    payload["color"] = 0xE74C3C if ui.get("kind") == "approval" else 0x5865F2
    return payload


def components(ui: object) -> list[dict]:
    """`meta["ui"]` -> one action row of buttons (or a select for `choices`)."""
    if not isinstance(ui, Mapping):
        return []
    found = ui.get("buttons") or ui.get("choices") or ()
    if not isinstance(found, (list, tuple)):
        return []
    row: list[dict] = []
    for item in found:
        if not isinstance(item, Mapping):
            continue
        command = str(item.get("command") or "").strip()
        label = str(item.get("label") or item.get("id") or "").strip()
        if not command or not label:
            continue
        custom_id = f"{COMPONENT_PREFIX}{command}"
        if len(custom_id) > CUSTOM_ID_LIMIT:
            continue
        row.append(
            {
                "type": 2,
                "style": STYLES.get(str(item.get("style") or ""), 2),
                "label": label[:LABEL_LIMIT],
                "custom_id": custom_id,
            }
        )
        if len(row) == 5:
            break
    return [{"type": 1, "components": row}] if row else []


def chunk(text: str, limit: int = LIMIT) -> list[str]:
    """Split a reply at line boundaries, reopening a code fence in every piece it spans."""
    body = str(text or "").strip()
    if not body:
        return []
    if len(body) <= limit:
        return [body]
    return _pack(_split_long_lines(body.splitlines(), limit), limit)


def _split_long_lines(lines: list[str], limit: int) -> list[str]:
    found: list[str] = []
    for line in lines:
        while len(line) > limit:
            found.append(line[:limit])
            line = line[limit:]
        found.append(line)
    return found


def _pack(lines: list[str], limit: int) -> list[str]:
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    fence = ""
    for line in lines:
        reserve = 4 if fence else 0
        if current and size + len(line) + 1 + reserve > limit:
            if fence:
                current.append("```")
            pieces.append("\n".join(current))
            current = [fence] if fence else []
            size = len(fence) + 1 if fence else 0
        if FENCE.match(line):
            fence = "" if fence else line
        current.append(line)
        size += len(line) + 1
    if current:
        pieces.append("\n".join(current))
    return [piece for piece in pieces if piece.strip()]


def plain(text: str) -> str:
    """The dialect Discord renders: markdown bullets become `•`, headings lose their hashes."""
    cleaned = BULLET.sub("• ", str(text or ""))
    return re.sub(r"(?m)^#{1,6}\s+", "", cleaned)
