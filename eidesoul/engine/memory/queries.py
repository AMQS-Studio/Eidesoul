"""Pure SQL for canonical memory: every statement lives here, every function takes a connection.

`MemoryStore` stays the single write port and the readable port (ADR-0001); this module only holds
the statements it used to carry, so the port keeps its behaviour and loses the SQL bodies
(ADR-0031 §4.2). Nothing here decides policy: the caller keeps the fence, the view choice, and the
`MemoryHit` mapping.
"""

import re
import time

from sqlalchemy import String, and_, case, cast, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..schema import (
    memories,
    memories_readable,
    memory_events,
    memory_index,
    memory_tombstones,
    memory_uses,
    messages,
    thread_people,
)
from .integrity import TRUST_UNLABELED

DAY = 86400.0


def rows(conn, statement) -> list[dict]:
    """`Database.fetch_all` shape, on a connection the caller already opened."""
    return [dict(row) for row in conn.execute(statement).mappings().all()]


def one(conn, statement) -> dict | None:
    row = conn.execute(statement).mappings().first()
    return dict(row) if row is not None else None


# ------------------------------------------------------------------ reads (view only)


def readable_rows(conn, limit: int) -> list[dict]:
    return rows(
        conn,
        select(memories_readable)
        .where(memories_readable.c.dispute_state != "open")
        .order_by(memories_readable.c.created_at.desc())
        .limit(max(1, int(limit))),
    )


def admin_rows(conn, state: str | None, limit: int) -> list[dict]:
    statement = select(memories).order_by(memories.c.created_at.desc()).limit(
        max(1, int(limit))
    )
    if state:
        statement = statement.where(memories.c.state == str(state))
    return rows(conn, statement)


def backlog_rows(conn) -> dict[str, int]:
    found = rows(
        conn,
        select(memories.c.state, func.count().label("count")).group_by(memories.c.state),
    )
    return {str(row["state"]): int(row["count"]) for row in found}


def scope_statement(scope: str, person_id: str, thread_id: str):
    """The readable-view clause for one scope; `None` means "no such scope, read nothing"."""
    if scope == "global":
        return memories_readable.c.scope == "global"
    if scope == "person":
        if not person_id:
            return None
        return and_(
            memories_readable.c.scope == "person",
            memories_readable.c.person_id == person_id,
            or_(
                memories_readable.c.audience != "group",
                memories_readable.c.thread_id == thread_id,
            ),
        )
    if scope == "thread":
        if not thread_id:
            return None
        return and_(
            memories_readable.c.scope == "thread",
            memories_readable.c.thread_id == thread_id,
        )
    return None


def match_rows(
    conn, scope: str, query: str, person_id: str, thread_id: str, limit: int
) -> list[dict]:
    clause = scope_statement(scope, person_id, thread_id)
    if clause is None:
        return []
    statement = select(memories_readable).where(clause)
    statement = statement.where(memories_readable.c.dispute_state != "open")
    for term in re.findall(r"\w+", str(query or ""), re.UNICODE):
        pattern = f"%{term}%"
        statement = statement.where(
            or_(
                memories_readable.c.text.ilike(pattern),
                memories_readable.c.kind.ilike(pattern),
            )
        )
    statement = statement.order_by(
        memories_readable.c.last_used_at.desc(),
        memories_readable.c.created_at.desc(),
    ).limit(max(1, int(limit)))
    return rows(conn, statement)


def get_view(conn, memory_id: str) -> dict | None:
    return one(
        conn,
        select(memories_readable).where(
            memories_readable.c.id == memory_id,
            memories_readable.c.dispute_state != "open",
        ),
    )


def resolve_readable(conn, raw: str) -> str:
    """A full uuid, a 32-hex id, or a hex prefix that resolves to exactly one row."""
    found = rows(
        conn,
        select(cast(memories_readable.c.id, String).label("id"))
        .where(cast(memories_readable.c.id, String).like(f"{raw}%"))
        .limit(2),
    )
    return str(found[0]["id"]) if len(found) == 1 else ""


def base_row(conn, memory_id: str) -> dict | None:
    return one(conn, select(memories).where(memories.c.id == memory_id))


def resolve_id(conn, raw: str) -> str:
    found = rows(
        conn,
        select(cast(memories.c.id, String).label("id"))
        .where(cast(memories.c.id, String).like(f"{raw}%"))
        .limit(2),
    )
    return str(found[0]["id"]) if len(found) == 1 else ""


def pending(conn, thread_id: str, limit: int) -> list[dict]:
    statement = (
        select(memories)
        .where(memories.c.state.in_(("pending", "staged")))
        .order_by(memories.c.created_at)
        .limit(max(1, int(limit)))
    )
    if thread_id:
        statement = statement.where(memories.c.thread_id == thread_id)
    return rows(conn, statement)


def quota_counts(conn, *, person_id: str, thread_id: str, now: float) -> tuple[int, int, int]:
    """Rows written in the last hour and day, per person and per thread (ADR-0005 quotas)."""
    hour = float(now) - 3600.0
    day = float(now) - DAY
    live = memories.c.state.in_(("ok", "staged"))

    def count(*clauses) -> int:
        statement = select(func.count()).select_from(memories).where(live, *clauses)
        return int(conn.execute(statement).scalar() or 0)

    person_hour = count(memories.c.person_id == person_id, memories.c.created_at >= hour)
    person_day = count(memories.c.person_id == person_id, memories.c.created_at >= day)
    thread_hour = (
        count(memories.c.thread_id == thread_id, memories.c.created_at >= hour)
        if thread_id
        else 0
    )
    return person_hour, person_day, thread_hour


# --------------------------------------------------------------- worker and schedule reads


def count_staged(conn, scope: str) -> int:
    statement = (
        select(func.count()).select_from(memories).where(memories.c.state == "staged")
    )
    return int(conn.execute(apply_scope(statement, scope)).scalar() or 0)


def count_rows_since(conn, scope: str, after: float) -> int:
    statement = (
        select(func.count())
        .select_from(memories)
        .where(memories.c.state == "ok", memories.c.created_at > float(after))
    )
    return int(conn.execute(apply_scope(statement, scope)).scalar() or 0)


def apply_scope(statement, scope: str):
    """Scope a base-table statement by `person:<id>` / `thread:<id>` / `global`."""
    kind, _, value = str(scope or "").partition(":")
    if kind == "person" and value:
        return statement.where(memories.c.scope == "person", memories.c.person_id == value)
    if kind == "thread" and value:
        return statement.where(memories.c.scope == "thread", memories.c.thread_id == value)
    if kind == "global":
        return statement.where(memories.c.scope == "global")
    return statement


def pending_candidates(conn, scope: str, limit: int, now: float) -> list[dict]:
    statement = (
        select(memories)
        .where(memories.c.state == "pending", memories.c.label_after <= float(now))
        .order_by(memories.c.created_at)
        .limit(max(1, int(limit)))
    )
    return rows(conn, apply_scope(statement, scope))


def supersede_candidates(conn, scope: str, limit: int) -> list[dict]:
    statement = (
        select(memories)
        .where(memories.c.state == "pending")
        .order_by(memories.c.created_at)
        .limit(max(1, int(limit)))
    )
    return rows(conn, apply_scope(statement, scope))


def reflection_rows(conn, scope: str, limit: int) -> list[dict]:
    statement = (
        select(memories)
        .where(memories.c.state.in_(("ok", "staged")))
        .order_by(memories.c.created_at)
        .limit(max(1, int(limit)))
    )
    return rows(conn, apply_scope(statement, scope))


def novelty_pool(
    conn, person_id: str, thread_id: str, limit: int
) -> list[dict]:
    """Readable rows in the candidate's own scopes: global + person + this thread."""
    clauses = [memories_readable.c.scope == "global"]
    if person_id:
        clauses.append(
            and_(
                memories_readable.c.scope == "person",
                memories_readable.c.person_id == person_id,
                or_(
                    memories_readable.c.audience != "group",
                    memories_readable.c.thread_id == thread_id,
                ),
            )
        )
    if thread_id:
        clauses.append(
            and_(
                memories_readable.c.scope == "thread",
                memories_readable.c.thread_id == thread_id,
            )
        )
    return rows(
        conn,
        select(memories_readable.c.id, memories_readable.c.text)
        .where(or_(*clauses))
        .where(memories_readable.c.dispute_state != "open")
        .order_by(memories_readable.c.created_at.desc())
        .limit(max(1, int(limit))),
    )


def scopes(conn, limit: int) -> list[dict]:
    return rows(
        conn,
        select(memories.c.scope, memories.c.person_id, memories.c.thread_id)
        .where(memories.c.state.in_(("ok", "staged")))
        .distinct()
        .limit(max(1, int(limit))),
    )


def event_candidate(conn, event_id: str) -> dict | None:
    return one(
        conn,
        select(memories)
        .join(memory_events, memory_events.c.memory_id == memories.c.id)
        .where(memory_events.c.id == str(event_id))
        .limit(1),
    )


def open_disputes(conn, limit: int) -> list[dict]:
    return rows(
        conn,
        select(memories)
        .where(memories.c.dispute_state == "open")
        .order_by(memories.c.disputed_at, memories.c.id)
        .limit(max(1, int(limit))),
    )


def disputes_asked_today(conn, subject_id: str, now: float) -> int:
    """Distinct normalized pairs asked today: one question marks both rows of its pair."""
    day_start = float(now) - (float(now) % DAY)
    left = cast(memories.c.id, String)
    right = cast(memories.c.disputed_with, String)
    pair = func.concat(func.least(left, right), "|", func.greatest(left, right))
    found = conn.execute(
        select(func.count(func.distinct(pair)))
        .select_from(memories)
        .where(
            memories.c.subject_id == str(subject_id),
            memories.c.dispute_asked_at.is_not(None),
            memories.c.dispute_asked_at >= day_start,
        )
    ).scalar()
    return int(found or 0)


def threads_of(conn, person_id: str) -> set[str]:
    found = rows(
        conn,
        select(thread_people.c.thread_id).where(thread_people.c.person_id == str(person_id)),
    )
    return {str(row["thread_id"]) for row in found}


# ------------------------------------------------------------------- retention and rekey


def purge_candidates(conn, cutoff: float, limit: int) -> list[dict]:
    return rows(
        conn,
        select(memories)
        .where(
            memories.c.expires_at.is_not(None),
            memories.c.expires_at <= float(cutoff),
            memories.c.pinned.is_(False),
        )
        .order_by(memories.c.expires_at)
        .limit(max(1, int(limit))),
    )


def purge_metrics(conn, now: float, days: float) -> int:
    """Cost rows expire by `engine.metrics.retention_days`; a missing table is not an error."""
    if conn.execute(text("SELECT to_regclass('llm_calls')")).scalar() is None:
        return 0
    return int(
        conn.execute(
            text("DELETE FROM llm_calls WHERE ts < :cutoff"),
            {"cutoff": float(now) - float(days) * DAY},
        ).rowcount
        or 0
    )


def rekey_rows(conn, old_id: str) -> list[dict]:
    return rows(
        conn,
        select(memories).where(
            or_(
                memories.c.person_id == old_id,
                memories.c.owner_id == old_id,
                memories.c.subject_id == old_id,
            )
        ),
    )


def rekey_update(conn, old_id: str, new_id: str) -> int:
    result = conn.execute(
        memories.update()
        .where(
            or_(
                memories.c.person_id == old_id,
                memories.c.owner_id == old_id,
                memories.c.subject_id == old_id,
            )
        )
        .values(
            person_id=case(
                (memories.c.person_id == old_id, new_id),
                else_=memories.c.person_id,
            ),
            owner_id=case(
                (memories.c.owner_id == old_id, new_id),
                else_=memories.c.owner_id,
            ),
            subject_id=case(
                (memories.c.subject_id == old_id, new_id),
                else_=memories.c.subject_id,
            ),
            updated_at=time.time(),
        )
    )
    return int(result.rowcount or 0)


# ------------------------------------------------------------------------ index mapping


def mapping_external(conn, memory_id: str) -> str:
    found = one(
        conn,
        select(memory_index.c.external_id).where(memory_index.c.memory_id == memory_id),
    )
    return str(found["external_id"]) if found else ""


def unlink_index(conn, memory_id: str) -> None:
    conn.execute(memory_index.delete().where(memory_index.c.memory_id == memory_id))


# ---------------------------------------------------------------- write-path statements


def insert_memory(conn, values: dict) -> None:
    conn.execute(pg_insert(memories).values(**values))


def insert_event_row(conn, values: dict) -> None:
    conn.execute(pg_insert(memory_events).values(**values))


def insert_candidate_row(conn, values: dict) -> None:
    conn.execute(pg_insert(memories).values(**values))


def supersede_row(conn, memory_id: str, by: str, expires_at, now: float) -> None:
    conn.execute(
        memories.update()
        .where(memories.c.id == memory_id)
        .values(
            state="superseded",
            superseded_by=by,
            pinned=False,
            expires_at=expires_at,
            updated_at=now,
        )
    )


def locked(conn, memory_id: str) -> dict | None:
    """The row an update is about to touch, locked for the transaction."""
    return one(conn, select(memories).where(memories.c.id == memory_id).with_for_update())


def unlocked(conn, memory_id: str) -> dict | None:
    return one(conn, select(memories).where(memories.c.id == memory_id))


def update_memory(conn, memory_id: str, values: dict, *, bump_label_attempts: bool = False) -> None:
    statement = memories.update().where(memories.c.id == memory_id)
    if bump_label_attempts:
        conn.execute(statement.values(**values, label_attempts=memories.c.label_attempts + 1))
        return
    conn.execute(statement.values(**values))


def raise_visibility(conn, refs, level: int) -> None:
    """The stored level raises the turn's visibility so later reads respect it."""
    conn.execute(
        update(messages)
        .where(messages.c.msg_id.in_([str(item) for item in refs]))
        .where(messages.c.visibility < int(level))
        .values(visibility=int(level))
    )


def forget_candidates(conn, owner_id: str, thread_id: str) -> list[dict]:
    return rows(
        conn,
        select(memories_readable).where(
            memories_readable.c.owner_id == owner_id,
            or_(
                memories_readable.c.scope == "global",
                memories_readable.c.thread_id == thread_id,
            ),
        ),
    )


def open_dispute_rows(conn, cutoff: float) -> list[dict]:
    return rows(
        conn,
        select(memories)
        .where(memories.c.dispute_state == "open", memories.c.disputed_at <= cutoff)
        .with_for_update(),
    )


def pair_locked(conn, left: str, right: str) -> list[dict]:
    return rows(
        conn,
        select(memories).where(memories.c.id.in_((left, right))).with_for_update(),
    )


def soul_rule_rows(conn) -> list[dict]:
    return rows(
        conn,
        select(memories).where(
            memories.c.state == "ok",
            memories.c.via == "soul_doc",
            memories.c.kind != "rule",
        ),
    )


def soul_rule_seen(conn, body: str, day_start: float) -> bool:
    return conn.execute(
        select(memories.c.id)
        .where(
            memories.c.state == "ok",
            memories.c.via == "soul_doc",
            memories.c.text == body,
            memories.c.asserted_at >= day_start,
        )
        .limit(1)
    ).first() is not None


def set_kind_and_expiry(conn, ids, *, kind: str, pinned: bool, expires_at, moment: float) -> None:
    conn.execute(
        memories.update()
        .where(memories.c.id.in_(list(ids)))
        .values(kind=kind, pinned=pinned, expires_at=expires_at, updated_at=moment)
    )


def delete_row(conn, memory_id: str, cutoff: float) -> bool:
    removed = conn.execute(
        delete(memories)
        .where(
            memories.c.id == memory_id,
            memories.c.pinned.is_(False),
            memories.c.expires_at <= cutoff,
        )
        .returning(memories.c.id)
    ).scalar()
    return removed is not None


def insert_tombstone(conn, values: dict) -> None:
    conn.execute(pg_insert(memory_tombstones).values(**values))


def record_use(conn, memory_id: str, turn_id: str) -> bool:
    """Insert the (memory, day, turn) touch once; a repeated delivery is a no-op."""
    inserted = conn.execute(
        pg_insert(memory_uses)
        .values(memory_id=memory_id, day=func.current_date(), turn_id=str(turn_id))
        .on_conflict_do_nothing(index_elements=["memory_id", "day", "turn_id"])
        .returning(memory_uses.c.memory_id)
    ).scalar()
    return inserted is not None


def distinct_use_days(conn, memory_id: str) -> int:
    return int(
        conn.execute(
            select(func.count(func.distinct(memory_uses.c.day))).where(
                memory_uses.c.memory_id == memory_id
            )
        ).scalar()
        or 1
    )


def candidate_values(
    *,
    memory_id: str,
    body: str,
    person_id: str,
    thread_id: str,
    kind: str,
    audience: str,
    refs: list[str],
    asserted_at: float,
    now: float,
    expires_at,
) -> dict:
    """The pending row of a captured turn: level NULL until the assess labels it."""
    return {
        "id": memory_id,
        "scope": "person",
        "person_id": person_id,
        "thread_id": thread_id,
        "kind": kind,
        "text": body,
        "state": "pending",
        "level": None,
        "confirmed": None,
        "owner_id": person_id,
        "subject_id": person_id,
        "via": "turn",
        "refs": refs,
        "trust": TRUST_UNLABELED,
        "audience": audience,
        "source": "turn",
        "vetoed": False,
        "vetoed_by": "",
        "vetoed_at": 0.0,
        "pinned": False,
        "durability": "normal",
        "category": "general",
        "use_count": 0,
        "distinct_use_days": 0,
        "last_used_at": 0.0,
        "max_lifetime_at": None,
        "expires_at": expires_at,
        "supersedes": None,
        "superseded_by": None,
        "due_at": None,
        "task_status": None,
        "dispute_state": "none",
        "disputed_with": None,
        "dispute_reason": "",
        "disputed_at": 0.0,
        "asserted_at": asserted_at,
        "created_at": now,
        "updated_at": now,
    }


def event_values(
    *,
    event_id: str,
    memory_id: str,
    thread_id: str,
    person_id: str,
    message_id: str,
    body: str,
    digest: str,
    now: float,
) -> dict:
    return {
        "id": event_id,
        "memory_id": memory_id,
        "thread_id": thread_id,
        "person_id": person_id,
        "message_id": message_id,
        "kind": "message",
        "content": body,
        "content_hash": digest,
        "created_at": now,
    }
