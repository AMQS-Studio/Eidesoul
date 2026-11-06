"""Payload assembly: the newest tail plus every message this turn quotes, under a budget.

Deterministic, archive-backed, and never LLM-driven: the model sees the messages a reader would
need to answer this one, not an arbitrary count of rows.
"""


import logging
from typing import TYPE_CHECKING

from .context import Message, _message_lines

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger(__name__)

TAIL_MIN_MESSAGES = 6
TAIL_MAX_MESSAGES = 20
TAIL_CHARS = 1200
ANCHOR_LIMIT = 3
SCAN = TAIL_MAX_MESSAGES * 4


def _rows(history, thread_id: str, window) -> list[Message]:
    if history is not None:
        try:
            return list(history.recent(thread_id, SCAN))
        except Exception:  # a broken archive must not break the payload
            log.exception("assembly: failed to read the archive chat=%s", thread_id)
    return list(window or [])


def _find(history, thread_id: str, message_id: str, window) -> Message | None:
    if not message_id:
        return None
    for entry in reversed(list(window or [])):
        if entry.id == message_id:
            return entry
    if history is None:
        return None
    try:
        return history.find(thread_id, message_id)
    except Exception:
        log.exception("assembly: failed to read message chat=%s id=%s", thread_id, message_id)
        return None


def _tail(rows: list[Message], current: Message, max_chars: int = TAIL_CHARS) -> list[Message]:
    """Newest rows under the character budget, never fewer than the floor.

    `max_chars <= 0` means the operator disabled the character budget: the tail then grows to
    `TAIL_MAX_MESSAGES` and only that message count stops it (P2.1).
    """
    picked: list[Message] = []
    chars = 0
    for entry in reversed(rows):
        if _same(entry, current):
            continue
        if len(picked) + 1 >= TAIL_MAX_MESSAGES:
            break
        picked.append(entry)
        chars += len(entry.text or "")
        if max_chars > 0 and chars >= TAIL_CHARS and len(picked) + 1 >= TAIL_MIN_MESSAGES:
            break
    return [*reversed(picked), current]


def _anchors(
    history, thread_id: str, current: Message, window, tail: list[Message]
) -> list[Message]:
    """The quote chain of this message, newest first, capped at `ANCHOR_LIMIT`."""
    seen = {entry.id for entry in tail if entry.id}
    found: list[Message] = []
    quote = current.quote
    while quote is not None and len(found) < ANCHOR_LIMIT:
        if quote.id in seen:
            break
        entry = _find(history, thread_id, quote.id, window)
        if entry is None:
            break
        found.append(entry)
        seen.add(entry.id)
        quote = entry.quote
    return found


def _key(entry: Message) -> tuple:
    return (entry.when, str(entry.id))


def _merge(groups: list[list[Message]]) -> list[Message]:
    seen: set[str] = set()
    merged: list[Message] = []
    for group in groups:
        for entry in group:
            if entry.id:
                if entry.id in seen:
                    continue
                seen.add(entry.id)
            merged.append(entry)
    return sorted(merged, key=_key)


def _rendered(config: Config, thread_id: str, entry: Message, current: Message) -> int:
    """Full rendered size of one row: body, quote, and attachment lines, without writing.

    `thread_chars` protects the model's context window, so a short text with a long quote or an
    inlined attachment costs what the payload actually carries (ADR-0003 §4.2). Historical rows
    are measured with the real `current`, so their text attachments count as a summary line
    rather than the full inlined body.
    """
    lines = _message_lines(config, thread_id, entry, {}, current, persist=False)
    return sum(len(line) for line in lines)


def _trim(
    merged: list[Message],
    current: Message,
    anchors: list[Message],
    floor: set[str],
    max_chars: int,
    config: Config,
    thread_id: str,
) -> list[Message]:
    """Drop the oldest rows beyond the floor until the budget fits.

    `current`, its anchors, and the newest `TAIL_MIN_MESSAGES` turns always stay: a short reply
    to "three messages above" must not lose the context the budget was meant to protect. The
    final row stays even when the store has not assigned the inbound message an id yet.
    `max_chars <= 0` disables the character budget entirely (P2.1).
    """
    if max_chars <= 0:
        return list(merged)
    keep = floor | {entry.id for entry in (current, *anchors) if entry.id}
    sizes = [_rendered(config, thread_id, entry, current) for entry in merged]
    total = sum(sizes)
    last = len(merged) - 1
    kept: list[Message] = []
    for index, entry in enumerate(merged):
        protected = (entry.id and entry.id in keep) or entry is current or index == last
        if total > max_chars and not protected:
            total -= sizes[index]
            continue
        kept.append(entry)
    return kept


def _same(entry: Message, current: Message) -> bool:
    if entry.id:
        return entry.id == current.id
    return entry is current


def thread(
    history,
    thread_id: str,
    current: Message,
    *,
    max_chars: int,
    config: Config,
    window=None,
) -> list[Message]:
    """The payload rows for this turn: tail, quote anchors, deduped, budget-trimmed."""
    rows = [entry for entry in _rows(history, thread_id, window) if not _newer(entry, current)]
    tail = _tail(rows, current, max_chars)
    anchors = _anchors(history, thread_id, current, window, tail)
    floor = {entry.id for entry in tail[-TAIL_MIN_MESSAGES:] if entry.id}
    return _trim(_merge([tail, anchors]), current, anchors, floor, max_chars, config, thread_id)


def _newer(entry: Message, current: Message) -> bool:
    """Never hand the model a message that arrived after this turn."""
    if _same(entry, current):
        return False
    return entry.when > current.when
