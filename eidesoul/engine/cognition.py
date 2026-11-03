"""ADR-0003 cognition worker: one job table, two runners, the claim and the reaper.

Worker C owns `capture`, `consolidate`, `reflect`, and `distill`; the slow lane owns
`backfill` and `audit`. Every job is claimed with `FOR UPDATE SKIP LOCKED` plus a lease, a
failed attempt backs off, and `dead_letter_after` moves it to `dead`. The intake transaction
writes the `capture` job on the same connection as the message, the turn, and the journal, so
the four rows commit or roll back together (ADR-0011 §4.1).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import io, observability
from .config import Config
from .db import Database
from .lease import Superseded
from .memory import assess as memory_assess
from .memory import efficiency, gate, intent
from .memory import episodes as memory_episodes
from .memory import reflect as memory_reflect
from .schema import actions, cognition_jobs

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
        """`FOR UPDATE SKIP LOCKED`: two workers never take the same job (ADR-0012 §4.4)."""
        if self.db is None:
            return []
        moment = time.time() if now is None else float(now)
        wanted = " AND kind = :kind" if kind else ""
        statement = text(
            "UPDATE cognition_jobs SET status = 'claimed', claimed_by = :worker, "
            "lease_until = :lease, attempts = attempts + 1, updated_at = :now, "
            "generation = :generation "
            "WHERE id IN (SELECT id FROM cognition_jobs "
            "WHERE status = 'queued' AND next_attempt_at <= :now" + wanted + " "
            "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT :limit) "
            "RETURNING id, kind, scope, payload_ref, attempts, idempotency_key, generation"
        )
        params = {
            "worker": str(worker),
            "lease": moment + max(1.0, float(lease_seconds)),
            "now": moment,
            "limit": max(1, int(limit)),
            "generation": int(generation or 0),
        }
        if kind:
            params["kind"] = str(kind)
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
        """Update one job; `False` when the lease generation moved on (ADR-0012 §4.4).

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
                live = conn.execute(
                    text("SELECT generation FROM engine_lease WHERE id = 1")
                ).scalar()
                if not isinstance(live, int) or live != int(generation):
                    log.warning("job %s superseded: generation moved", job_id)
                    return False
                statement = statement.where(cognition_jobs.c.generation == int(generation))
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
        with self.db.connect() as conn:
            dead = conn.execute(
                cognition_jobs.update()
                .where(
                    cognition_jobs.c.status == "claimed",
                    cognition_jobs.c.lease_until < moment,
                    cognition_jobs.c.attempts >= limit,
                )
                .values(status="dead", claimed_by="", lease_until=0.0, updated_at=moment)
            )
            requeued = conn.execute(
                cognition_jobs.update()
                .where(
                    cognition_jobs.c.status == "claimed",
                    cognition_jobs.c.lease_until < moment,
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


class CognitionWorker:
    """Worker C: in-process, only while primary, never blocks the event loop."""

    def __init__(
        self,
        db: Database | None,
        config: Config,
        memory,
        *,
        llm=None,
        skills=None,
        history=None,
        language: str = "id",
        known_tools: Sequence[str] = (),
        ask=None,
        worker_id: str = "",
        lease=None,
        ask_dispute=None,
        propose=None,
    ) -> None:
        self.db = db
        self.config = config
        self.memory = memory
        self.llm = llm
        self.skills = skills
        self.history = history
        self.language = str(language or "id")
        self.known_tools = tuple(str(item) for item in known_tools if str(item or ""))
        self.ask = ask
        self.lease = lease
        self.ask_dispute = ask_dispute
        self.propose = propose
        self.queue = CognitionQueue(db, config)
        self.worker_id = str(worker_id or f"{uuid.uuid4().hex[:8]}")
        self.running = False
        self._task: asyncio.Task | None = None
        self._last: dict[str, float] = {}
        self.counters: dict[str, int] = {
            "jobs_done": 0,
            "jobs_failed": 0,
            "captured": 0,
            "reflected": 0,
            "distilled": 0,
            "reaped": 0,
        }
        if history is not None and getattr(history, "job_sink", None) is None:
            history.job_sink = self.queue

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self.running = True
        self._task = asyncio.create_task(self.run(), name="cognition-worker")
        log.info("cognition worker %s started", self.worker_id)

    async def stop(self, *, close_episodes: bool = False) -> None:
        """Stop taking jobs; a job in flight is finished, then the loop exits (ADR-0011 §4.4)."""
        self.running = False
        if close_episodes:
            closed = await io.run(memory_episodes.close_all, self.db, signal="shutdown")
            moment = time.time()
            for episode_id in closed:
                await self._enqueue_distill(episode_id, moment)
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("cognition worker stopped with an error")

    async def run(self) -> None:
        tick = max(0.1, float(getattr(self.config.memory.worker, "tick_seconds", 1.0)))
        while self.running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("cognition tick failed")
            await asyncio.sleep(tick)

    async def tick(self) -> None:
        if self.db is None:
            return
        now = time.time()
        if now - self._last.get("reap", 0.0) >= float(self.config.worker.reaper.every_seconds):
            self._last["reap"] = now
            self.counters["reaped"] += await io.run(self.queue.reap, now=now)
        await self._schedule(now)
        limit = int(getattr(self.config.memory.worker, "concurrency", 2))
        jobs = await io.run(
            self.queue.claim,
            worker=self.worker_id,
            limit=limit,
            now=now,
            generation=self._generation(),
        )
        if not jobs:
            return
        batches: dict[str, list[Job]] = {}
        for job in jobs:
            if job.kind in {"capture", "consolidate"}:
                batches.setdefault(job.kind, []).append(job)
            else:
                batches.setdefault(job.id, []).append(job)
        await asyncio.gather(
            *(self._run_batch(kind, group) for kind, group in batches.items()),
            return_exceptions=True,
        )

    def _generation(self) -> int:
        """The lease generation this worker writes under; 0 when no lease is attached."""
        return int(getattr(self.lease, "generation", 0) or 0)

    async def _run_batch(self, label: str, jobs: list[Job]) -> None:
        if not jobs:
            return
        # Background work carries its own run id and never a turn id (ADR-0019 §4.2).
        tokens = observability.enter(run_id=f"job:{label}")
        try:
            if jobs[0].kind == "capture":
                results = await self._capture(jobs)
            elif jobs[0].kind == "consolidate":
                results = await self._consolidate(jobs)
            else:
                results = {jobs[0].id: await self._handle(jobs[0])}
        except Superseded:
            # The lease moved on mid-batch: the writes were refused, so the job stays claimed
            # and the reaper hands it to the new primary (ADR-0012 §4.4).
            log.warning("cognition %s superseded: generation moved", label)
            return
        except asyncio.CancelledError:
            for job in jobs:
                await io.run(
                    self.queue.fail, job.id, "cancelled", generation=job.generation
                )
            raise
        except Exception as exc:
            log.warning("cognition %s failed: %s: %s", label, type(exc).__name__, exc)
            for job in jobs:
                await io.run(
                    self.queue.fail,
                    job.id,
                    f"{type(exc).__name__}: {exc}",
                    generation=job.generation,
                )
            self.counters["jobs_failed"] += len(jobs)
            return
        finally:
            observability.leave(tokens)
        for job in jobs:
            await io.run(
                self.queue.complete, job.id, results.get(job.id, ""), generation=job.generation
            )
        self.counters["jobs_done"] += len(jobs)

    async def drain(self, *, kind: str = "", limit: int = 1) -> dict[str, str]:
        """Claim and run one batch now (deterministic tests, a manual kick, a slow lane)."""
        jobs = await io.run(
            self.queue.claim,
            worker=self.worker_id,
            limit=limit,
            kind=kind,
            generation=self._generation(),
        )
        if not jobs:
            return {}
        results: dict[str, str] = {}
        grouped: dict[str, list[Job]] = {}
        for job in jobs:
            label = job.kind if job.kind in {"capture", "consolidate"} else job.id
            grouped.setdefault(label, []).append(job)
        for group in grouped.values():
            if group[0].kind == "capture":
                results.update(await self._capture(group))
            elif group[0].kind == "consolidate":
                results.update(await self._consolidate(group))
            else:
                for job in group:
                    results[job.id] = await self._handle(job)
        for job in jobs:
            await io.run(
                self.queue.complete, job.id, results.get(job.id, ""), generation=job.generation
            )
        self.counters["jobs_done"] += len(jobs)
        return results

    # ------------------------------------------------------------ scheduling

    async def _schedule(self, now: float) -> None:
        if getattr(self.memory, "db", None) is None:
            return
        settings = self.config.memory.worker
        if self.skills is not None and self.config.skills.enabled:
            every = max(1.0, float(settings.consolidation_every_seconds))
            if now - self._last.get("consolidate", 0.0) >= every:
                self._last["consolidate"] = now
                window = int(now // every)
                await io.run(
                    self.queue.enqueue_job,
                    "consolidate",
                    "global",
                    "",
                    key=f"consolidate:{window}",
                    now=now,
                )
                closed = await io.run(
                    memory_episodes.scan,
                    self.db,
                    now=now,
                    idle_minutes=float(self.config.skills.task_idle_minutes),
                    idle_after_send_minutes=float(
                        self.config.skills.idle_after_send_minutes
                    ),
                )
                for episode_id in closed:
                    await self._enqueue_distill(episode_id, now)
        if now - self._last.get("audit", 0.0) >= float(settings.audit_every_seconds):
            self._last["audit"] = now
            window = int(now // max(60.0, float(settings.audit_every_seconds)))
            await io.run(
                self.queue.enqueue_job,
                "audit",
                "global",
                "",
                key=f"audit:{window}",
                now=now,
            )
        await self._schedule_reflection(now)

    async def _schedule_reflection(self, now: float) -> None:
        reflection = self.config.reflection
        if not reflection.enabled:
            return
        every = max(1, int(reflection.every_minutes)) * SECONDS
        if now - self._last.get("reflect", 0.0) < every:
            return
        self._last["reflect"] = now
        scopes = await io.run(self.memory.scopes)
        taken = 0
        for scope in scopes:
            if taken >= max(1, int(reflection.scopes_per_pass)):
                break
            since = await io.run(self.queue.last_done, "reflect", scope)
            new_rows = await io.run(self.memory.count_rows_since, scope=scope, after=since)
            staged = await io.run(self.memory.count_staged, scope=scope)
            if new_rows < int(reflection.min_new_rows) and staged == 0:
                continue
            if int(reflection.passes_per_day) > 0:
                passes = await io.run(
                    self.queue.count_since, "reflect", scope=scope, after=now - DAY
                )
                if passes >= int(reflection.passes_per_day):
                    continue
            window = int(now // every)
            await io.run(
                self.queue.enqueue_job,
                "reflect",
                scope,
                "",
                key=f"reflect:{scope}:{window}",
                now=now,
            )
            taken += 1

    async def _enqueue_distill(self, episode_id: str, now: float) -> None:
        episode = await io.run(memory_episodes.get, self.db, episode_id)
        if episode is None:
            return
        steps = await io.run(memory_episodes.steps_of, self.db, episode_id)
        ok, reason = memory_episodes.admission(episode, steps)
        if not ok:
            log.debug("episode %s not admitted: %s", episode_id, reason)
            return
        await io.run(
            self.queue.enqueue_job,
            "distill",
            f"thread:{episode.thread_id}",
            episode_id,
            key=f"distill:{episode_id}",
            now=now,
        )

    # ------------------------------------------------------------ handlers

    async def _handle(self, job: Job) -> str:
        if job.kind == "reflect":
            return await self._reflect(job)
        if job.kind == "distill":
            return await self._distill(job)
        if job.kind == "backfill":
            return await self._backfill(job)
        if job.kind == "audit":
            return await self._audit(job)
        return f"unhandled {job.kind}"

    async def _capture(self, jobs: list[Job]) -> dict[str, str]:
        """Stage 2 + 3 of ADR-0002, batched per scope: novelty, then one assess call."""
        results: dict[str, str] = {}
        capture = self.config.memory.capture
        grouped: dict[str, list[Job]] = {}
        for job in jobs:
            grouped.setdefault(job.scope, []).append(job)
        for scope, group in grouped.items():
            rows: list[tuple[Job, dict]] = []
            for job in group:
                row = await io.run(self.memory.candidate_by_event, job.payload_ref)
                if row is None or str(row.get("state") or "") != "pending":
                    results[job.id] = "no candidate"
                    continue
                rows.append((job, row))
            if not rows:
                continue
            kind, value = _scope_of(scope)
            person_id = str(rows[0][1].get("person_id") or "") if kind != "thread" else ""
            thread_id = str(rows[0][1].get("thread_id") or "")
            pool = await io.run(
                self.memory.novelty_pool,
                scope=scope,
                person_id=person_id,
                thread_id=thread_id,
                limit=max(50, int(self.config.memory.worker.capture_max_items) * 10),
            )
            novel: list[tuple[Job, dict]] = []
            for job, row in rows:
                found = gate.novelty(
                    str(row.get("text") or ""),
                    pool,
                    threshold=float(capture.novelty_threshold),
                )
                if found.verdict == "duplicate":
                    await io.run(self.memory.mark_not_worth, str(row["id"]), "duplicate")
                    results[job.id] = "duplicate"
                    continue
                if found.verdict == "known":
                    await io.run(
                        self.memory.mark_supersede_candidate,
                        str(row["id"]),
                        found.source_id,
                    )
                    results[job.id] = "supersede candidate"
                    continue
                novel.append((job, row))
            if not novel:
                continue
            items = [
                {"id": str(row["id"]), "text": str(row.get("text") or "")}
                for _, row in novel
            ]
            verdicts = await memory_assess.assess_batch(
                self.llm,
                items,
                language=self.language,
                ask_level=int(self.config.memory.privacy.ask_level),
            )
            for job, row in novel:
                results[job.id] = await self._label(row, verdicts.get(str(row["id"])))
        return results

    async def _label(self, row: Mapping, verdict) -> str:
        memory_id = str(row["id"])
        if verdict is None or not verdict.valid:
            await io.run(self.memory.fail_candidate, memory_id, "labeling failed")
            return "dropped"
        if not verdict.worth:
            await io.run(self.memory.mark_not_worth, memory_id, "assess: not worth")
            return "not worth"
        group = str(row.get("audience") or "private") == "group"
        owner = str(row.get("owner_id") or "")
        subject = str(verdict.subject or "")
        rules = self.config.memory.privacy.group_capture
        if group and rules.self_only and subject and subject != owner:
            if rules.third_party == "deny":
                await io.run(self.memory.defer_candidate, memory_id, reason="third party")
                return "third party held"
            await io.run(self.memory.mark_not_worth, memory_id, "third party")
            return "third party dropped"
        if group and verdict.ask and not rules.ask_in_group:
            await io.run(self.memory.defer_candidate, memory_id, reason="ask in group")
            return "ask pending"
        category, durability = await self._remember_hint(row, verdict)
        labelled = await io.run(
            self.memory.label_candidate,
            memory_id,
            level=int(verdict.level),
            confirmed=not bool(verdict.ask),
            subject_id=subject or owner,
            category=category,
            durability=durability,
            kind=str(verdict.kind or ""),
            text=str(verdict.text or ""),
        )
        if not labelled:
            return "label refused"
        self.counters["captured"] += 1
        await self._visibility(row, int(verdict.level))
        if verdict.ask and self.ask is not None:
            try:
                await self.ask(
                    str(row.get("thread_id") or ""),
                    owner,
                    [memory_id],
                    int(verdict.level),
                    "",
                )
            except Exception:
                log.debug("ask callback failed", exc_info=True)
        return f"ok L{verdict.level}"

    async def _remember_hint(self, row: Mapping, verdict) -> tuple[str, str]:
        """ADR-0005 §4.6: "remember this" raises durability and pins only a core memory.

        The hint never overrides a verdict: a `not worth` candidate is dropped before this runs,
        and the hard exclusion already refused credentials and instructions at intake. The
        embedding runs on the bounded pool, never on the event loop (ADR-0017 §4.1).
        """
        category = str(verdict.category or "")
        durability = str(verdict.durability or "")
        settings = getattr(self.config.memory, "intent", None)
        text = str(verdict.text or row.get("text") or "")
        kind = await io.run(intent.classify, text, self.language, settings)
        if kind != "remember":
            return category, durability
        if durability != "core":
            durability = "core"
        return category, durability

    async def _visibility(self, row: Mapping, level: int) -> None:
        if self.history is None:
            return
        refs = [str(item) for item in row.get("refs") or []]
        ids = [item.split(":", 1)[1] for item in refs if item.startswith("message:")]
        if not ids:
            return
        try:
            await io.run(
                self.history.set_visibility, str(row.get("thread_id") or ""), ids, level
            )
        except Exception:
            log.debug("visibility update failed", exc_info=True)

    async def _consolidate(self, jobs: list[Job]) -> dict[str, str]:
        results: dict[str, str] = {}
        for job in jobs:
            resolved = 0
            rows = await io.run(
                self.memory.supersede_candidates, scope=job.scope, limit=50
            )
            for row in rows:
                marker = next(
                    (
                        item.split(":", 1)[1]
                        for item in row.get("refs") or []
                        if str(item).startswith("supersede:")
                    ),
                    "",
                )
                if marker and await io.run(
                    self.memory.resolve_supersede_candidate, str(row["id"]), marker
                ):
                    resolved += 1
            promoted = await io.run(self.memory.promote_staged, limit=50)
            decayed = 0
            if self.skills is not None:
                decayed = await io.run(
                    self.skills.decay, days=float(self.config.skills.use_renewal_days)
                )
            results[job.id] = f"supersede {resolved} promote {promoted} decay {decayed}"
        return results

    async def _reflect(self, job: Job) -> str:
        settings = self.config.reflection
        if not settings.enabled:
            return "reflection disabled"
        # `run_pass` is a coroutine (it awaits the ruling model); `io.run` would hand back an
        # un-awaited coroutine, so the pass is awaited directly under the same budget.
        report = await asyncio.wait_for(
            memory_reflect.run_pass(
                self.memory,
                scope=job.scope,
                llm=self.llm,
                language=self.language,
                max_items=int(settings.items_per_pass),
                max_writes=int(settings.writes_per_pass),
                min_candidates=int(settings.min_candidates),
                dispute_ttl_days=float(settings.dispute_ttl_days),
                derive_depth=int(settings.derive_depth),
            ),
            timeout=float(settings.seconds_per_pass),
        )
        self.counters["reflected"] += 1
        await self._ladder()
        await self._ask_dispute(report)
        return (
            f"candidates {report.candidates} writes {report.writes} "
            f"disputes {report.disputes} promoted {report.staged_promoted} "
            f"stale {report.stale} {report.skipped}".strip()
        )

    # -------------------------------------------------------------- ladder

    LADDER_ACTIONS = ("keep", "merge", "promote", "archive", "propose_automation")
    LADDER_BATCH = 5
    LADDER_PROMPT = (
        "You compare episodes of the same procedure and pick one ladder action. Reply ONLY with "
        "JSON {\"action\":\"keep|merge|promote|archive|propose_automation\",\"skill\":\"\","
        "\"reason\":\"\",\"refs\":[],\"manifest\":null}. Choose keep when the evidence is "
        "unclear, merge when two procedures are the same job, promote when the procedure "
        "clearly saves work, archive when it keeps failing, and propose_automation when the "
        "same steps repeat on a schedule. Never invent a skill name; use only the ones given."
    )

    async def _ladder(self) -> None:
        """ADR-0020 §4.6: compare eligible signatures and apply one deterministic action."""
        settings = self.config.skills
        if not getattr(settings, "efficiency_enabled", False) or self.skills is None:
            return
        if self.db is None or self.llm is None:
            return
        moment = time.time()
        window_days = float(getattr(settings, "efficiency_window_days", 30.0))
        signatures = await io.run(
            efficiency.signatures, self.db, window_days=window_days, now=moment
        )
        for signature in signatures[: self.LADDER_BATCH]:
            found = await io.run(
                efficiency.evidence, self.db, signature, window_days=window_days, now=moment
            )
            if not efficiency.eligible(
                found,
                min_episodes=int(getattr(settings, "efficiency_min_evidence_episodes", 2)),
            ):
                continue
            names = await io.run(self.skills.evidence_for_signature, signature)
            if not names:
                continue
            decided = await self._ladder_decision(found, names)
            if decided is None or str(decided.get("action")) == "keep":
                continue
            await self._ladder_apply(str(decided.get("action")), decided, names, found)

    async def _ladder_decision(self, found, names: list[str]) -> dict | None:
        payload = {
            "signature": found.signature,
            "episodes": found.as_dict(),
            "skills": [str(name) for name in names],
            "language": self.language,
        }
        try:
            raw = await self.llm.complete(
                self.LADDER_PROMPT, json.dumps(payload), max_tokens=500
            )
        except Exception:
            log.debug("ladder decision failed", exc_info=True)
            return None
        body = _json_object(raw)
        if body is None:
            return None
        action = str(body.get("action") or "").strip().lower()
        skill = str(body.get("skill") or "").strip().lower()
        if action not in self.LADDER_ACTIONS:
            return None
        if action != "keep" and skill not in names:
            return None
        body["action"] = action
        body["skill"] = skill
        return body

    async def _ladder_apply(self, action: str, decided: dict, names: list[str], found) -> None:
        skill = str(decided.get("skill") or "")
        reason = str(decided.get("reason") or "")[:200]
        if action == "promote":
            current = await io.run(self.skills.get, skill)
            if current is None:
                return
            await io.run(
                self.skills.revise,
                current.name,
                current.description,
                current.body,
                scope=current.scope,
                stated_by=current.stated_by,
                trust=current.trust,
            )
            await io.run(self.skills.set_priority, current.name, current.priority + 1)
            await io.run(self.skills.record_notice, current.name, f"promoted: {reason}")
            self.counters["distilled"] += 1
            return
        if action == "merge":
            survivor = await io.run(self.skills.get, skill)
            if survivor is None:
                return
            others = [item for item in names if item != skill]
            merged = survivor.body + "\n\n" + "\n".join(
                f"# {item}" for item in others
            )
            await io.run(
                self.skills.revise,
                survivor.name,
                survivor.description,
                merged[: int(getattr(self.config.skills, "max_body_chars", 18000))],
                scope=survivor.scope,
                stated_by=survivor.stated_by,
                trust=survivor.trust,
            )
            await io.run(self.skills.record_notice, survivor.name, f"merged: {reason}")
            return
        if action == "archive":
            try:
                await io.run(self.skills.archive, skill)
            except KeyError:
                return
            await io.run(self.skills.record_notice, skill, f"archived: {reason}")
            return
        if action == "propose_automation":
            await self._ladder_propose(skill, decided, found, reason)

    async def _ladder_propose(self, skill: str, decided: dict, found, reason: str) -> None:
        """Ladder step 8: one approval, nothing stored until the owner approves it."""
        settings = self.config.skills
        if not getattr(settings, "ladder_enabled", False) or self.propose is None:
            return
        if await io.run(self._proposal_recent, skill):
            await io.run(
                self.skills.record_notice, skill, f"proposal skipped (cooldown): {reason}"
            )
            return
        manifest = decided.get("manifest")
        if not isinstance(manifest, dict):
            await io.run(self.skills.record_notice, skill, "proposal skipped (no manifest)")
            return
        person = found.person_ids[0] if found.person_ids else ""
        thread = ""
        if person and self.history is not None:
            thread = await io.run(self.history.dm_thread, person)
        if not thread:
            await io.run(self.skills.record_notice, skill, "proposal skipped (no dm)")
            return
        try:
            await self.propose(manifest, thread, None)
        except Exception as exc:
            await io.run(
                self.skills.record_notice, skill, f"proposal refused: {type(exc).__name__}"
            )
            return
        await io.run(self.skills.record_notice, skill, f"proposal:{skill}: {reason}")

    def _proposal_recent(self, skill: str) -> bool:
        """Cooldown: the same skill was proposed inside `proposal_cooldown_days`."""
        days = float(getattr(self.config.skills, "ladder_proposal_cooldown_days", 14.0))
        if days <= 0:
            return False
        cutoff = time.time() - days * DAY
        found = self.db.fetch_one(
            select(actions.c.id)
            .where(
                actions.c.kind == "skill",
                actions.c.ts >= cutoff,
                actions.c.summary.like(f"proposal:{skill}:%"),
            )
            .limit(1)
        )
        return found is not None

    async def _ask_dispute(self, report) -> None:
        """One question per pass for the first opened pair that names a subject (ADR-0010 §4.7)."""
        if self.ask_dispute is None:
            return
        for pair in getattr(report, "opened", ()) or ():
            if not str(pair.get("subject_id") or "").strip():
                continue
            try:
                await self.ask_dispute(str(pair["subject_id"]), pair)
            except Exception:
                log.warning("dispute question failed", exc_info=True)
            return

    async def _distill(self, job: Job) -> str:
        settings = self.config.skills
        if not settings.enabled or self.skills is None:
            return "skills disabled"
        episode = await io.run(memory_episodes.get, self.db, job.payload_ref)
        if episode is None:
            return "episode missing"
        if episode.distilled_at:
            return "already distilled"
        steps = await io.run(memory_episodes.steps_of, self.db, episode.id)
        ok, reason = memory_episodes.admission(episode, steps)
        if not ok:
            return f"not admitted: {reason}"
        procedure = await self._procedure(episode, steps)
        if procedure is None:
            return "no procedure"
        name, description, body = procedure
        try:
            await io.run(
                self.skills.revise,
                name,
                description,
                body,
                source_episode=episode.id,
                scope="thread",
                stated_by=episode.thread_id,
                trust="T2",
            )
        except (ValueError, FileExistsError) as exc:
            return f"procedure refused: {exc}"
        await io.run(memory_episodes.mark_distilled, self.db, episode.id)
        self.counters["distilled"] += 1
        return f"procedure {name}"

    async def _procedure(self, episode, steps: list[dict]) -> tuple[str, str, str] | None:
        """One procedure per episode: the LLM when present, a deterministic trace otherwise."""
        tools = [str(step.get("tool") or "") for step in steps if str(step.get("tool") or "")]
        name = memory_episodes.slug(episode.signature or "episode")
        description = f"Repeat the {'→'.join(dict.fromkeys(tools)) or 'task'} procedure."
        body = (
            f"Observed in episode {episode.id} ({episode.turns} turns, "
            f"{episode.tool_calls} tool calls).\n\n"
            + "\n".join(f"{index}. {tool}" for index, tool in enumerate(tools, start=1))
        ).strip()
        if self.llm is not None and tools:
            drafted = await self._draft(episode, steps)
            if drafted is not None:
                name, description, body = drafted
        if not self._tools_allowed(body):
            return None
        return name, description[:200], body

    async def _draft(self, episode, steps: list[dict]) -> tuple[str, str, str] | None:
        payload = {
            "episode": episode.id,
            "turns": episode.turns,
            "tools": [str(step.get("tool") or "") for step in steps],
            "language": self.language,
        }
        prompt = (
            "Turn one finished task into one reusable procedure. Reply ONLY with JSON "
            '{"worth":true,"name":"lowercase-hyphen-name","description":"","body":""}. '
            "The body is a short ordered recipe that mentions only the tools listed. "
            "worth=false when the task is not repeatable."
        )
        try:
            raw = await self.llm.complete(prompt, json.dumps(payload), max_tokens=600)
        except Exception:
            log.debug("distill draft failed", exc_info=True)
            return None
        start, end = str(raw or "").find("{"), str(raw or "").rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            found = json.loads(str(raw)[start : end + 1])
        except (TypeError, ValueError):
            return None
        if not isinstance(found, dict) or not found.get("worth", True):
            return None
        name = str(found.get("name") or "")
        description = str(found.get("description") or "")
        body = str(found.get("body") or "")
        if not name or not description or not body:
            return None
        return name, description, body

    def _tools_allowed(self, body: str) -> bool:
        """A procedure may only mention tools that already exist in the catalog."""
        if not self.known_tools:
            return True
        known = set(self.known_tools)
        mentioned = set(TOOL.findall(str(body or "")))
        unknown = {
            word
            for word in mentioned
            if word not in known and re.search(rf"\b{re.escape(word)}\b\s*\(", body)
        }
        return not unknown

    async def _backfill(self, job: Job) -> str:
        added = await self.memory.backfill_index(
            limit=max(1, int(self.config.reflection.items_per_pass))
        )
        return f"indexed {added}"

    async def _audit(self, job: Job) -> str:
        settings = self.config.memory.retention
        report = await self.memory.purge(
            grace_days=float(settings.purge_grace_days),
            batch=int(settings.purge_batch),
        )
        stale = await io.run(
            self.memory.stale_disputes,
            ttl_days=float(self.config.reflection.dispute_ttl_days),
        )
        return f"purged {report.purged} selected {report.selected} stale {stale}"

    def status(self) -> dict[str, int]:
        queue = {f"queue.{key}": value for key, value in self.queue.stats().items()}
        return {**self.counters, **queue}
