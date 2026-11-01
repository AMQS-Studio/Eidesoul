"""Payload assembly: the newest tail plus every message this turn quotes, under a budget.

Deterministic, archive-backed, and never LLM-driven: the model sees the messages a reader would
need to answer this one, not an arbitrary count of rows.
"""

from __future__ import annotations

import logging

from .context import ChatMessage

log = logging.getLogger(__name__)

TAIL_MIN_MESSAGES = 6
TAIL_MAX_MESSAGES = 20
TAIL_CHARS = 1200
ANCHOR_LIMIT = 3
SCAN = TAIL_MAX_MESSAGES * 4


def _rows(history, chat_id: str, window) -> list[ChatMessage]:
    if history is not None:
        try:
            return list(history.recent(chat_id, SCAN))
        except Exception:  # a broken archive must not break the payload
            log.exception("assembly: failed to read the archive chat=%s", chat_id)
    return list(window or [])


def _find(history, chat_id: str, message_id: str, window) -> ChatMessage | None:
    if not message_id:
        return None
    for entry in reversed(list(window or [])):
        if entry.id == message_id:
            return entry
    if history is None:
        return None
    try:
        return history.find(chat_id, message_id)
    except Exception:
        log.exception("assembly: failed to read message chat=%s id=%s", chat_id, message_id)
        return None


def _tail(rows: list[ChatMessage], current: ChatMessage) -> list[ChatMessage]:
    """Newest rows under the character budget, never fewer than the floor."""
    picked: list[ChatMessage] = []
    chars = 0
    for entry in reversed(rows):
        if _same(entry, current):
            continue
        if len(picked) + 1 >= TAIL_MAX_MESSAGES:
            break
        picked.append(entry)
        chars += len(entry.text or "")
        if chars >= TAIL_CHARS and len(picked) + 1 >= TAIL_MIN_MESSAGES:
            break
    return [*reversed(picked), current]


def _anchors(
    history, chat_id: str, current: ChatMessage, window, tail: list[ChatMessage]
) -> list[ChatMessage]:
    """The quote chain of this message, newest first, capped at `ANCHOR_LIMIT`."""
    seen = {entry.id for entry in tail if entry.id}
    found: list[ChatMessage] = []
    quote = current.quote
    while quote is not None and len(found) < ANCHOR_LIMIT:
        if quote.id in seen:
            break
        entry = _find(history, chat_id, quote.id, window)
        if entry is None:
            break
        found.append(entry)
        seen.add(entry.id)
        quote = entry.quote
    return found


def _key(entry: ChatMessage) -> tuple:
    return (entry.when, str(entry.id))


def _merge(groups: list[list[ChatMessage]]) -> list[ChatMessage]:
    seen: set[str] = set()
    merged: list[ChatMessage] = []
    for group in groups:
        for entry in group:
            if entry.id:
                if entry.id in seen:
                    continue
                seen.add(entry.id)
            merged.append(entry)
    return sorted(merged, key=_key)


def _trim(
    merged: list[ChatMessage],
    current: ChatMessage,
    anchors: list[ChatMessage],
    floor: set[str],
    max_chars: int,
) -> list[ChatMessage]:
    """Drop the oldest rows beyond the floor until the budget fits.

    `current`, its anchors, and the newest `TAIL_MIN_MESSAGES` turns always stay: a short reply
    to "three messages above" must not lose the context the budget was meant to protect.
    """
    keep = floor | {entry.id for entry in (current, *anchors) if entry.id}
    total = sum(len(entry.text or "") for entry in merged)
    trimmed = list(merged)
    for entry in merged:
        if total <= max_chars:
            break
        if entry.id in keep:
            continue
        trimmed.remove(entry)
        total -= len(entry.text or "")
    return trimmed


def _same(entry: ChatMessage, current: ChatMessage) -> bool:
    if entry.id:
        return entry.id == current.id
    return entry is current


def thread(
    history,
    chat_id: str,
    current: ChatMessage,
    *,
    max_chars: int,
    window=None,
) -> list[ChatMessage]:
    """The payload rows for this turn: tail, quote anchors, deduped, budget-trimmed."""
    rows = [entry for entry in _rows(history, chat_id, window) if not _newer(entry, current)]
    tail = _tail(rows, current)
    anchors = _anchors(history, chat_id, current, window, tail)
    floor = {entry.id for entry in tail[-TAIL_MIN_MESSAGES:] if entry.id}
    return _trim(_merge([tail, anchors]), current, anchors, floor, max_chars)


def _newer(entry: ChatMessage, current: ChatMessage) -> bool:
    """Never hand the model a message that arrived after this turn."""
    if _same(entry, current):
        return False
    return entry.when > current.when
