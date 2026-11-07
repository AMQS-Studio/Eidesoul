"""Slack rendering: Block Kit blocks from transport-neutral UI (ADR-0010 §4.5).

Pure functions only: an approval block becomes a `header` + `section` + `actions` set, a step or
a thinking line becomes a `context` block, and a long reply is split under Slack's own ceiling.
"""


import re
from collections.abc import Mapping

LIMIT = 39000
COMPONENT_PREFIX = "cmd:"
ACTION_ID_LIMIT = 255
TEXT_LIMIT = 3000
BULLET = re.compile(r"^\s*[-*]\s+", re.MULTILINE)


def buttons_of(ui: object) -> list[Mapping]:
    if not isinstance(ui, Mapping):
        return []
    found = ui.get("buttons") or ui.get("choices") or ()
    if not isinstance(found, (list, tuple)):
        return []
    return [item for item in found if isinstance(item, Mapping)]


def approval_blocks(ui: object) -> list[dict]:
    """`meta["ui"]` -> Block Kit; the readable text is already the message's own `text`."""
    if not isinstance(ui, Mapping):
        return []
    blocks: list[dict] = []
    title = str(ui.get("title") or "").strip()
    summary = str(ui.get("summary") or "").strip()
    if title:
        blocks.append(
            {"type": "header", "text": {"type": "plain_text", "text": title[:150]}}
        )
    if summary:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{summary[:TEXT_LIMIT]}*"},
            }
        )
    buttons = []
    for item in buttons_of(ui):
        command = str(item.get("command") or "").strip()
        label = str(item.get("label") or item.get("id") or "").strip()
        if not command or not label:
            continue
        action_id = f"{COMPONENT_PREFIX}{command}"
        if len(action_id) > ACTION_ID_LIMIT:
            continue
        buttons.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": label[:75]},
                "action_id": action_id,
                "value": command,
                "style": "danger" if item.get("style") == "danger" else "primary",
            }
        )
        if len(buttons) == 5:
            break
    if buttons:
        blocks.append({"type": "actions", "elements": buttons})
    return blocks


def progress_blocks(capability: str, text: str) -> list[dict]:
    """A progress line as one `context` block; thinking is labelled, steps are not."""
    body = str(text or "").strip()
    if not body:
        return []
    if capability == "send_thinking":
        body = f"🧠 {body}"
    return [{"type": "context", "elements": [{"type": "mrkdwn", "text": body[:TEXT_LIMIT]}]}]


def chunk(text: str, limit: int = LIMIT) -> list[str]:
    """Split prose at line boundaries; Slack's hard ceiling is far above a normal reply."""
    body = str(text or "").strip()
    if not body:
        return []
    found: list[str] = []
    current = ""
    for line in body.splitlines():
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
    """Slack's own dialect: bullets become `•` and headings lose their hashes."""
    cleaned = BULLET.sub("• ", str(text or ""))
    return re.sub(r"(?m)^#{1,6}\s+", "*", cleaned)
