"""Turn checkpoints and the step log (ADR-0013 §6-§7, ADR-0011 §4.5).

One `turns` row per incoming message; `id` is the message id and therefore the idempotency
key. The state machine only moves forward through `checkpoint`, so a resumed turn reads
`turns.step` and never repeats an output that is already stored.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Sequence

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db import Database
from ..schema import TURN_TERMINAL, outbound_messages, turn_steps, turns

# ADR-0013 §8.2: these tools are not replayed after they already ran.
SIDE_EFFECTING = frozenset({"send", "send_to", "shell", "sandbox", "settings"})
MAX_RESULT_CHARS = 4000


def args_hash(tool: str, args: object) -> str:
    """Stable hash for the replay check; the tool name is part of the identity."""
    payload = json.dumps(
        {"tool": str(tool), "args": args}, sort_keys=True, ensure_ascii=False, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _clip(value: object) -> str:
    text_value = str(value if value is not None else "")
    if len(text_value) <= MAX_RESULT_CHARS:
        return text_value
    return text_value[: MAX_RESULT_CHARS - 1] + "…"


class TurnStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------- read

    def get(self, turn_id: str) -> dict | None:
        found = self.db.fetch_one(select(turns).where(turns.c.id == str(turn_id)))
        return found

    def steps(self, turn_id: str) -> list[dict]:
        statement = (
            select(turn_steps)
            .where(turn_steps.c.turn_id == str(turn_id))
            .order_by(turn_steps.c.step_index)
        )
        return self.db.fetch_all(statement)

    def find_step(self, turn_id: str, tool: str, digest: str) -> dict | None:
        statement = (
            select(turn_steps)
            .where(
                turn_steps.c.turn_id == str(turn_id),
                turn_steps.c.tool == str(tool),
                turn_steps.c.args_hash == str(digest),
            )
            .order_by(turn_steps.c.step_index.desc())
            .limit(1)
        )
        return self.db.fetch_one(statement)

    def side_effect_done(self, turn_id: str) -> bool:
        statement = (
            select(turn_steps.c.turn_id)
            .where(
                turn_steps.c.turn_id == str(turn_id),
                turn_steps.c.tool.in_(tuple(SIDE_EFFECTING)),
                turn_steps.c.status == "executed",
            )
            .limit(1)
        )
        return self.db.fetch_one(statement) is not None

    # ------------------------------------------------------------ write

    def claim(
        self,
        turn_id: str,
        *,
        owner: str,
        generation: int,
        lease_seconds: float,
        boot_epoch: int = 0,
    ) -> dict | None:
        """Claim a live turn once; `None` means it is terminal or another worker owns it."""
        now = time.time()
        with self.db.connect() as conn:
            found = conn.execute(
                update(turns)
                .where(
                    turns.c.id == str(turn_id),
                    turns.c.status.in_(("received", "running", "sending")),
                )
                .values(
                    status="running",
                    attempt=turns.c.attempt + 1,
                    lease_owner=str(owner)[:80],
                    lease_until=now + max(1.0, float(lease_seconds)),
                    generation=int(generation),
                    boot_epoch=int(boot_epoch),
                    updated_at=now,
                )
                .returning(*turns.c)
            ).mappings().first()
            if found is None:
                return None
            # A crash between "intent" and "sent" leaves an honest `unknown`, never a
            # second send by default (ADR-0013 §8.3).
            conn.execute(
                update(outbound_messages)
                .where(
                    outbound_messages.c.scope_key == f"turn:{found['id']}",
                    outbound_messages.c.status == "intent",
                )
                .values(status="unknown")
            )
            return dict(found)

    def checkpoint(self, turn_id: str, step: str, **fields) -> dict | None:
        """Persist one checkpoint; the row keeps the newest stored output."""
        values: dict = {
            "step": str(step),
            "updated_at": time.time(),
        }
        if step in {"sending"}:
            values["status"] = "sending"
        elif step == "delivered":
            values["status"] = "delivered"
        else:
            values["status"] = "running"
        for key, value in fields.items():
            if key in turns.c:
                values[key] = value
        with self.db.connect() as conn:
            row = conn.execute(
                update(turns)
                .where(turns.c.id == str(turn_id))
                .values(**values)
                .returning(*turns.c)
            ).mappings().first()
            return dict(row) if row is not None else None

    def plan_sending(self, turn_id: str, planned: int) -> None:
        self.checkpoint(turn_id, "sending", planned_chunks=max(0, int(planned)))

    def chunk_sent(self, turn_id: str) -> None:
        now = time.time()
        with self.db.connect() as conn:
            conn.execute(
                update(turns)
                .where(turns.c.id == str(turn_id))
                .values(
                    delivered_chunks=turns.c.delivered_chunks + 1,
                    updated_at=now,
                )
            )

    def finish(self, turn_id: str, *, planned: int = 0) -> None:
        now = time.time()
        with self.db.connect() as conn:
            conn.execute(
                update(turns)
                .where(turns.c.id == str(turn_id))
                .values(
                    status="delivered",
                    step="delivered",
                    planned_chunks=max(0, int(planned)),
                    lease_until=0.0,
                    last_error="",
                    updated_at=now,
                )
            )

    def interrupt(
        self,
        turn_id: str,
        reason: str,
        *,
        backoff_seconds: float = 0.0,
        force: bool = False,
    ) -> None:
        now = time.time()
        clause = turns.c.id == str(turn_id)
        if force:
            clause = clause & (turns.c.status != "delivered")
        else:
            clause = clause & turns.c.status.not_in(TURN_TERMINAL)
        with self.db.connect() as conn:
            conn.execute(
                update(turns)
                .where(clause)
                .values(
                    status="interrupted",
                    last_error=str(reason)[:2000],
                    lease_until=now + max(0.0, float(backoff_seconds)),
                    updated_at=now,
                )
            )

    def defer(self, turn_id: str, reason: str, *, delay_seconds: float) -> None:
        """A transport failure keeps the turn resumable; the reaper waits out the backoff."""
        now = time.time()
        with self.db.connect() as conn:
            conn.execute(
                update(turns)
                .where(turns.c.id == str(turn_id), turns.c.status.not_in(TURN_TERMINAL))
                .values(
                    status="sending",
                    last_error=str(reason)[:2000],
                    lease_until=now + max(0.0, float(delay_seconds)),
                    updated_at=now,
                )
            )

    def stamp_shutdown(self, turn_id: str, epoch: int) -> None:
        with self.db.connect() as conn:
            conn.execute(
                update(turns)
                .where(turns.c.id == str(turn_id))
                .values(shutdown_epoch=int(epoch), updated_at=time.time())
            )

    def expire(self, turn_id: str, reason: str) -> None:
        now = time.time()
        with self.db.connect() as conn:
            conn.execute(
                update(turns)
                .where(turns.c.id == str(turn_id), turns.c.status.not_in(TURN_TERMINAL))
                .values(
                    status="expired",
                    last_error=str(reason)[:2000],
                    lease_until=0.0,
                    updated_at=now,
                )
            )

    def release(self, turn_ids: Sequence[str]) -> int:
        """Return claimed but unselected turns to `received` without burning an attempt.

        A backlog pass claims more than the minute's budget allows; the rest must not look like
        a failed attempt, so the attempt counter is given back (ADR-0013 §11).
        """
        wanted = [str(item) for item in turn_ids if str(item or "").strip()]
        if not wanted:
            return 0
        now = time.time()
        with self.db.connect() as conn:
            result = conn.execute(
                update(turns)
                .where(
                    turns.c.id.in_(wanted),
                    turns.c.status.in_(("running", "sending")),
                )
                .values(
                    status="received",
                    lease_owner="",
                    lease_until=0.0,
                    attempt=func.greatest(0, turns.c.attempt - 1),
                    updated_at=now,
                )
            )
        return int(result.rowcount or 0)

    def claim_expired(
        self,
        *,
        owner: str,
        generation: int,
        lease_seconds: float,
        limit: int,
        max_attempts: int,
        dead_letter_after: int,
        boot_epoch: int = 0,
    ) -> tuple[list[dict], int]:
        """Claim turns whose lease expired; exhausted ones become `interrupted` (dead)."""
        now = time.time()
        claimed: list[dict] = []
        dead = 0
        with self.db.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, updated_at FROM turns "
                    "WHERE status IN ('received','running','sending') "
                    "AND lease_until < EXTRACT(EPOCH FROM clock_timestamp()) "
                    "ORDER BY created_at "
                    "FOR UPDATE SKIP LOCKED LIMIT :limit"
                ),
                {"limit": max(1, int(limit))},
            ).mappings().all()
            for row in rows:
                found = conn.execute(
                    update(turns)
                    .where(
                        turns.c.id == row["id"],
                        turns.c.status.in_(("received", "running", "sending")),
                    )
                    .values(
                        status="running",
                        attempt=turns.c.attempt + 1,
                        lease_owner=str(owner)[:80],
                        lease_until=now + max(1.0, float(lease_seconds)),
                        generation=int(generation),
                        boot_epoch=int(boot_epoch),
                        updated_at=now,
                    )
                    .returning(*turns.c)
                ).mappings().first()
                if found is None:
                    continue
                item = dict(found)
                item["previous_updated_at"] = float(row.get("updated_at") or 0.0)
                if int(item["attempt"]) > int(max_attempts) or int(item["attempt"]) > int(
                    dead_letter_after
                ):
                    conn.execute(
                        update(turns)
                        .where(turns.c.id == item["id"])
                        .values(
                            status="interrupted",
                            last_error="resume abandoned: attempts exhausted",
                            lease_until=0.0,
                            updated_at=now,
                        )
                    )
                    dead += 1
                    continue
                conn.execute(
                    update(outbound_messages)
                    .where(
                        outbound_messages.c.scope_key == f"turn:{item['id']}",
                        outbound_messages.c.status == "intent",
                    )
                    .values(status="unknown")
                )
                claimed.append(item)
        return claimed, dead

    def record_step(
        self,
        turn_id: str,
        *,
        step: str,
        tool: str,
        args: object,
        result: object,
        status: str = "executed",
        started_at: float | None = None,
        finished_at: float | None = None,
    ) -> int:
        """Append one tool step; the index is allocated under the turn row lock."""
        now = time.time()
        with self.db.connect() as conn:
            conn.execute(
                select(turns.c.id).where(turns.c.id == str(turn_id)).with_for_update()
            )
            last = conn.execute(
                select(func.max(turn_steps.c.step_index)).where(
                    turn_steps.c.turn_id == str(turn_id)
                )
            ).scalar()
            index = int(last) + 1 if last is not None else 0
            conn.execute(
                pg_insert(turn_steps).values(
                    turn_id=str(turn_id),
                    step_index=index,
                    step=str(step),
                    tool=str(tool),
                    args_hash=args_hash(str(tool), args),
                    result=_clip(result),
                    status=str(status),
                    started_at=float(started_at if started_at is not None else now),
                    finished_at=float(finished_at if finished_at is not None else now),
                )
            )
            return index

    def unfinished_threads(self) -> list[dict]:
        statement = (
            select(turns.c.thread_id, turns.c.id, turns.c.status, turns.c.updated_at)
            .where(turns.c.status.in_(("received", "running", "sending")))
            .order_by(turns.c.updated_at)
        )
        return self.db.fetch_all(statement)
