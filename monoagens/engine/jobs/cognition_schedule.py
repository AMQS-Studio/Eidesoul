"""Cognition scheduling: consolidation windows, audit ticks, reflection, and distill admission."""

import logging
from typing import TYPE_CHECKING

from .. import io
from ..memory import episodes as memory_episodes
from .cognition_queue import DAY, SECONDS

if TYPE_CHECKING:
    pass

from typing import Any

from ..config import Config
from ..db import Database
from .cognition_queue import CognitionQueue

log = logging.getLogger(__name__)
class CognitionSchedule:
    """Scheduling ticks that enqueue work; the worker owns the loop."""

    config: Config
    db: Database | None
    memory: Any
    skills: Any
    queue: CognitionQueue
    _last: dict[str, float]

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
