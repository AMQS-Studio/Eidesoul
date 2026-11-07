"""Room evidence: which threads a person provably belongs to (ADR-0007 §4.5).

The archive is the second layer of membership proof, used when a body cannot answer. Keeping
these queries here lets the tools ask one question without reaching into the store's schema,
and the route row for observed messages.
"""


from sqlalchemy import and_, select
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
