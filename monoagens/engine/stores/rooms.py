"""Room evidence: which threads a person provably belongs to (ADR-0007 §4.5).

The archive is the second layer of membership proof, used when a body cannot answer. Keeping
these queries here lets the tools ask one question without reaching into the store's schema,
and the route row for observed messages.
"""


from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..schema import messages, threads


def private_rooms(db, person_id: str) -> set[str]:
    """DM threads of this person: a private room the person themselves spoke in.

    A direct thread has exactly two accounts, so the person's own message proves the room is
    theirs; groups never qualify, so continuity across a linked identity (ADR-0006 §4.3) can
    never open somebody else's room.
    """
    who = str(person_id or "").strip()
    if not who:
        return set()
    rows = db.fetch_all(
        select(messages.c.thread_id)
        .join(threads, threads.c.thread_id == messages.c.thread_id)
        .where(
            and_(
                threads.c.kind == "dm",
                messages.c.sender_id == who,
                messages.c.thread_id != "",
            )
        )
        .distinct()
    )
    return {str(row["thread_id"]) for row in rows}


def member_rooms(db, person_ids, *, limit: int = 10) -> list[dict]:
    """Rooms where this person is recorded, newest first: thread id, name, and gateway.

    `person_ids` carries every address the sender is known by (the canonical `per_…` id, the
    body's raw sender id, and its address), because `messages.sender_id` keeps what the body
    wrote while `memories` and `thread_people` keep the canonical id. The archive layer of
    ADR-0007 §4.5: the prompt's verified-facts block names these rooms, so a question about one
    of them is continuity, not a guess. Content never leaves this query.
    """
    who = {
        str(item).strip() for item in person_ids or () if str(item or "").strip()
    }
    if not who:
        return []
    last = func.max(messages.c.ts)
    rows = db.fetch_all(
        select(
            messages.c.thread_id,
            threads.c.name,
            threads.c.gateway,
            last.label("last"),
        )
        .join(threads, threads.c.thread_id == messages.c.thread_id)
        .where(and_(messages.c.sender_id.in_(who), messages.c.thread_id != ""))
        .group_by(messages.c.thread_id, threads.c.name, threads.c.gateway)
        .order_by(last.desc())
        .limit(max(1, int(limit)))
    )
    return [dict(row) for row in rows]


def ensure_thread(db, thread_id: str, *, gateway: str, kind: str) -> None:
    """Create the route row with the room's real kind when it is absent.

    An observed message never re-labels a thread another body routed: the row is only
    inserted, so `threads.kind` keeps the truth a private-room read depends on.
    """
    thread = str(thread_id or "")
    if not thread:
        return
    with db.connect() as conn:
        conn.execute(
            pg_insert(threads)
            .values(
                thread_id=thread,
                name="",
                reset_ts=0.0,
                kind="group" if str(kind) == "group" else "dm",
                gateway=str(gateway or ""),
            )
            .on_conflict_do_nothing(index_elements=["thread_id"])
        )
