"""Worker C: the in-process runner that claims cognition jobs and never blocks the loop."""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Sequence

from .. import io
from ..config import Config
from ..db import Database
from ..memory import episodes as memory_episodes
from ..turn import observability
from .cognition_capture import CognitionCapture
from .cognition_distill import CognitionDistill
from .cognition_ladder import CognitionLadder
from .cognition_queue import CognitionQueue, Job
from .cognition_schedule import CognitionSchedule
from .lease import Superseded

log = logging.getLogger(__name__)

class CognitionWorker(CognitionCapture, CognitionSchedule, CognitionLadder, CognitionDistill):
    """Worker C: in-process, only while primary, never blocks the event loop."""

    def __init__(
        self: CognitionWorker,
        db: Database | None,
        config: Config,
        memory,
        *,
        llm=None,
        skills=None,
        history=None,
        known_tools: Sequence[str] = (),
        ask=None,
        worker_id: str = "",
        lease=None,
        ask_dispute=None,
        propose=None,
        private_thread=None,
    ) -> None:
        self.db = db
        self.config = config
        self.memory = memory
        self.llm = llm
        self.skills = skills
        self.history = history
        self.language = str(config.soul.language)
        self.known_tools = tuple(str(item) for item in known_tools if str(item or ""))
        self.ask = ask
        self.lease = lease
        self.ask_dispute = ask_dispute
        self.propose = propose
        self.private_thread = private_thread
        if lease is not None:  # the worker's stores write under the same generation
            for store in (memory, skills):
                bind = getattr(store, "bind_lease", None)
                if callable(bind):
                    bind(lease)
        self.queue = CognitionQueue(db, config)
        self.worker_id = str(worker_id or f"{uuid.uuid4().hex[:8]}")
        self.running = False
        self._task: asyncio.Task | None = None
        self._last: dict[str, float] = {}
        self.heartbeat_seconds = max(
            0.05,
            float(
                getattr(self.config.memory.worker, "heartbeat_seconds", 0.0) or 0.0
                or min(5.0, self.queue.lease_floor() / 3.0)
            ),
        )
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

    def start(self: CognitionWorker) -> None:
        if self._task is not None and not self._task.done():
            return
        self.running = True
        self._task = asyncio.create_task(self.run(), name="cognition-worker")
        log.info("cognition worker %s started", self.worker_id)

    async def stop(self: CognitionWorker, *, close_episodes: bool = False) -> None:
        """Stop taking jobs; a job in flight is finished, then the loop exits (ADR-0001 §4.4)."""
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

    async def run(self: CognitionWorker) -> None:
        tick = max(0.1, float(getattr(self.config.memory.worker, "tick_seconds", 1.0)))
        while self.running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("cognition tick failed")
            await asyncio.sleep(tick)

    async def tick(self: CognitionWorker) -> None:
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

    def _generation(self: CognitionWorker) -> int:
        """The lease generation this worker writes under; 0 when no lease is attached."""
        return int(getattr(self.lease, "generation", 0) or 0)

    async def _run_batch(self: CognitionWorker, label: str, jobs: list[Job]) -> None:
        if not jobs:
            return
        # Background work carries its own run id and never a turn id (ADR-0003 §4.2).
        tokens = observability.enter(run_id=f"job:{label}")
        beat = asyncio.create_task(self._heartbeat(jobs), name=f"cognition-beat:{label}")
        try:
            if jobs[0].kind == "capture":
                results = await self._capture(jobs)
            elif jobs[0].kind == "consolidate":
                results = await self._consolidate(jobs)
            else:
                results = {jobs[0].id: await self._handle(jobs[0])}
        except Superseded:
            # The lease moved on mid-batch: the writes were refused, so the job stays claimed
            # and the reaper hands it to the new primary (ADR-0002 §4.4).
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
            await self._stop_heartbeat(beat)
            observability.leave(tokens)
        for job in jobs:
            done = await io.run(
                self.queue.complete, job.id, results.get(job.id, ""), generation=job.generation
            )
            # A refused completion means the generation moved; the job is not done here.
            if done:
                self.counters["jobs_done"] += 1

    async def _heartbeat(self: CognitionWorker, jobs: list[Job]) -> None:
        """Renew the lease and the claim leases while one model call outlasts them."""
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            await io.run(self._renew, jobs)

    def _renew(self: CognitionWorker, jobs: list[Job]) -> None:
        renew = getattr(self.lease, "renew", None)
        if callable(renew):
            renew()
        self.queue.extend_claims(
            [job.id for job in jobs],
            generation=self._generation(),
            seconds=self.queue.lease_floor(),
        )

    @staticmethod
    async def _stop_heartbeat(beat: asyncio.Task) -> None:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat

    async def drain(self: CognitionWorker, *, kind: str = "", limit: int = 1) -> dict[str, str]:
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
        beat = asyncio.create_task(self._heartbeat(jobs), name="cognition-beat:drain")
        try:
            return await self._drain_jobs(jobs)
        finally:
            await self._stop_heartbeat(beat)

    async def _drain_jobs(self: CognitionWorker, jobs: list[Job]) -> dict[str, str]:
        results: dict[str, str] = {}
        skipped: set[str] = set()
        failed = 0
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
                    # One bad job must not abort the rest of the batch.
                    try:
                        results[job.id] = await self._handle(job)
                    except Superseded:
                        log.warning(
                            "cognition job %s superseded: generation moved", job.id
                        )
                        skipped.add(job.id)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        log.warning(
                            "cognition job %s failed: %s: %s",
                            job.id,
                            type(exc).__name__,
                            exc,
                        )
                        await io.run(
                            self.queue.fail,
                            job.id,
                            f"{type(exc).__name__}: {exc}",
                            generation=job.generation,
                        )
                        skipped.add(job.id)
                        failed += 1
        for job in jobs:
            if job.id in skipped:
                continue
            done = await io.run(
                self.queue.complete, job.id, results.get(job.id, ""), generation=job.generation
            )
            if done:
                self.counters["jobs_done"] += 1
        self.counters["jobs_failed"] += failed
        return results

    async def _handle(self: CognitionWorker, job: Job) -> str:
        if job.kind == "reflect":
            return await self._reflect(job)
        if job.kind == "distill":
            return await self._distill(job)
        if job.kind == "backfill":
            return await self._backfill(job)
        if job.kind == "audit":
            return await self._audit(job)
        return f"unhandled {job.kind}"

    async def _backfill(self: CognitionWorker, job: Job) -> str:
        added = await self.memory.backfill_index(
            limit=max(1, int(self.config.reflection.items_per_pass))
        )
        return f"indexed {added}"

    async def _audit(self: CognitionWorker, job: Job) -> str:
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

    def status(self: CognitionWorker) -> dict[str, int]:
        queue = {f"queue.{key}": value for key, value in self.queue.stats().items()}
        return {**self.counters, **queue}
