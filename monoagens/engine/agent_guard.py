"""Cross-room leak check for outbound payloads (ADR-0004 §4.2)."""

import logging
from typing import TYPE_CHECKING

from . import guard, io
from .i18n import t
from .inbound import Action, Address

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .soul import Soul
    from .stores.history import HistoryStore

LEAK_FIELDS = ("text", "caption", "message", "body")

def _leak_text(payload: object) -> str:
    """The human-readable text of an action payload; a file's path is never quoted text."""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        return "\n".join(
            str(payload.get(name) or "").strip() for name in LEAK_FIELDS
        ).strip()
    return ""

class CrossRoomGuard:
    """A cross-room payload may not quote the origin room verbatim."""

    history: HistoryStore
    soul: Soul

    async def _cross_room_leak(self, action: Action, origin: Address) -> str:
        """ADR-0004 §4.2: a cross-room payload may not quote the origin room verbatim."""
        body = _leak_text(action.payload)
        if not body.strip():
            return ""
        try:
            recent = await io.run(self.history.recent, str(origin.thread), 50)
        except Exception:
            log.warning(
                "cross-room leak check could not read origin=%s", origin.thread, exc_info=True
            )
            return ""
        sources = [
            str(entry.text or "") for entry in recent if str(entry.text or "").strip()
        ]
        if sources and guard.cross_chat_leak(body, sources):
            log.warning(
                "cross-room payload blocked action=%s origin=%s", action.id, origin.thread
            )
            return t("agent.cross_room_blocked", self.soul.language)
        return ""
