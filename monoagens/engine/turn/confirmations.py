"""Confirmation vocabulary and the staged-approval payload helpers (ADR-0006 §4.5)."""


import json
import re
from collections.abc import Mapping

from ..character import lexicon

PENDING_HEADER = "## Tool result: pending_approval"
CONFIRM_MAX_WORDS = 12
CONFIRM_MAX_CHARS = 160


def _cues(
    language: str,
) -> tuple[frozenset[str], frozenset[str], frozenset[str], frozenset[str]]:
    """The pack's confirmation vocabulary, falling back to English when a pack has none."""
    pack = lexicon.load(language)
    if pack.confirm:
        return pack.confirm, pack.reject, pack.modify, pack.filler
    english = lexicon.load("en")
    return english.confirm, english.reject, english.modify, english.filler


def confirmation_intent(text: object, language: str = "") -> str:
    """`confirm`, `reject`, `modify`, or "" for a short reply to a staged action.

    ADR-0006 §4.5: the classifier only reads a short answer whose first word (or whose whole
    body) is a confirmation cue; a longer message is a new request, not a decision. The cues
    are language data from the pack, never a literal in this module.
    """
    body = " ".join(str(text or "").strip().split())
    if not body or len(body) > CONFIRM_MAX_CHARS:
        return ""
    words = re.findall(r"[a-z0-9]+", body.casefold())
    if not words or len(words) > CONFIRM_MAX_WORDS:
        return ""
    confirm, reject, modify, filler = _cues(language)
    if words[0] in reject:
        return "reject"
    if words[0] in modify:
        return "modify"
    if all(word in confirm | filler for word in words):
        return "confirm"
    if words[0] in confirm:
        return "confirm"
    return ""


def pending_result(
    rows, status: str, receipts: Mapping[int, str] | None = None
) -> str:
    """The structured `pending_approval` tool result the model reads on the next call."""
    found = []
    for row in rows:
        item = {
            "approval_id": int(row.id),
            "kind": str(row.kind),
            "tool": str(row.tool),
            "summary": str(row.summary),
            "target": str(row.thread_id),
            "approve_command": f"/approve {row.id}",
        }
        if receipts is not None and row.id in receipts:
            item["result"] = str(receipts[row.id])
        found.append(item)
    return json.dumps(
        {"type": "pending_approval", "status": status, "approvals": found},
        ensure_ascii=False,
    )


def with_tool_result(content, block: str):
    """Prepend a synthetic tool result to the turn payload (str or multimodal list)."""
    if not block:
        return content
    text = f"{PENDING_HEADER}\n{block}"
    if isinstance(content, list):
        return [{"type": "text", "text": text}, *content]
    return f"{text}\n\n{content}"
