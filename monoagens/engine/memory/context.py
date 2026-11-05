"""Cross-chat memory query: global + sender person + active chat, then render the prompt block."""


import logging

from .. import clock
from . import MemoryContext

log = logging.getLogger(__name__)

SCOPES = ("global", "person", "thread")
HEADER = "Long-term memory (cross-chat):"


def _dedupe(hits: list) -> list:
    found: dict[str, object] = {}
    texts: set[str] = set()
    for hit in hits:
        key = str(getattr(hit, "id", "") or "")
        text = " ".join(str(getattr(hit, "text", "") or "").lower().split())
        if key and key in found:
            continue
        if text and text in texts:
            continue
        if key:
            found[key] = hit
        if text:
            texts.add(text)
    return list(found.values())


async def gather(
    backend,
    *,
    query: str,
    person_id: str,
    thread_id: str,
    limit: int,
    scopes: tuple[str, ...] = SCOPES,
) -> list:
    """One hybrid retrieval for the three legal audiences; another person is never requested."""
    try:
        hits = await backend.search(
            query,
            person_id=person_id,
            thread_id=thread_id,
            limit=limit,
            scopes=scopes,
        )
    except Exception as exc:  # a memory read never fails the turn
        log.debug("memory search failed: %s", exc)
        return []
    return _dedupe(list(hits))


def _date(value, tz: str) -> str:
    if not value:
        return ""
    try:
        return clock.day(float(value), tz)
    except (TypeError, ValueError, OSError):
        return ""


def render(hits: list, *, max_items: int, max_chars: int, tz: str) -> str:
    if not hits:
        return ""
    lines = [HEADER]
    used = len(HEADER)
    for hit in hits[: max(1, int(max_items))]:
        scope = str(getattr(hit, "scope", "") or "person")
        stamp = _date(getattr(hit, "created_at", None), tz)
        label = f"{scope}/{stamp}" if stamp else scope
        level = int(getattr(hit, "level", 2) or 0)
        mark = "" if getattr(hit, "confirmed", True) else "?"
        if str(getattr(hit, "metadata", {}).get("dispute_state") or "") == "open":
            mark += "!"
        trust = int(getattr(hit, "trust", 1) if getattr(hit, "trust", 1) is not None else 1)
        kind = str(getattr(hit, "kind", "fact") or "fact")
        short = str(getattr(hit, "id", "") or "").replace("-", "")[:8]
        markers = f"L{level}{mark} T{trust}" + (" rule" if kind == "rule" else "")
        ident = f"#{short} " if short else ""
        text = " ".join(str(getattr(hit, "text", "") or "").split())
        if not text:
            continue
        line = f"- {ident}[{label} {markers}] {text}"
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) if len(lines) > 1 else ""


async def build(
    backend,
    *,
    person_id: str,
    thread_id: str,
    query: str,
    direct: bool,
    policy,
    max_items: int = 12,
    max_chars: int = 3000,
    scopes: tuple[str, ...] = SCOPES,
    tz: str,
) -> MemoryContext:
    """ADR-0006 §4.1: drop above-ceiling and vetoed hits before render and before `hits`."""
    hits = [
        hit
        for hit in await gather(
            backend,
            query=query,
            person_id=person_id,
            thread_id=thread_id,
            limit=max(1, int(max_items)),
            scopes=scopes,
        )
        if not getattr(hit, "vetoed", False)
        and policy.show(hit, direct=bool(direct))
    ]
    text = render(hits, max_items=max_items, max_chars=max_chars, tz=tz)
    return MemoryContext(text=text, hits=tuple(hit for hit in hits if getattr(hit, "text", "")))
