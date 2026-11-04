"""Canonical write preparation, validation, supersede selection, and the index.

Every function takes the facade as its first argument; `MemoryStore` remains the one
write port (ADR-0001, ADR-0037 §4.4).
"""

import inspect
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .. import io
from ..guard import SENSITIVE, is_crisis
from ..schema import memories, memories_readable, memory_index
from . import queries, records
from .integrity import quarantine_kind, supersede_verdict, trust_for, via_of
from .records import (
    SCOPES,
    _flat,
    _WriteParams,
)
from .retention import RetentionPlan

log = logging.getLogger(__name__)

def _quota_counts(store, *, person_id: str, thread_id: str, now: float) -> tuple[int, int, int]:
    if store.db is None:
        return (0, 0, 0)
    with store.db.connect() as conn:
        return queries.quota_counts(
            conn, person_id=person_id, thread_id=thread_id, now=now
        )


def _state_after_quota(
    store,
    *,
    state: str,
    kind: str,
    person_id: str,
    thread_id: str,
    category: str,
    via: str,
    pinned: bool,
    now: float,
) -> str:
    if state != "ok" or store.db is None:
        return state
    if store._quota.exempt(category=category, kind=kind, via=via, pinned=pinned):
        return state
    person_hour, person_day, thread_hour = _quota_counts(store,
        person_id=person_id, thread_id=thread_id, now=now
    )
    if store._quota.overflow(
        person_hour=person_hour, person_day=person_day, thread_hour=thread_hour
    ):
        return "staged"
    return state


async def add(
    store,
    text: str,
    *,
    scope: str = "person",
    kind: str = "fact",
    person_id: str = "",
    thread_id: str = "",
    audience: str = "",
    source: str = "",
    metadata: dict | None = None,
    supersedes: str | None = None,
    infer: bool = False,
    level: int = 2,
    owner_id: str = "",
    subject_id: str = "",
    confirmed: bool = True,
) -> list[str]:
    """Conscious write (tool/command): canonical first, then the derived index."""
    old = (
        await io.run(_superseded_row_sync, store, supersedes)
        if supersedes
        else None
    )
    memory_id = await io.run(
        store.write,
        text,
        scope=scope,
        kind=kind,
        person_id=person_id,
        thread_id=thread_id,
        audience=audience,
        via=via_of(source or "command"),
        source=source,
        level=level,
        owner_id=owner_id,
        subject_id=subject_id,
        confirmed=confirmed,
        supersedes=supersedes,
        asserted_at=None,
    )
    if old is not None:
        await _drop_index(store, old)
    await _index_row(store, memory_id)
    return [memory_id]


def _superseded_row_sync(store, memory_id: str) -> dict | None:
    return records._base_row(store, records._resolve_id(store, memory_id))


def _validated(
    store,
    *,
    text: str,
    level: int | None,
    scope: str,
    kind: str,
    source: str,
    via: str,
    person_id: str,
    owner_id: str,
    subject_id: str,
    corroboration_days: int,
) -> tuple[str, int, str, str, str, str]:
    body = _flat(text)
    if not body:
        raise ValueError("memory text is empty")
    if level is None:
        raise ValueError("a canonical ok/staged row needs a privacy level")
    number = int(level)
    if number > 4:
        raise ValueError("privacy level 5/credentials are never stored")
    if number < 0:
        raise ValueError("privacy level must be 0-4")
    if SENSITIVE.search(body):
        raise ValueError("text contains a secret/credential")
    if is_crisis(body, store._language):
        raise ValueError("crisis text is never stored in memory")
    if scope not in SCOPES:
        raise ValueError(f"unknown scope: {scope}")
    route = via_of(source or via, fallback=via)
    owner = str(owner_id or person_id or "").strip()
    subject = str(subject_id or owner or "").strip()
    wanted = quarantine_kind(
        kind,
        via=route,
        trust=trust_for(route, owner, subject),
        corroboration_days=corroboration_days,
    )
    return body, number, route, owner, subject, wanted


def _prepare(
    store,
    *,
    body: str,
    kind: str,
    scope: str,
    person_id: str,
    thread_id: str,
    audience: str,
    route: str,
    source: str,
    refs: Sequence[str],
    level: int,
    owner: str,
    subject: str,
    confirmed: bool | None,
    pinned: bool,
    durability: str,
    category: str,
    task_status: str | None,
    due_at: float | None,
    asserted_at: float | None,
    dedupe: bool,
    external_id: str,
    supersedes: str | None,
    change: str,
    state: str,
) -> tuple[_WriteParams, str, RetentionPlan, float]:
    if state not in {"ok", "staged"}:
        raise ValueError(
            "write creates only ok or staged rows; use intake/transition otherwise"
        )
    now = time.time()
    is_pinned = bool(pinned) or str(category) in store._policy.pin_categories
    if state == "ok":
        state = _state_after_quota(store,
            state=state,
            kind=kind,
            person_id=person_id,
            thread_id=thread_id,
            category=category,
            via=route,
            pinned=is_pinned,
            now=now,
        )
    plan = store._policy.plan(
        kind=kind,
        level=level,
        state=state,
        durability=durability,
        category=category,
        task_status=task_status,
        pinned=is_pinned,
        now=now,
    )
    if confirmed is None:
        confirmed = True if state == "ok" else None
    params = _WriteParams(
        body=body,
        scope=scope,
        kind=kind,
        person_id=str(person_id or ""),
        thread_id=str(thread_id or ""),
        audience=store._audience(scope, audience),
        route=route,
        source=str(source or route),
        refs=[_flat(item, 200) for item in refs if str(item or "").strip()],
        level=level,
        owner=owner,
        subject=subject,
        confirmed=confirmed,
        pinned=plan.pinned,
        durability=str(durability or "normal"),
        category=str(category or "general"),
        task_status=task_status,
        due_at=due_at,
        asserted_at=float(asserted_at if asserted_at is not None else now),
        dedupe=dedupe,
        external_id=str(external_id or ""),
        supersedes=supersedes,
        change=change,
    )
    return params, state, plan, now


def _values(
    store, params: _WriteParams, state: str, plan: RetentionPlan, now: float
) -> dict:
    memory_id = str(uuid.uuid4())
    return {
        "id": memory_id,
        "scope": params.scope,
        "person_id": params.person_id,
        "thread_id": params.thread_id,
        "kind": params.kind,
        "text": params.body,
        "state": state,
        "level": params.level,
        "confirmed": params.confirmed,
        "owner_id": params.owner,
        "subject_id": params.subject,
        "via": params.route,
        "refs": params.refs,
        "trust": trust_for(params.route, params.owner, params.subject),
        "audience": params.audience,
        "source": params.source,
        "vetoed": False,
        "vetoed_by": "",
        "vetoed_at": 0.0,
        "pinned": plan.pinned,
        "durability": params.durability,
        "category": params.category,
        "use_count": 0,
        "distinct_use_days": 0,
        "last_used_at": 0.0,
        "max_lifetime_at": plan.max_lifetime_at,
        "expires_at": plan.expires_at,
        "supersedes": None,
        "superseded_by": None,
        "due_at": params.due_at,
        "task_status": params.task_status,
        "dispute_state": "none",
        "disputed_with": None,
        "dispute_reason": "",
        "disputed_at": 0.0,
        "asserted_at": params.asserted_at,
        "created_at": now,
        "updated_at": now,
    }


def _existing(store, conn, params: _WriteParams) -> str:
    found = conn.execute(
        select(memories.c.id)
        .where(
            memories.c.text == params.body,
            memories.c.scope == params.scope,
            memories.c.person_id == params.person_id,
            memories.c.thread_id == params.thread_id,
            memories.c.state == "ok",
            memories.c.vetoed.is_(False),
        )
        .limit(1)
    ).scalar()
    return str(found) if found is not None else ""


def _superseded(store, conn, params: _WriteParams) -> dict | None:
    if not params.supersedes:
        return None
    resolved = records._resolve_id(store, params.supersedes)
    if not resolved:
        raise ValueError("memory to supersede was not found")
    old = (
        conn.execute(
            select(memories)
            .where(memories.c.id == resolved)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if old is None:
        raise ValueError("memory to supersede was not found")
    verdict = supersede_verdict(
        old,
        owner_id=params.owner,
        subject_id=params.subject,
        scope=params.scope,
        kind=params.kind,
        thread_id=params.thread_id,
        change=params.change,
        task_status=params.task_status,
    )
    if not verdict.allowed:
        raise ValueError(verdict.reason or "supersede refused")
    return dict(old)


def write(
    store,
    text: str,
    *,
    scope: str = "person",
    kind: str = "fact",
    person_id: str = "",
    thread_id: str = "",
    audience: str = "",
    via: str = "turn",
    source: str = "",
    refs: Sequence[str] = (),
    level: int | None = 2,
    owner_id: str = "",
    subject_id: str = "",
    confirmed: bool | None = None,
    pinned: bool = False,
    durability: str = "normal",
    category: str = "general",
    task_status: str | None = None,
    due_at: float | None = None,
    supersedes: str | None = None,
    change: str = "contradiction",
    asserted_at: float | None = None,
    state: str = "ok",
    corroboration_days: int = 0,
    external_id: str = "",
    dedupe: bool = False,
) -> str:
    """Insert one labelled canonical row; raises before touching anything forbidden."""
    if store.db is None:
        raise ValueError("memory store has no database")
    body, number, route, owner, subject, wanted = _validated(store,
        text=text,
        level=level,
        scope=scope,
        kind=kind,
        source=source,
        via=via,
        person_id=person_id,
        owner_id=owner_id,
        subject_id=subject_id,
        corroboration_days=corroboration_days,
    )
    params, state, plan, now = _prepare(store,
        body=body,
        kind=wanted,
        scope=scope,
        person_id=person_id,
        thread_id=thread_id,
        audience=audience,
        route=route,
        source=source,
        refs=refs,
        level=number,
        owner=owner,
        subject=subject,
        confirmed=confirmed,
        pinned=pinned,
        durability=durability,
        category=category,
        task_status=task_status,
        due_at=due_at,
        asserted_at=asserted_at,
        dedupe=dedupe,
        external_id=external_id,
        supersedes=supersedes,
        change=change,
        state=state,
    )
    values = _values(store, params, state, plan, now)
    memory_id = str(values["id"])
    with store.db.connect() as conn:
        store._fence(conn, "write")
        if params.dedupe:
            found = _existing(store, conn, params)
            if found:
                if params.external_id:
                    _link(store, conn, found, params.external_id)
                return found
        old = _superseded(store, conn, params)
        if old is not None:
            values["supersedes"] = str(old["id"])
        queries.insert_memory(conn, values)
        if old is not None:
            queries.supersede_row(
                conn, str(old["id"]), memory_id, store._policy.superseded_expiry(now), now
            )
        if params.external_id:
            _link(store, conn, memory_id, params.external_id)
    return memory_id


def record_uses(
    store,
    turn_id: str,
    memory_ids: Sequence[str],
    *,
    delivered: bool = True,
) -> int:
    """ADR-0005 §4.3: one touch per memory per delivered turn, then renew deterministically."""
    if (
        store.db is None
        or not delivered
        or not turn_id
        or not store._policy.renew_on_use
        or not store._policy.enabled
    ):
        return 0
    wanted = list(dict.fromkeys(str(item) for item in memory_ids if str(item or "").strip()))
    if not wanted:
        return 0
    now = time.time()
    touched = 0
    with store.db.connect() as conn:
        store._fence(conn, "record_uses")
        for memory_id in wanted:
            if not queries.record_use(conn, memory_id, str(turn_id)):
                continue
            row = queries.locked(conn, memory_id)
            if row is None or str(row.get("state") or "") != "ok":
                continue
            values: dict = {
                "use_count": memories.c.use_count + 1,
                "distinct_use_days": queries.distinct_use_days(conn, memory_id),
                "last_used_at": now,
                "updated_at": now,
            }
            renewed = store._policy.renewed_expiry(
                kind=str(row.get("kind") or "fact"),
                level=int(row.get("level") or 0),
                pinned=bool(row.get("pinned")),
                confirmed=row.get("confirmed"),
                max_lifetime_at=row.get("max_lifetime_at"),
                last_used_at=float(row.get("last_used_at") or 0.0),
                now=now,
            )
            if renewed is not None:
                values["expires_at"] = renewed
            queries.update_memory(conn, memory_id, values)
            touched += 1
    return touched


def _mapping_external_sync(store, memory_id: str) -> str:
    if store.db is None:
        return ""
    with store.db.connect() as conn:
        return queries.mapping_external(conn, memory_id)


def _unlink_sync(store, memory_id: str) -> None:
    if store.db is None:
        return
    with store.db.connect() as conn:
        queries.unlink_index(conn, memory_id)


def _link_sync(store, memory_id: str, external_id: str) -> None:
    if store.db is None:
        return
    with store.db.connect() as conn:
        _link(store, conn, memory_id, external_id)


async def _drop_index(store, row: Mapping) -> None:
    memory_id = str(row.get("id") or "")
    text = str(row.get("text") or "")
    external = await io.run(_mapping_external_sync, store, memory_id)
    delete_index: Callable[[str], Awaitable[object]] | None = getattr(
        store._index, "delete", None
    )
    purge_library: Callable[[str, str], object] | None = getattr(
        store._index, "purge_library", None
    )
    if store._has_index:
        if external and delete_index is not None:
            try:
                await delete_index(external)
            except Exception as exc:
                log.warning("index delete failed %s: %s", memory_id, exc)
        if purge_library is not None:
            try:
                purged = purge_library(external, text)
                if inspect.isawaitable(purged):
                    await purged
            except Exception as exc:
                log.warning("library purge failed %s: %s", memory_id, exc)
    await io.run(_unlink_sync, store, memory_id)


async def _index_row(store, memory_id: str, *, external_id: str = "") -> str:
    if not store._has_index or store.db is None:
        return ""
    if not external_id:
        found = await io.run(_mapping_external_sync, store, memory_id)
        if found:
            return found
    row = await io.run(records._base_row, store, memory_id)
    if row is None or str(row.get("state") or "") != "ok":
        return ""
    if not external_id:
        add: Callable[..., Awaitable[list[str]]] | None = getattr(store._index, "add", None)
        if add is None:  # pragma: no cover - the attached index always implements `add`
            return ""
        try:
            ids = await add(
                row["text"],
                scope=str(row["scope"]),
                kind=str(row["kind"]),
                person_id=str(row["person_id"]),
                thread_id=str(row["thread_id"]),
                audience=str(row["audience"]),
                source=str(row["source"]),
                metadata={"canonical_id": memory_id, "trust": int(row["trust"])},
                infer=False,
                level=int(row["level"]),
                owner_id=str(row["owner_id"]),
                subject_id=str(row["subject_id"]),
                confirmed=bool(row["confirmed"]),
            )
        except Exception as exc:
            log.warning("memory index add failed %s: %s", memory_id, exc)
            return ""
        external_id = str(ids[0]) if ids else ""
    if external_id:
        await io.run(_link_sync, store, memory_id, external_id)
    return external_id


def _link(store, conn, memory_id: str, external_id: str) -> None:
    backend = str(getattr(store._index, "backend", "mem0"))
    model = str(getattr(store._index, "embedding_model", "") or "")
    dim = int(getattr(store._index, "dim", 384) or 384)
    values = {
        "memory_id": memory_id,
        "backend": backend,
        "external_id": str(external_id),
        "model": model,
        "dim": dim,
        "updated_at": time.time(),
    }
    conn.execute(
        pg_insert(memory_index)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["memory_id"],
            set_={key: value for key, value in values.items() if key != "memory_id"},
        )
    )


async def backfill_index(store, *, limit: int = 200) -> int:
    """ADR-0003 slow lane: re-attach readable rows that lost their derived index entry."""
    if store.db is None or not store._has_index:
        return 0
    rows = store.db.fetch_all(
        select(memories_readable.c.id)
        .where(~memories_readable.c.id.in_(select(memory_index.c.memory_id)))
        .limit(max(1, int(limit)))
    )
    added = 0
    for row in rows:
        await _index_row(store, str(row["id"]))
        added += 1
    return added
