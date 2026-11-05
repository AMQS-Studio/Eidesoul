"""Turn checkpoints and the step log (ADR-0013 §6-§7, ADR-0011 §4.5).

One `turns` row per incoming message; `id` is the namespaced turn identity
(`<gateway>:<thread>:<message_id>`) and is therefore the idempotency key. The state machine only
moves forward through `checkpoint`, so a resumed turn reads `turns.step` and never repeats an
output that is already stored. A step row is written `running` before the tool runs and finalized
after, so a crash mid-execution is visible and never replayed.
"""


import hashlib
import json
import time
from collections.abc import Sequence

from sqlalchemy import ColumnElement, TextClause, func, select, text, update

from ..db import Database
from ..jobs.lease import FencedStore, Superseded, fence_clause, fenced_insert, row_clause
from ..schema import TURN_TERMINAL, outbound_messages, turn_steps, turns

# ADR-0013 §8.2: a step whose handler declared state is never replayed.
MAX_RESULT_CHARS = 4000
# A step row that is still `running` when the turn is claimed again lost its worker.
STEP_RUNNING = "running"
STEP_EXECUTED = "executed"
STEP_FAILED = "failed"
STEP_INTERRUPTED = "interrupted"


def turn_identity(gateway: object, thread: object, message_id: object) -> str:
    """`<gateway>:<thread>:<message_id>`: two bodies can never collide on one message id."""
    body = str(gateway or "").strip() or "core"
    return f"{body}:{thread or ''!s}:{message_id or ''!s}"


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


class TurnStore(FencedStore):
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------- fence

    def _fence(self, *, unclaimed: bool = False):
        """The predicate every turn mutation carries (ADR-0012 §4.3).

        A bound store fences with its own (instance, generation); an unbound one compares the
        row's recorded claim generation with the live one, so a deposed instance's checkpoints
        match nothing. A database with no lease row at all is not fenced.
        """
        bound = self.guard_clause()
        return bound if bound is not None else row_clause(
            "turns.generation", unclaimed=unclaimed
        )

    @staticmethod
    def _claim_fence(owner: str, generation: int):
        """A claim is fenced by the caller's own pair, so only the live holder can take it."""
        return fence_clause(owner, generation)

    # ------------------------------------------------------------- read

    def get(self, turn_id: str) -> dict | None:
        found = self.db.fetch_one(select(turns).where(turns.c.id == str(turn_id)))
        return found

    def message_id(self, turn_id: str) -> str:
        """The raw transport id behind a turn; "" when the row is gone.

        A resume re-reads the archive with this id: `turns.id` is namespaced, the archive row is
        keyed by the body's own message id (ADR-0001 §4.3).
        """
        found = self.db.fetch_one(
            select(turns.c.message_id).where(turns.c.id == str(turn_id))
        )
        return str((found or {}).get("message_id") or "")

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
        """A declared side effect ran, or was running when the worker died: never replay."""
        statement = (
            select(turn_steps.c.turn_id)
            .where(
                turn_steps.c.turn_id == str(turn_id),
                turn_steps.c.approval_class != "none",
                turn_steps.c.status.in_((STEP_EXECUTED, STEP_INTERRUPTED)),
            )
            .limit(1)
        )
        return self.db.fetch_one(statement) is not None

    @staticmethod
    def _turn_scope(turn_id: object):
        """`scope_key LIKE 'turn:<id>%'`: covers `:0`, `:1`, and `:tool:1` of one turn."""
        return outbound_messages.c.scope_key.startswith(
            f"turn:{turn_id or ''!s}", autoescape=True
        )

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
        fence = self.fence_clause()
        if fence is None:
            fence = self._claim_fence(owner, generation)
        with self.db.connect() as conn:
            statement = (
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
            )
            if fence is not None:
                statement = statement.where(fence)
            found = conn.execute(statement).mappings().first()
            if found is None:
                return None
            # A crash between "intent" and "sent" leaves an honest `unknown`, never a
            # second send by default (ADR-0013 §8.3). The scope prefix covers every row of the
            # turn: reply chunks (`:0`, `:1`) and the actions a tool emitted (`:tool:1`).
            conn.execute(
                update(outbound_messages)
                .where(
                    self._turn_scope(found["id"]),
                    outbound_messages.c.status == "intent",
                )
                .values(status="unknown")
            )
            self._interrupt_running_steps(conn, str(found["id"]), now)
            return dict(found)

    @staticmethod
    def _interrupt_running_steps(conn, turn_id: str, now: float) -> int:
        """A `running` step lost its worker: mark it `interrupted` so it is never replayed."""
        outcome = conn.execute(
            update(turn_steps)
            .where(
                turn_steps.c.turn_id == str(turn_id),
                turn_steps.c.status == STEP_RUNNING,
            )
            .values(status=STEP_INTERRUPTED, finished_at=now)
        )
        return int(outcome.rowcount or 0)

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
        fence = self._fence()
        with self.db.connect() as conn:
            statement = (
                update(turns)
                .where(turns.c.id == str(turn_id))
                .values(**values)
                .returning(*turns.c)
            )
            if fence is not None:
                statement = statement.where(fence)
            row = conn.execute(statement).mappings().first()
            if row is None and fence is not None:
                raise Superseded(f"lease generation moved on: checkpoint {turn_id} refused")
            return dict(row) if row is not None else None

    def plan_sending(self, turn_id: str, planned: int) -> None:
        self.checkpoint(turn_id, "sending", planned_chunks=max(0, int(planned)))

    def chunk_sent(self, turn_id: str) -> None:
        now = time.time()
        fence = self._fence()
        with self.db.connect() as conn:
            statement = (
                update(turns)
                .where(turns.c.id == str(turn_id))
                .values(
                    delivered_chunks=turns.c.delivered_chunks + 1,
                    updated_at=now,
                )
            )
            if fence is not None:
                statement = statement.where(fence)
            conn.execute(statement)

    def finish(self, turn_id: str, *, planned: int = 0) -> None:
        now = time.time()
        fence = self._fence()
        with self.db.connect() as conn:
            statement = (
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
            if fence is not None:
                statement = statement.where(fence)
            conn.execute(statement)

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
        fence = self._fence(unclaimed=True)
        if fence is not None:
            clause = clause & fence
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
        fence = self._fence()
        clause = [turns.c.id == str(turn_id), turns.c.status.not_in(TURN_TERMINAL)]
        if fence is not None:
            clause.append(fence)
        with self.db.connect() as conn:
            conn.execute(
                update(turns)
                .where(*clause)
                .values(
                    status="sending",
                    last_error=str(reason)[:2000],
                    lease_until=now + max(0.0, float(delay_seconds)),
                    updated_at=now,
                )
            )

    def stamp_shutdown(self, turn_id: str, epoch: int) -> None:
        fence = self._fence()
        statement = (
            update(turns)
            .where(turns.c.id == str(turn_id))
            .values(shutdown_epoch=int(epoch), updated_at=time.time())
        )
        if fence is not None:
            statement = statement.where(fence)
        with self.db.connect() as conn:
            conn.execute(statement)

    def expire(self, turn_id: str, reason: str) -> None:
        now = time.time()
        fence = self._fence(unclaimed=True)
        clause = [turns.c.id == str(turn_id), turns.c.status.not_in(TURN_TERMINAL)]
        if fence is not None:
            clause.append(fence)
        with self.db.connect() as conn:
            conn.execute(
                update(turns)
                .where(*clause)
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
        fence = self._fence()
        clause: list[ColumnElement[bool] | TextClause] = [
            turns.c.id.in_(wanted),
            turns.c.status.in_(("running", "sending")),
        ]
        if fence is not None:
            clause.append(fence)
        with self.db.connect() as conn:
            result = conn.execute(
                update(turns)
                .where(*clause)
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
        fence = self.fence_clause()
        if fence is None:
            fence = self._claim_fence(owner, generation)
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
                statement = (
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
                )
                if fence is not None:
                    statement = statement.where(fence)
                found = conn.execute(statement).mappings().first()
                if found is None:
                    continue
                item = dict(found)
                item["previous_updated_at"] = float(row.get("updated_at") or 0.0)
                if int(item["attempt"]) > int(max_attempts) or int(item["attempt"]) > int(
                    dead_letter_after
                ):
                    closed = (
                        update(turns)
                        .where(turns.c.id == item["id"])
                        .values(
                            status="interrupted",
                            last_error="resume abandoned: attempts exhausted",
                            lease_until=0.0,
                            updated_at=now,
                        )
                    )
                    if fence is not None:
                        closed = closed.where(fence)
                    conn.execute(closed)
                    dead += 1
                    continue
                closed_intent = (
                    update(outbound_messages)
                    .where(
                        self._turn_scope(item["id"]),
                        outbound_messages.c.status == "intent",
                    )
                    .values(status="unknown")
                )
                if fence is not None:
                    closed_intent = closed_intent.where(fence)
                conn.execute(closed_intent)
                self._interrupt_running_steps(conn, str(item["id"]), now)
                claimed.append(item)
        return claimed, dead

    def _next_step_index(self, conn, turn_id: str) -> int:
        """Allocate the next index under the turn row lock, so two workers never collide."""
        conn.execute(select(turns.c.id).where(turns.c.id == str(turn_id)).with_for_update())
        last = conn.execute(
            select(func.max(turn_steps.c.step_index)).where(
                turn_steps.c.turn_id == str(turn_id)
            )
        ).scalar()
        return int(last) + 1 if last is not None else 0

    def begin_step(
        self,
        turn_id: str,
        *,
        step: str,
        tool: str,
        args: object,
        target: str = "",
        approval_class: str = "none",
        started_at: float | None = None,
    ) -> int:
        """Write the step row as `running` before the tool runs; return its index.

        ADR-0003 §4.3: the row is the crash evidence. A process that dies between this write and
        `finish_step` leaves `running`, which the next claim turns into `interrupted`.
        """
        now = time.time()
        # A step row belongs to no generation column of its own: only a bound lease fences it.
        fence = self.fence_clause()
        with self.db.connect() as conn:
            index = self._next_step_index(conn, str(turn_id))
            row = {
                "turn_id": str(turn_id),
                "step_index": index,
                "step": str(step),
                "tool": str(tool),
                "target": str(target),
                "approval_class": str(approval_class),
                "args_hash": args_hash(str(tool), args),
                "result": None,
                "status": STEP_RUNNING,
                "started_at": float(started_at if started_at is not None else now),
                "finished_at": None,
            }
            added = conn.execute(
                fenced_insert(turn_steps, row, fence).returning(turn_steps.c.turn_id)
            ).first()
            if fence is not None and added is None:
                raise Superseded(f"lease generation moved on: step {index} refused")
            return index

    def finish_step(
        self,
        turn_id: str,
        index: int,
        *,
        result: object,
        status: str = STEP_EXECUTED,
        finished_at: float | None = None,
    ) -> None:
        """Finalize a `running` step with its result and its real outcome."""
        now = time.time()
        fence = self.fence_clause()
        statement = (
            update(turn_steps)
            .where(
                turn_steps.c.turn_id == str(turn_id),
                turn_steps.c.step_index == int(index),
            )
            .values(
                result=_clip(result),
                status=str(status or STEP_EXECUTED),
                finished_at=float(finished_at if finished_at is not None else now),
            )
        )
        if fence is not None:
            statement = statement.where(fence)
        with self.db.connect() as conn:
            conn.execute(statement)

    def unfinished_threads(self) -> list[dict]:
        statement = (
            select(turns.c.thread_id, turns.c.id, turns.c.status, turns.c.updated_at)
            .where(turns.c.status.in_(("received", "running", "sending")))
            .order_by(turns.c.updated_at)
        )
        return self.db.fetch_all(statement)
