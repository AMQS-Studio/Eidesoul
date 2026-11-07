"""Readable, admin, and context reads: the `memories_readable` path only.

Functions take the facade first; no second read path exists (ADR-0001, ADR-0007 §4.4).
"""

import logging

from .. import embed, io
from ..config import PrivacyConfig
from . import MemoryContext, MemoryHit, queries
from . import context as memory_context
from .privacy import PrivacyPolicy
from .records import FULL_UUID, HEX_PREFIX, SCOPES, _hit

log = logging.getLogger(__name__)

def readable(store, limit: int = 200) -> list[MemoryHit]:
    if store.db is None:
        return []
    with store.db.connect() as conn:
        found = queries.readable_rows(conn, limit)
    return [_hit(row) for row in found]


def admin(store, *, state: str | None = None, limit: int = 200) -> list[MemoryHit]:
    """Worker/admin read of the base table (ADR-0004 §5.3); never for prompts."""
    if store.db is None:
        return []
    with store.db.connect() as conn:
        found = queries.admin_rows(conn, state, limit)
    return [_hit(row) for row in found]


def backlog(store) -> dict[str, int]:
    if store.db is None:
        return {}
    with store.db.connect() as conn:
        return queries.backlog_rows(conn)


def _rows(
    store, scope: str, query: str, person_id: str, thread_id: str, limit: int
) -> list[MemoryHit]:
    if store.db is None:
        return []
    with store.db.connect() as conn:
        found = queries.match_rows(conn, scope, query, person_id, thread_id, limit)
    return [_hit(row) for row in found]


def _hybrid(
    store,
    query: str,
    person_id: str,
    thread_id: str,
    limit: int,
    scopes: tuple[str, ...],
    max_level: int,
) -> list[MemoryHit]:
    """One statement over `memories_readable`; the vector comes from the shared embedder."""
    if store.db is None:
        return []
    body = str(query or "").replace("\x00", "").strip()
    vector = None
    if body:
        encoded = embed.encode([body])
        if encoded and len(encoded[0]) == queries.MEMORY_DIM:
            vector = encoded[0]
    with store.db.connect() as conn:
        found = queries.hybrid_rows(
            conn,
            query=body,
            vector=vector,
            person_id=person_id,
            thread_id=thread_id,
            limit=limit,
            scopes=scopes,
            max_level=max_level,
        )
    return [_hit(row) for row in found]


async def search(
    store,
    query: str,
    *,
    person_id: str = "",
    thread_id: str = "",
    limit: int = 12,
    scopes: tuple[str, ...] = SCOPES,
    max_level: int = 4,
) -> list[MemoryHit]:
    """ADR-0007 §4.2: one hybrid retrieval for the three legal audiences."""
    return await io.run(
        _hybrid, store, query, person_id, thread_id, limit, scopes, max_level
    )


async def list_rows(
    store,
    *,
    scope: str,
    person_id: str = "",
    thread_id: str = "",
    limit: int = 20,
) -> list[MemoryHit]:
    return await io.run(_rows, store, scope, "", person_id, thread_id, limit)


def get(store, memory_id: str) -> MemoryHit | None:
    if store.db is None:
        return None
    resolved = _resolve_readable(store, memory_id)
    if not resolved:
        return None
    with store.db.connect() as conn:
        found = queries.get_view(conn, resolved)
    return _hit(found) if found else None


def _resolve_readable(store, memory_id: str) -> str:
    raw = str(memory_id or "").strip().lstrip("#").strip().lower()
    if not raw or store.db is None:
        return ""
    if FULL_UUID.match(raw):
        return raw
    if not HEX_PREFIX.match(raw):
        return ""
    if len(raw) == 32:
        return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
    with store.db.connect() as conn:
        return queries.resolve_readable(conn, raw)


async def context(
    store,
    *,
    person_id: str,
    thread_id: str,
    query: str,
    direct: bool | None = None,
    mood=None,
    max_items: int | None = None,
    max_chars: int | None = None,
) -> MemoryContext:
    """ADR-0006 §4.1: the ceiling is applied here, before anything is rendered.

    `direct` is required: a caller that does not say which audience it is building for
    cannot obtain a block, so a new read path cannot forget the ceiling.
    """
    if direct is None:
        raise ValueError("memory context needs direct (ADR-0006 §4.1)")
    limits = store._limits
    items = max_items or int(getattr(limits, "max_items", 12) or 12)
    chars = max_chars or int(getattr(limits, "max_chars", 3000) or 3000)
    policy = PrivacyPolicy(getattr(limits, "privacy", None) or PrivacyConfig())
    try:
        return await memory_context.build(
            store,
            person_id=person_id,
            thread_id=thread_id,
            query=query,
            direct=bool(direct),
            policy=policy,
            tz=store._tz,
            max_items=items,
            max_chars=chars,
        )
    except Exception as exc:
        log.debug("memory context skipped: %s", exc)
        return MemoryContext()


def count_staged(store, *, scope: str = "") -> int:
    if store.db is None:
        return 0
    with store.db.connect() as conn:
        return queries.count_staged(conn, scope)


def scopes(store, *, limit: int = 200) -> list[str]:
    """Scopes that hold a live row: the reflection schedule input."""
    if store.db is None:
        return []
    with store.db.connect() as conn:
        found = queries.scopes(conn, limit)
    live: list[str] = []
    for row in found:
        scope = str(row["scope"] or "")
        if scope == "person" and row["person_id"]:
            live.append(f"person:{row['person_id']}")
        elif scope == "thread" and row["thread_id"]:
            live.append(f"thread:{row['thread_id']}")
        elif scope == "global":
            live.append("global")
    return sorted(set(live))


def count_rows_since(store, *, scope: str = "", after: float = 0.0) -> int:
    if store.db is None:
        return 0
    with store.db.connect() as conn:
        return queries.count_rows_since(conn, scope, after)


def novelty_pool(
    store,
    *,
    scope: str,
    person_id: str = "",
    thread_id: str = "",
    limit: int = 200,
) -> list[dict]:
    """Readable rows in the candidate's own scopes: global + person + this thread."""
    if store.db is None:
        return []
    with store.db.connect() as conn:
        return queries.novelty_pool(conn, person_id, thread_id, limit)


def threads_of(store, person_id: str) -> set[str]:
    """The threads a person is a member of: the membership source for group retrieval."""
    if store.db is None or not str(person_id or "").strip():
        return set()
    with store.db.connect() as conn:
        return queries.threads_of(conn, person_id)
