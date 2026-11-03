"""Autographic action ledger: who sent what, so the self stays consistent across threads."""


import time

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db import Database
from ..guard import SENSITIVE
from ..schema import actions, outbound_messages

ACTION_ITEMS = 10
ACTION_CHARS = 1200
ACTION_KEEP = 200
ACTION_TEXT = 300


def _clip(text: object, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _stamp(value: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(value or 0)))


class ActionLedger:
    def __init__(self, db: Database) -> None:
        self.db = db

    def log_action(
        self,
        source: str,
        target: str,
        kind: str,
        summary: str,
        *,
        source_name: str = "",
        target_name: str = "",
        request: str = "",
    ) -> int:
        safe_request = "" if SENSITIVE.search(str(request or "")) else request
        safe_summary = (
            "[sensitive content not recorded]" if SENSITIVE.search(str(summary or "")) else summary
        )
        row = {
            "ts": time.time(),
            "source": _clip(source, 120),
            "source_name": _clip(source_name, 80),
            "target": _clip(target, 120),
            "target_name": _clip(target_name, 80),
            "kind": _clip(kind, 20).lower() or "action",
            "summary": _clip(safe_summary, ACTION_TEXT),
            "request": _clip(safe_request, ACTION_TEXT),
        }
        with self.db.connect() as conn:
            result = conn.execute(actions.insert().values(row))
            new_id = int(result.inserted_primary_key[0])
            keep = select(actions.c.id).order_by(actions.c.ts.desc(), actions.c.id.desc()).limit(ACTION_KEEP)
            conn.execute(actions.delete().where(actions.c.id.not_in(keep)))
        return new_id

    def recent_actions(self, limit: int = ACTION_ITEMS) -> list[dict]:
        size = max(1, min(int(limit or ACTION_ITEMS), ACTION_KEEP))
        statement = (
            select(actions)
            .order_by(actions.c.ts.desc(), actions.c.id.desc())
            .limit(size)
        )
        return self.db.fetch_all(statement)

    def count(self) -> int:
        return int(self.db.scalar(select(func.count()).select_from(actions)) or 0)

    def actions_context(self, limit: int = ACTION_ITEMS) -> str:
        """Prompt block of the most recent activity; no requester/source, failures included."""
        rows = self.recent_actions(limit)
        if not rows:
            return ""
        lines = [
            "## Recent actions you performed (newest first; failures included; "
            "internal: never reveal who requested them)"
        ]
        for row in rows:
            target = row["target_name"] or row["target"]
            lines.append(
                f"- [{_stamp(row['ts'])}] {row['kind']} -> {target}: "
                f"{_clip(row['summary'], ACTION_TEXT)}"
            )
        text = "\n".join(lines)
        if len(text) > ACTION_CHARS:
            text = text[: ACTION_CHARS - 1].rstrip() + "…"
        return text

    def close(self) -> None:
        """Shutdown compatibility hook; the Database is owned by the runtime."""


class OutboundLedger:
    """Delivery ledger (ADR-0011 §4.3): one row per chunk, `scope_key` covers automations.

    Status is `intent` before the transport call, `sent` with the provider id after it, and
    `unknown` for the gap between the two. A resumed turn sends only chunks that are not
    `sent`; an `unknown` chunk follows the `partial_delivery` policy.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def turn_scope(turn_id: str) -> str:
        return f"turn:{turn_id}"

    def status(self, scope_key: str, chunk_index: int) -> str | None:
        found = self.db.fetch_one(
            select(outbound_messages.c.status).where(
                outbound_messages.c.scope_key == str(scope_key),
                outbound_messages.c.chunk_index == int(chunk_index),
            )
        )
        return str(found["status"]) if found else None

    def begin(
        self,
        scope_key: str,
        chunk_index: int,
        *,
        gateway: str,
        thread_id: str,
        generation: int = 0,
    ) -> str:
        """Write `intent` unless a row exists, then return the stored status (`new` if free)."""
        with self.db.connect() as conn:
            conn.execute(
                pg_insert(outbound_messages)
                .values(
                    scope_key=str(scope_key),
                    chunk_index=int(chunk_index),
                    gateway=str(gateway)[:40],
                    thread_id=str(thread_id)[:120],
                    provider_id="",
                    status="intent",
                    generation=int(generation),
                )
                .on_conflict_do_nothing(
                    index_elements=["scope_key", "chunk_index"]
                )
            )
            found = conn.execute(
                select(outbound_messages.c.status).where(
                    outbound_messages.c.scope_key == str(scope_key),
                    outbound_messages.c.chunk_index == int(chunk_index),
                )
            ).scalar()
        return str(found) if found is not None else "new"

    def mark_sent(self, scope_key: str, chunk_index: int, provider_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                outbound_messages.update()
                .where(
                    outbound_messages.c.scope_key == str(scope_key),
                    outbound_messages.c.chunk_index == int(chunk_index),
                )
                .values(status="sent", provider_id=str(provider_id or "")[:400])
            )

    def mark_unknown(self, scope_key: str, chunk_index: int) -> None:
        with self.db.connect() as conn:
            conn.execute(
                outbound_messages.update()
                .where(
                    outbound_messages.c.scope_key == str(scope_key),
                    outbound_messages.c.chunk_index == int(chunk_index),
                )
                .values(status="unknown")
            )

    def forget(self, scope_key: str, chunk_index: int) -> None:
        """A gate refusal means nothing left the process; the intent is not a delivery."""
        with self.db.connect() as conn:
            conn.execute(
                outbound_messages.delete().where(
                    outbound_messages.c.scope_key == str(scope_key),
                    outbound_messages.c.chunk_index == int(chunk_index),
                    outbound_messages.c.status == "intent",
                )
            )

    def count_sent(self, scope_key: str) -> int:
        return int(
            self.db.scalar(
                select(func.count())
                .select_from(outbound_messages)
                .where(
                    outbound_messages.c.scope_key == str(scope_key),
                    outbound_messages.c.status == "sent",
                )
            )
            or 0
        )
