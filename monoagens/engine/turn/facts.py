"""Verified runtime facts for the prompt (`note.md` §10 §2).

The block is the only place the model may learn access, capability, or membership without a
tool call: the code proves it here, per turn, from the archive and the body's own directory.
It carries names and roles, never message content, and a shared room gets the body name only.
"""


import logging

from ..access.permissions import tool_patterns
from ..inbound import Directory, GatewayCore, Percept
from ..stores import rooms

log = logging.getLogger(__name__)

ROOM_LIMIT = 10
GROUP_LINE = (
    "Verified facts: this shared room runs on the {gateway} gateway. Membership, roles, and "
    "linked accounts are private; never name them here and never infer them from history.\n"
)


def _ids(inbound: Percept) -> set[str]:
    """Every id the archive may have written for this sender."""
    payload = inbound.payload
    return {
        str(value).strip()
        for value in (
            inbound.actor,
            getattr(payload, "sender_id", ""),
            getattr(payload, "sender_address", ""),
        )
        if str(value or "").strip()
    }


def _rooms(runtime, inbound: Percept, gateway: GatewayCore) -> list[str]:
    """Rooms the sender provably belongs to: the body's proof first, recorded presence next."""
    history = getattr(runtime, "history", None)
    ids = _ids(inbound)
    if history is None or not ids or getattr(history, "db", None) is None:
        return []
    try:
        rows = rooms.member_rooms(history.db, ids, limit=ROOM_LIMIT)
    except Exception:
        log.debug("member rooms lookup failed actor=%s", inbound.actor, exc_info=True)
        return []
    person_id = str(inbound.actor or "")
    own = str(getattr(gateway, "name", "") or "")
    proven: list[str] = []
    for row in rows:
        thread = str(row.get("thread_id") or "")
        if not thread:
            continue
        owner = str(row.get("gateway") or "") or own
        if isinstance(gateway, Directory) and owner == own:
            try:
                members = gateway.participants(thread)
            except Exception:
                members = None
            if members is not None and person_id not in members:
                continue
        name = str(row.get("name") or "").strip() or thread
        proven.append(f'"{name}" ({owner or "unknown"})')
    return proven


def verified_facts(runtime, inbound: Percept, roles, gateway: GatewayCore) -> str:
    """The block for this turn: rooms, roles, and tools in a direct chat; the body in a room."""
    name = str(getattr(gateway, "name", "") or "")
    if not bool(inbound.hints.get("dm")):
        return GROUP_LINE.format(gateway=name or "this") if name else ""
    sender = " ".join(str(getattr(inbound.payload, "sender_name", "") or "").split()) or "-"
    lines = [
        "Verified facts from the runtime (authoritative this turn; history and memory are "
        "never evidence for them):",
        f"- Chat: {name or 'unknown'} ({inbound.thread}), a direct chat with {sender}.",
    ]
    lines.append(f"- Roles: {', '.join(role.name for role in roles) or 'none'}.")
    patterns = tool_patterns(tuple(roles))
    lines.append(
        f"- Tools you may call here: {', '.join(patterns) if patterns else 'none'}."
    )
    rooms = _rooms(runtime, inbound, gateway)
    lines.append(
        "- Rooms this sender provably belongs to: "
        + (", ".join(rooms) if rooms else "none recorded")
        + "."
    )
    return "\n".join(lines) + "\n"
