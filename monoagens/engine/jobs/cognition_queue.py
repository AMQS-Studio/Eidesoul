"""The one cognition queue (ADR-0003 §5.4): enqueue, claim, complete, fail, reap, stats."""

import json
import logging
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..config import Config
from ..db import Database
from ..schema import cognition_jobs
from .lease import LIVE_SQL, TTL, caller_clause, row_clause

log = logging.getLogger(__name__)

KINDS = ("capture", "consolidate", "reflect", "backfill", "audit", "distill")
DAY = 86400.0
SECONDS = 60.0
TOOL = re.compile(r"\b([a-z][a-z0-9_]{1,40})\b")

@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    scope: str
    payload_ref: str
    attempts: int
    idempotency_key: str
    generation: int = 0

def _json_object(text: str) -> dict | None:
    """The first JSON object in one model answer; anything else is unusable, never guessed."""
    raw = str(text or "").strip()
    if not raw:
        return None
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        body = json.loads(raw[start : end + 1])
    except (TypeError, ValueError):
        return None
    return body if isinstance(body, dict) else None

def _scope_of(scope: str) -> tuple[str, str]:
    head, _, value = str(scope or "").partition(":")
    if head == "person":
        return "person", value
    if head == "thread":
        return "thread", value
    return "global", ""

class CognitionQueue:
    """The one queue (ADR-0003 §5.4): enqueue, claim, complete, fail, reap, stats."""

    def __init__(self, db: Database | None, config: Config) -> None:
        self.db = db
        self.config = config
        self.settings = config.memory.worker

    # ------------------------------------------------------------ enqueue

    def enqueue(self, conn, candidate: object) -> None:
        """`CaptureJobSink`: the intake transaction's capture job (never a second write path)."""
        event_id = str(getattr(candidate, "event_id", "") or "")
        if not event_id:
            return
        person_id = str(getattr(candidate, "person_id", "") or "")
        thread_id = str(getattr(candidate, "thread_id", "") or "")
        scope = f"person:{person_id}" if person_id else f"thread:{thread_id}"
        self._insert(
            conn,
            kind="capture",
            scope=scope,
            payload_ref=event_id,
            key=f"capture:{event_id}",
        )

    def enqueue_job(
        self,
        kind: str,
        scope: str,
        payload_ref: str = "",
        *,
        key: str = "",
        conn=None,
        now: float | None = None,
    ) -> bool:
        """Idempotent enqueue: one job per key, bounded by `queue_cap`."""
        if self.db is None or str(kind) not in KINDS:
            return False
        identity = str(key or f"{kind}:{scope}:{payload_ref}")
        if conn is not None:
            return self._insert(
                conn, kind=kind, scope=scope, payload_ref=payload_ref, key=identity, now=now
            )
        with self.db.connect() as own:
            return self._insert(
                own, kind=kind, scope=scope, payload_ref=payload_ref, key=identity, now=now
            )

    def _insert(
        self,
        conn,
        *,
        kind: str,
        scope: str,
        payload_ref: str,
        key: str,
        now: float | None = None,
    ) -> bool:
        moment = time.time() if now is None else float(now)
        cap = int(getattr(self.settings, "queue_cap", 5000))
        backlog = conn.execute(
            select(func.count()).select_from(cognition_jobs).where(
                cognition_jobs.c.status == "queued"
            )
        ).scalar()
        if cap and int(backlog or 0) >= cap:
            log.warning("cognition queue is full (%s); dropping %s", backlog, kind)
            return False
        inserted = conn.execute(
            pg_insert(cognition_jobs)
            .values(
                id=str(uuid.uuid4()),
                kind=str(kind),
                scope=str(scope or "global"),
                payload_ref=str(payload_ref or ""),
                status="queued",
                claimed_by="",
                lease_until=0.0,
                attempts=0,
                next_attempt_at=0.0,
                result_ref="",
                idempotency_key=str(key),
                created_at=moment,
                updated_at=moment,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(cognition_jobs.c.id)
        ).scalar()
        return inserted is not None

    # ------------------------------------------------------------ claim

    def claim(
        self,
        *,
        worker: str,
        limit: int = 1,
        lease_seconds: float = 60.0,
        kind: str = "",
        now: float | None = None,
        generation: int = 0,
    ) -> list[Job]:
        """`FOR UPDATE SKIP LOCKED`: two workers never take the same job (ADR-0002 §4.4).

        The claim lease is never shorter than one model call plus a minute of slack, so a slow
        LLM answer cannot let the reaper hand the same job to the next primary mid-call. Every
        lease stamp and comparison uses the database clock, never the application clock.
        """
        if self.db is None:
            return []
        moment = time.time() if now is None else float(now)
        wanted = " AND kind = :kind" if kind else ""
        lease = max(1.0, float(lease_seconds), self.lease_floor())
        fence = caller_clause(generation)
        statement = text(
            "UPDATE cognition_jobs SET status = 'claimed', claimed_by = :worker, "
            "lease_until = EXTRACT(EPOCH FROM clock_timestamp()) + :lease, "
            "attempts = attempts + 1, updated_at = :now, "
            "generation = :generation "
            "WHERE id IN (SELECT id FROM cognition_jobs "
            "WHERE status = 'queued' AND next_attempt_at <= :now" + wanted + " "
            "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT :limit) "
            + (f"AND :fence_gen = {LIVE_SQL} " if fence is not None else "")
            + "RETURNING id, kind, scope, payload_ref, attempts, idempotency_key, generation"
        )
        params = {
            "worker": str(worker),
            "lease": lease,
            "now": moment,
            "limit": max(1, int(limit)),
            "generation": int(generation or 0),
        }
        if kind:
            params["kind"] = str(kind)
        if fence is not None:
            params.update({"fence_gen": int(generation), "fence_ttl": float(TTL)})
        with self.db.connect() as conn:
            rows = conn.execute(statement, params).mappings().all()
        return [
            Job(
                id=str(row["id"]),
                kind=str(row["kind"]),
                scope=str(row["scope"]),
                payload_ref=str(row["payload_ref"] or ""),
                attempts=int(row["attempts"] or 0),
                idempotency_key=str(row["idempotency_key"]),
                generation=int(row["generation"] or 0),
            )
            for row in rows
        ]

    def lease_floor(self) -> float:
        """One LLM call plus a minute: a claim lease shorter than that invites a double run."""
        timeout = float(getattr(getattr(self.config, "llm", None), "timeout_seconds", 0.0) or 0.0)
        return timeout + 60.0

    def extend_claims(
        self, job_ids: Sequence[str], *, generation: int = 0, seconds: float = 0.0
    ) -> int:
        """Renew the claim leases of in-flight jobs while a slow call is still running."""
        wanted = [str(item) for item in job_ids if str(item or "").strip()]
        if self.db is None or not wanted:
            return 0
        span = max(1.0, float(seconds or 0.0), self.lease_floor())
        where = [cognition_jobs.c.id.in_(wanted), cognition_jobs.c.status == "claimed"]
        params: dict = {"renew_seconds": span}
        if generation:
            where.append(cognition_jobs.c.generation == int(generation))
            params.update({"fence_gen": int(generation), "fence_ttl": float(TTL)})
            where.append(text(f":fence_gen = {LIVE_SQL}"))
        statement = (
            cognition_jobs.update()
            .where(*where)
            .values(
                lease_until=text("EXTRACT(EPOCH FROM clock_timestamp()) + :renew_seconds"),
                updated_at=text("EXTRACT(EPOCH FROM clock_timestamp())"),
            )
        )
        with self.db.connect() as conn:
            return int(conn.execute(statement, params).rowcount or 0)

    def complete(self, job_id: str, result: str = "", *, generation: int | None = None) -> bool:
        return self._set(
            job_id,
            status="done",
            result_ref=str(result or "")[:2000],
            claimed_by="",
            lease_until=0.0,
            generation=generation,
        )

    def fail(
        self, job_id: str, reason: str, *, generation: int | None = None
    ) -> bool:
        """Retry with backoff; after `dead_letter_after` the job is `dead` and visible."""
        if self.db is None:
            return False
        row = self.db.fetch_one(
            select(cognition_jobs.c.attempts).where(cognition_jobs.c.id == str(job_id))
        )
        attempts = int(row["attempts"] or 0) if row else 0
        retry = self.settings.retry
        dead = attempts >= int(retry.dead_letter_after)
        delay = 0.0
        if not dead:
            delay = float(retry.backoff_seconds) * (2 ** max(0, attempts - 1))
        return self._set(
            job_id,
            status="dead" if dead else "queued",
            next_attempt_at=time.time() + delay,
            claimed_by="",
            lease_until=0.0,
            result_ref=("error:" + " ".join(str(reason or "").split()))[:2000],
            generation=generation,
        )

    def _set(self, job_id: str, *, generation: int | None = None, **values) -> bool:
        """Update one job; `False` when the lease generation moved on (ADR-0002 §4.4).

        A lease-bound worker checks the live generation on its own connection and matches the
        row's claim generation, so a replaced primary can neither finish nor retry a job the
        new one now owns. `generation=None` (or 0) is the unbound path: admin, reaper, tests.
        """
        if self.db is None:
            return False
        values["updated_at"] = time.time()
        statement = cognition_jobs.update().where(cognition_jobs.c.id == str(job_id))
        with self.db.connect() as conn:
            if generation:
                # The job row carries the generation it was claimed under; comparing it with
                # the live one inside the statement makes the fence atomic (ADR-0002 §4.4).
                statement = statement.where(
                    cognition_jobs.c.generation == int(generation),
                    row_clause("cognition_jobs.generation"),
                )
            changed = conn.execute(statement.values(**values)).rowcount
        if generation and not changed:
            log.warning("job %s superseded: generation moved", job_id)
            return False
        return True

    def reap(self, *, now: float | None = None) -> int:
        """A lease that passed returns to `queued`; a spent attempt count goes `dead`."""
        if self.db is None:
            return 0
        moment = time.time() if now is None else float(now)
        limit = int(self.settings.retry.dead_letter_after)
        expired = cognition_jobs.c.lease_until < text(
            "EXTRACT(EPOCH FROM clock_timestamp())"
        )
        with self.db.connect() as conn:
            dead = conn.execute(
                cognition_jobs.update()
                .where(
                    cognition_jobs.c.status == "claimed",
                    expired,
                    cognition_jobs.c.attempts >= limit,
                )
                .values(status="dead", claimed_by="", lease_until=0.0, updated_at=moment)
            )
            requeued = conn.execute(
                cognition_jobs.update()
                .where(
                    cognition_jobs.c.status == "claimed",
                    expired,
                    cognition_jobs.c.attempts < limit,
                )
                .values(
                    status="queued",
                    claimed_by="",
                    lease_until=0.0,
                    next_attempt_at=moment,
                    updated_at=moment,
                )
            )
        return int(dead.rowcount or 0) + int(requeued.rowcount or 0)

    def last_done(self, kind: str, scope: str = "") -> float:
        if self.db is None:
            return 0.0
        statement = select(func.max(cognition_jobs.c.created_at)).where(
            cognition_jobs.c.kind == str(kind), cognition_jobs.c.status == "done"
        )
        if scope:
            statement = statement.where(cognition_jobs.c.scope == str(scope))
        return float(self.db.scalar(statement) or 0.0)

    def count_since(self, kind: str, *, scope: str = "", after: float = 0.0) -> int:
        if self.db is None:
            return 0
        statement = (
            select(func.count())
            .select_from(cognition_jobs)
            .where(cognition_jobs.c.kind == str(kind), cognition_jobs.c.created_at > float(after))
        )
        if scope:
            statement = statement.where(cognition_jobs.c.scope == str(scope))
        return int(self.db.scalar(statement) or 0)

    def stats(self) -> dict[str, int]:
        """`/status` numbers: backlog by kind and status, oldest queued age."""
        if self.db is None:
            return {}
        rows = self.db.fetch_all(
            select(
                cognition_jobs.c.kind,
                cognition_jobs.c.status,
                func.count().label("count"),
            ).group_by(cognition_jobs.c.kind, cognition_jobs.c.status)
        )
        found = {f"{row['kind']}.{row['status']}": int(row["count"]) for row in rows}
        for status in ("queued", "claimed", "done", "failed", "dead"):
            found[status] = sum(
                int(row["count"]) for row in rows if str(row["status"]) == status
            )
        oldest = self.db.scalar(
            select(func.min(cognition_jobs.c.created_at)).where(
                cognition_jobs.c.status == "queued"
            )
        )
        if oldest:
            found["queued_age_seconds"] = int(max(0.0, time.time() - float(oldest)))
        return found
