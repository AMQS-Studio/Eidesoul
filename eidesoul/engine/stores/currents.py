"""Self-refreshing Indonesia context: one JSON document in the `state` table.

Stored under the `currents` key, the same pattern as the emotion document. It only holds
summaries of the latest `web_search` results per topic; no numbers are invented here.
"""


import json
import logging
import time

from sqlalchemy import select

from ..db import Database
from ..schema import state

log = logging.getLogger(__name__)

KEY = "currents"
TTL_SECONDS = 7 * 24 * 3600


class CurrentsStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def load(self) -> dict:
        """{"updated_at": float, "topics": {name: {"text", "fetched_at", "source"}}}."""
        try:
            row = self.db.fetch_one(select(state.c.value).where(state.c.key == KEY))
        except Exception:
            log.debug("currents: failed to read state", exc_info=True)
            return {"updated_at": 0.0, "topics": {}}
        raw: object = None
        if row is not None:
            try:
                raw = json.loads(row["value"] or "null")
            except (TypeError, ValueError):
                raw = None
        data = raw if isinstance(raw, dict) else {}
        topics = data.get("topics")
        return {
            "updated_at": float(data.get("updated_at") or 0.0),
            "topics": topics if isinstance(topics, dict) else {},
        }

    def save(self, data: dict) -> None:
        payload = {
            "updated_at": float(data.get("updated_at") or 0.0),
            "topics": data.get("topics") if isinstance(data.get("topics"), dict) else {},
        }
        with self.db.connect() as conn:
            self.db.upsert(
                conn,
                state,
                [{"key": KEY, "value": json.dumps(payload), "updated_at": time.time()}],
                ["key"],
                update=lambda stmt: {
                    "value": stmt.excluded.value,
                    "updated_at": stmt.excluded.updated_at,
                },
            )

    def block(self, now: float, limit: int = 6) -> str:
        from ..jobs.currents import format_block

        return format_block(self.load(), now, limit)
