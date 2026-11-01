"""Re-key Mem0 entries from one person id to another; used only by the identity merge."""

from __future__ import annotations

import logging

from sqlalchemy import select

from ..schema import chat_people

log = logging.getLogger(__name__)

LIMIT = 1000
# Keys the adapter rebuilds from the arguments; carrying the old ones over would re-key nothing.
RESERVED = frozenset(
    {
        "scope",
        "kind",
        "person_id",
        "chat_id",
        "audience",
        "source",
        "level",
        "owner_id",
        "subject_id",
        "confirmed",
        "vetoed",
        "vetoed_by",
        "vetoed_at",
        "where",
        "sensor_context",
    }
)


def _extra(metadata: dict) -> dict:
    return {str(key): value for key, value in (metadata or {}).items() if str(key) not in RESERVED}


async def _move(hits, memory, old_id: str, new_id: str, *, scope: str) -> int:
    moved = 0
    for hit in hits:
        try:
            await memory.add(
                hit.text,
                scope=scope,
                kind=str(hit.metadata.get("kind") or "fact"),
                person_id=new_id if scope == "person" else "",
                chat_id=hit.chat_id,
                level=hit.level,
                owner_id=new_id if hit.owner_id == old_id else hit.owner_id,
                subject_id=new_id if hit.subject_id == old_id else hit.subject_id,
                confirmed=hit.confirmed,
                metadata=_extra(hit.metadata),
            )
        except ValueError:  # a secret or an empty entry is skipped, never fatal
            log.warning("rekey: memory entry skipped id=%s", hit.id)
            continue
        await memory.forget(hit.id, requester_id=old_id, chat_id=hit.chat_id)
        moved += 1
    return moved


def _chats(db, person_id: str) -> list[str]:
    if db is None:
        return []
    try:
        rows = db.fetch_all(
            select(chat_people.c.chat_id).where(chat_people.c.person_id == person_id)
        )
    except Exception:
        log.warning("rekey: chat list failed person=%s", person_id, exc_info=True)
        return []
    return sorted({str(row["chat_id"]) for row in rows})


async def rekey_memory(memory, old_id: str, new_id: str, *, db=None) -> int:
    """Move the person and chat entries that name `old_id` onto `new_id`."""
    if memory is None or not getattr(memory, "available", False):
        return 0
    if not old_id or not new_id or old_id == new_id:
        return 0
    moved = await _move(
        await memory.list(scope="person", person_id=old_id, limit=LIMIT),
        memory,
        old_id,
        new_id,
        scope="person",
    )
    for chat_id in _chats(db, new_id):
        hits = [
            hit
            for hit in await memory.list(scope="chat", chat_id=chat_id, limit=LIMIT)
            if old_id in {hit.owner_id, hit.subject_id}
        ]
        moved += await _move(hits, memory, old_id, new_id, scope="chat")
    return moved
