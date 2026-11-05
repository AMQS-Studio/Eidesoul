"""Per-thread serialization and the one place a turn is interrupted (ADR-0013 §7.1).

The coordinator owns the three maps the `Agent` used to hold: the per-thread lock, the active
turn task (plus the pending task still waiting for the lock), and the body that owns each
thread. `interrupt_thread` is the single implementation behind `/stop` and a barge-in: it
cancels the active task, awaits it, checkpoints the turn as `interrupted` with the reason, and
asks the body to stop whatever it is still streaming through the structural `abort()` helper.
The coordinator never imports a gateway package; it only sees the `GatewayCore` instance.
"""


import asyncio
import contextlib
import logging
from collections import OrderedDict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from .. import io
from ..inbound import GatewayCore, abort
from .runtime import TurnRuntime

log = logging.getLogger(__name__)


class TurnCoordinator:
    """One conversation is one sequence; a different thread runs in parallel."""

    def __init__(self, runtime: TurnRuntime) -> None:
        self.runtime = runtime
        self._locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._waiters: dict[str, int] = {}
        self._active: dict[str, asyncio.Task] = {}
        self._pending: dict[str, asyncio.Task] = {}
        self._gateways: dict[str, GatewayCore] = {}
        self._reasons: dict[str, str] = {}

    # ------------------------------------------------------------- locks

    @asynccontextmanager
    async def thread_lock(self, thread_id: str) -> AsyncGenerator[None]:
        """Hold one thread's turn slot; the lock is evicted once nobody waits on it."""
        lock = self._locks.get(thread_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[thread_id] = lock
        self._locks.move_to_end(thread_id)
        self._waiters[thread_id] = self._waiters.get(thread_id, 0) + 1
        self._evict_locks()
        try:
            async with lock:
                yield
        finally:
            waiting = self._waiters.get(thread_id, 1) - 1
            if waiting > 0:
                self._waiters[thread_id] = waiting
            else:
                self._waiters.pop(thread_id, None)
            self._evict_locks()

    def _evict_locks(self) -> None:
        limit = max(1, int(self.runtime.config.concurrency.max_chat_locks))
        while len(self._locks) > limit:
            for key, lock in self._locks.items():
                if not lock.locked() and not self._waiters.get(key):
                    del self._locks[key]
                    break
            else:
                return

    # ------------------------------------------------------------ tasks

    def register(
        self,
        thread_id: str,
        task: asyncio.Task,
        gateway: GatewayCore,
        *,
        active: bool,
    ) -> None:
        """Record the task that owns a thread; `active` once it holds the turn slot."""
        bucket = self._active if active else self._pending
        bucket[thread_id] = task
        self._gateways[thread_id] = gateway

    def release(self, thread_id: str, task: asyncio.Task, *, active: bool) -> None:
        """Forget a task, and the thread's body once no task claims it any more."""
        bucket = self._active if active else self._pending
        if bucket.get(thread_id) is task:
            bucket.pop(thread_id, None)
        if self._active.get(thread_id) is None and self._pending.get(thread_id) is None:
            self._gateways.pop(thread_id, None)

    def active_task(self, thread_id: str) -> asyncio.Task | None:
        """The task running in this thread, or the one still waiting for its lock."""
        return self._active.get(thread_id) or self._pending.get(thread_id)

    def gateway_of(self, thread_id: str) -> GatewayCore | None:
        """The body that owns an in-flight thread, or `None` when nothing is running."""
        return self._gateways.get(thread_id)

    @property
    def gateway_map(self) -> dict[str, GatewayCore]:
        """The live body map; callers read it, the coordinator writes it."""
        return self._gateways

    def tasks(self) -> dict[str, asyncio.Task]:
        """A snapshot of every in-flight task keyed by thread (pending first)."""
        found = dict(self._pending)
        found.update(self._active)
        return found

    def gateways(self) -> dict[str, GatewayCore]:
        return dict(self._gateways)

    # ----------------------------------------------------------- reasons

    def note(self, turn_id: str, reason: str) -> None:
        """Record why a running turn is being cancelled, before the task observes it."""
        self._reasons[str(turn_id)] = str(reason)

    def reason(self, turn_id: str) -> str:
        return self._reasons.get(str(turn_id), "cancelled")

    def forget(self, turn_id: str) -> None:
        self._reasons.pop(str(turn_id), None)

    # --------------------------------------------------------- lifecycle

    async def interrupt(self, turn_id: str, reason: str, *, force: bool = True) -> None:
        """Checkpoint one turn as `interrupted`; a delivered turn is never overwritten."""
        try:
            await io.run(self.runtime.turns.interrupt, turn_id, reason, force=force)
        except Exception:
            log.exception("failed to mark turn %s interrupted (%s)", turn_id, reason)

    async def finish(self, turn_id: str, *, planned: int = 0) -> None:
        """Checkpoint one turn as delivered with its chunk plan."""
        try:
            await io.run(self.runtime.turns.finish, turn_id, planned=planned)
        except Exception:
            log.exception("failed to mark turn %s delivered", turn_id)

    async def interrupt_thread(self, thread_id: str, reason: str = "barge_in") -> bool:
        """Cancel the turn running in one thread and stop the body's stream for it.

        Returns `False` when the thread is idle. The checkpoint is forced: a cancelled turn
        must be visible as `interrupted(reason)` even when its own handler could not run.
        """
        task = self.active_task(thread_id)
        if task is None or task.done():
            return False
        turn_id = str(getattr(task, "_monoagens_turn_id", "") or "")
        gateway = self.gateway_of(thread_id)
        if turn_id:
            self.note(turn_id, reason)
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        if turn_id:
            await self.interrupt(turn_id, reason)
        if gateway is not None:
            with contextlib.suppress(Exception):
                await abort(gateway, thread_id)
        return True
