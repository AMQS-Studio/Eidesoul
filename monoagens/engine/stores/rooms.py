"""Room evidence: which threads a person provably belongs to (ADR-0007 §4.5).

The archive is the second layer of membership proof, used when a body cannot answer. Keeping
these queries here lets the tools ask one question without reaching into the store's schema.
"""


from sqlalchemy import and_, select

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
