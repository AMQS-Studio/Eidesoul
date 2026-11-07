"""Telegram rendering: Bot API payloads from transport-neutral blocks (ADR-0010 §4.3).

Nothing here talks to the network. `inline_keyboard` turns `Action.meta["ui"]` into an
`InlineKeyboardMarkup`, `progress_body` renders a thinking line as an expandable blockquote, and
`chunk` splits a reply at Telegram's 4096-character ceiling.
"""


import html
import re
from collections.abc import Mapping

LIMIT = 4096
CALLBACK_PREFIX = "cmd:"
CALLBACK_LIMIT = 64
LABEL_LIMIT = 64
BULLET = re.compile(r"^\s*[-*]\s+", re.MULTILINE)


def escape(text: object) -> str:
    """HTML-escape text for `parse_mode="HTML"`; the entities the catalog emits survive."""
    return html.escape(str(text or ""), quote=False)


def buttons_of(ui: object) -> list[Mapping]:
    """The pressable items of one UI block: `buttons` first, then `choices`."""
    if not isinstance(ui, Mapping):
        return []
    found = ui.get("buttons") or ui.get("choices") or ()
    if not isinstance(found, (list, tuple)):
        return []
    return [item for item in found if isinstance(item, Mapping)]


def inline_keyboard(ui: object) -> dict | None:
    """`meta["ui"]` -> `InlineKeyboardMarkup`; None when there is nothing to press."""
    rows: list[list[dict]] = []
    for item in buttons_of(ui):
        command = str(item.get("command") or "").strip()
        label = str(item.get("label") or item.get("id") or "").strip()
        if not command or not label:
            continue
        data = f"{CALLBACK_PREFIX}{command}"
        if len(data.encode("utf-8")) > CALLBACK_LIMIT:
            continue
        rows.append([{"text": label[:LABEL_LIMIT], "callback_data": data}])
    return {"inline_keyboard": rows} if rows else None


def approval_card(ui: Mapping) -> str:
    """The readable card above the buttons; `Action.payload` already carries the same words."""
    title = str(ui.get("title") or "").strip()
    summary = str(ui.get("summary") or "").strip()
    parts = []
    if title:
        parts.append(f"<b>{escape(title)}</b>")
    if summary:
        parts.append(f"<blockquote>{escape(summary)}</blockquote>")
    return "\n".join(parts)


def body(capability: str, text: str) -> str:
    """The HTML body of one progress bubble; thinking collapses, everything else is plain."""
    if capability == "send_thinking":
        return f"<blockquote expandable>{escape(text)}</blockquote>"
    return escape(text)


def chunk(text: str, limit: int = LIMIT) -> list[str]:
    """Split prose at line boundaries, hard-splitting only a line longer than `limit`."""
    body_text = str(text or "").strip()
    if not body_text:
        return []
    found: list[str] = []
    current = ""
    for line in body_text.splitlines():
        pieces = [line] if len(line) <= limit else [
            line[start : start + limit] for start in range(0, len(line), limit)
        ]
        for piece in pieces:
            candidate = f"{current}\n{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                found.append(current)
            current = piece
    if current:
        found.append(current)
    return found


def plain(text: str) -> str:
    """The dialect Telegram renders: markdown bullets become `•`, headings lose their hashes."""
    cleaned = BULLET.sub("• ", str(text or ""))
    return re.sub(r"(?m)^#{1,6}\s+", "", cleaned)
