"""Identity rekey on canonical memory: one UPDATE, metadata carried along (ADR-0001).

Derived index entries are dropped so a reindex rebuilds them under the new
identity. Veto, audience, level, confirmed, and the state machine are never touched.
"""


import logging

log = logging.getLogger(__name__)


async def rekey_memory(memory, old_id: str, new_id: str, *, db=None) -> int:
    """Move every canonical row that names `old_id` onto `new_id`."""
    if memory is None or not getattr(memory, "available", False):
        return 0
    if not old_id or not new_id or old_id == new_id:
        return 0
    rekey = getattr(memory, "rekey", None)
    if rekey is None:
        log.warning("rekey: memory backend has no canonical rekey")
        return 0
    return int(await rekey(old_id, new_id))
