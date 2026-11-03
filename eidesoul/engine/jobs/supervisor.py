"""Supervisor: acquire the primary lease, serve gateways, and shut down in phases.

Neat exit follows ADR-0011 §4.4: `running → draining → noticed → stopped`. Intake stops
first, `engine_control.shutting_down` is raised for every instance, in-flight turns get
`drain_seconds`, one notice per chat is sent inside `notice_seconds`, and only then is the
gate lowered and the lease released (ADR-0017 §4.4: gate down before release).
"""


import asyncio
import contextlib
import logging
import signal
import time
from collections import deque

from sqlalchemy import text

from .. import io
from ..inbound import MESSAGE, Percept
from .automation import AutomationWorker
from .cognition import CognitionWorker
from .lease import Lease

log = logging.getLogger(__name__)

TICK = 5.0
RENEW_FAILURES = 3
# ADR-0013 §11: the resume budget is a rolling minute, not a fixed per-pass cap.
RESUME_WINDOW = 60.0

# --------------------------------------------------------------- turn recovery


def _is_action(row: dict) -> bool:
    """`turns.category` marks a single-action turn: never consolidated away (ADR-0013 §11)."""
    return str(row.get("category") or "chat") == "action"

def expire_consolidated(agent, thread: str, turn_ids: list[str]) -> int:
    """Expire the consolidated tail of one chat and let the away block explain it."""
    expired = 0
    for turn_id in turn_ids:
        try:
            agent.turns.expire(turn_id, "consolidate")
        except Exception:
            log.exception("failed to expire consolidated turn %s", turn_id)
            continue
        expired += 1
    if expired:
        agent.backlog[thread] = agent.backlog.get(thread, 0) + expired
        log.info("recovery: %d turn(s) consolidated chat=%s", expired, thread)
    return expired

def select_resume(agent, rows: list[dict], allowed: int) -> list[dict]:
    """Pace, fair-share, and consolidate one backlog (ADR-0013 §11).

    Selection is round-robin across chats so one busy room cannot starve the others; the
    global minute budget bounds the whole pass and the per-chat quota bounds one room. Under
    `per_message_then_consolidate` the rows past that quota are expired with
    `reason=consolidate`, which the away block turns into one consolidated answer; an
    `action` turn is always selected on its own and never expired.
    """
    cfg = agent.config.turns
    policy = str(cfg.backlog_policy)
    budget = max(0, int(allowed))
    if not budget:
        return []
    groups = backlog_groups(rows)
    if policy == "last_only":
        return [group[-1] for group in groups.values()][:budget]
    if policy == "consolidate":
        return consolidate(agent, groups)[:budget]
    quota = max(1, int(cfg.max_resume_per_chat))
    actions, chats = split_actions(groups)
    taken = dict.fromkeys(groups, 0)
    selected = round_robin(groups, actions, chats, taken, quota, budget)
    if policy == "per_message_then_consolidate":
        for thread in sorted(groups):
            if taken[thread] < quota:
                continue
            expire_consolidated(agent, thread, [str(row["id"]) for row in chats[thread]])
    return selected

def backlog_groups(rows: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for row in sorted(
        rows, key=lambda item: (str(item["thread_id"]), float(item["created_at"]))
    ):
        groups.setdefault(str(row["thread_id"]), []).append(row)
    return groups

def split_actions(
    groups: dict[str, list[dict]],
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    actions = {
        thread: [row for row in group if _is_action(row)]
        for thread, group in groups.items()
    }
    chats = {
        thread: [row for row in group if not _is_action(row)]
        for thread, group in groups.items()
    }
    return actions, chats

def consolidate(agent, groups: dict[str, list[dict]]) -> list[dict]:
    """`consolidate`: the last chat row per room plus every action row, oldest first."""
    chosen: list[dict] = []
    for thread in sorted(groups):
        group = groups[thread]
        chats = [row for row in group if not _is_action(row)]
        chosen.extend(row for row in group if _is_action(row))
        if chats:
            chosen.append(chats[-1])
        expire_consolidated(agent, thread, [str(row["id"]) for row in chats[:-1]])
    chosen.sort(key=lambda row: float(row["created_at"]))
    return chosen

def round_robin(
    groups: dict[str, list[dict]],
    actions: dict[str, list[dict]],
    chats: dict[str, list[dict]],
    taken: dict[str, int],
    quota: int,
    budget: int,
) -> list[dict]:
    """One row per room per lap: an action row first, then a chat row under the quota."""
    selected: list[dict] = []
    while len(selected) < budget:
        progressed = False
        for thread in sorted(groups):
            if len(selected) >= budget:
                break
            if actions[thread]:
                selected.append(actions[thread].pop(0))
                progressed = True
                continue
            if taken[thread] >= quota or not chats[thread]:
                continue
            selected.append(chats[thread].pop(0))
            taken[thread] += 1
            progressed = True
        if not progressed:
            break
    return selected

async def recover(agent) -> int:
    """Recovery pass on the primary (ADR-0011 §4.5, ADR-0013 §8)."""
    cfg = agent.config.turns
    if not cfg.resume or not agent._accepting():
        return 0
    generation = int(getattr(agent.lease, "generation", 0) or 0)
    rows, dead = await io.run(
        agent.turns.claim_expired,
        owner=getattr(agent.lease, "instance_id", "") or "local",
        generation=generation,
        lease_seconds=max(60.0, float(cfg.timeout_seconds) + 30.0),
        limit=max(1, int(agent.config.worker.reaper.batch)),
        max_attempts=int(cfg.max_attempts),
        dead_letter_after=min(
            int(agent.config.worker.retry.dead_letter_after),
            int(agent.config.worker.retry.attempts),
            int(agent.config.llm.retry.worker_attempts),
        ),
        boot_epoch=agent.boot_epoch,
    )
    if dead:
        log.warning("recovery: %d turn(s) abandoned after attempts were exhausted", dead)
    moment = time.time()
    agent._resume_times = deque(
        stamp for stamp in agent._resume_times if moment - stamp < RESUME_WINDOW
    )
    allowed = max(0, int(cfg.max_resume_per_minute) - len(agent._resume_times))
    selected = select_resume(agent, rows, allowed)
    picked = {str(row["id"]) for row in selected}
    unselected = [str(row["id"]) for row in rows if str(row["id"]) not in picked]
    if unselected:
        released = await io.run(agent.turns.release, unselected)
        if released:
            log.info("recovery: %d turn(s) released for a later pass", released)
    resumed = 0
    noticed: set[str] = set()
    for row in selected:
        thread = str(row["thread_id"])
        turn_id = str(row["id"])
        message = await io.run(agent.history.find, thread, turn_id)
        if message is None:
            agent.turns.expire(turn_id, "resume: message missing")
            continue
        age = time.time() - message.when.timestamp()
        if age > float(cfg.stale_after_seconds):
            agent.turns.expire(turn_id, "resume: stale")
            continue
        if age > float(cfg.max_age_seconds) and str(cfg.backlog_policy) not in {
            "consolidate",
            "per_message_then_consolidate",
        }:
            agent.turns.expire(turn_id, "resume: too old")
            continue
        gateway = agent.body_of(thread)
        if gateway is None:
            log.warning("recovery: no body for chat=%s turn=%s", thread, turn_id)
            continue
        downtime = time.time() - float(
            row.get("previous_updated_at") or row.get("updated_at") or 0.0
        )
        if (
            thread not in noticed
            and downtime > float(agent.config.turns.shutdown.resume_notice.after_seconds)
        ):
            noticed.add(thread)
            with contextlib.suppress(Exception):
                await agent._notice(
                    thread,
                    gateway,
                    "turn.notice.resumed",
                    scope_key=f"notice:{thread}:resumed:{agent.boot_epoch}",
                    kind="notice:resumed",
                )
        inbound = Percept(
            source=gateway.name,
            thread=thread,
            actor=str(row.get("person_id") or ""),
            kind=MESSAGE,
            payload=message,
            hints={},
        )
        agent._resume_times.append(time.time())
        agent.spawn(agent.handle(inbound, gateway, claimed=row))
        resumed += 1
    if resumed:
        log.info("recovery resumed %d turn(s)", resumed)
    return resumed

# ---------------------------------------------------------------- supervisor


class Supervisor:
    def __init__(
        self,
        agent,
        lease: Lease,
        gateways: list,
        *,
        takeover: bool = False,
        tick: float = TICK,
    ) -> None:
        self.agent = agent
        self.lease = lease
        self.gateways = gateways
        self.takeover = takeover
        self.tick = float(tick)
        self.stopping = False
        self._first = True
        self.agent.lease = lease
        # ADR-0012 §4.3: every canonical memory write now asks this guard first. Admin/CLI
        # stores stay unbound, so only the live primary writes under a generation.
        bind = getattr(getattr(agent, "memory", None), "bind_guard", None)
        if bind is not None:
            bind(lease.guard)
        self.automations = (
            AutomationWorker(agent) if getattr(agent, "db", None) is not None else None
        )
        self.cognition = CognitionWorker(
            getattr(agent, "db", None),
            agent.config,
            getattr(agent, "memory", None),
            llm=getattr(agent, "llm", None),
            skills=getattr(agent, "skills", None),
            history=getattr(agent, "history", None),
            known_tools=tuple(getattr(getattr(agent, "tools", None), "by_name", {}) or ()),
            ask=getattr(getattr(agent, "privacy", None), "ask_memory", None),
            lease=lease,
            ask_dispute=getattr(getattr(agent, "privacy", None), "ask_dispute", None),
            propose=(
                self.automations.store.propose if self.automations is not None else None
            ),
        )
        self.agent.cognition = self.cognition
        # ADR-0014 §4.3: the message-event sink shares the automations store, so a run and its
        # message commit in the same intake transaction.
        if self.automations is not None and hasattr(agent, "history"):
            agent.history.event_sink = self.automations.store

    # ------------------------------------------------------------ lifecycle

    def _install_signals(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGTERM, self.request_stop)
            loop.add_signal_handler(signal.SIGINT, self.request_stop)
        except (NotImplementedError, RuntimeError, ValueError):  # pragma: no cover - non-POSIX
            log.debug("SIGTERM handler is unavailable on this platform")

    def request_stop(self) -> None:
        if not self.stopping:
            log.info("shutdown requested: draining")
        self.stopping = True

    async def run(self) -> None:
        self._install_signals()
        try:
            while not self.stopping:
                takeover = self.takeover and self._first
                self._first = False
                holder = self.lease.acquire(takeover=takeover)
                if holder is not None:
                    await self.activate(holder)
                    await self.serve()
                    if self.stopping:
                        await self.shutdown()
                    else:
                        await self.deactivate()
                    continue
                current = self.lease.holder()
                if current is not None:
                    log.info(
                        "standby: lease held by %s pid %s since %.0f",
                        current.host,
                        current.pid,
                        current.started_at,
                    )
                else:
                    log.info("standby: lease free but frozen; waiting")
                await asyncio.sleep(self.tick)
        finally:
            if self.agent.mode == "primary":
                await self.deactivate()

    async def activate(self, holder) -> None:
        self.agent.draining = False
        self.agent.mode = "primary"
        runtime = self.agent.runtime
        runtime.mode = "primary"
        runtime.phase = "running"
        runtime.lease_host = holder.host
        runtime.lease_pid = holder.pid
        runtime.lease_since = holder.started_at
        self._set_shutting_down(False)
        if self.agent.ambient is not None:
            self.agent.ambient.start()
        if self.agent.vibe is not None:
            try:
                await self.agent.vibe.start()
            except Exception:
                log.exception("vibe failed to start; continuing without vibe")
        log.info(
            "primary: gateway %s (lease generation %d)",
            ", ".join(gateway.name for gateway in self.gateways),
            holder.generation,
        )
        try:
            await self.agent.recover()
        except Exception:
            log.exception("recovery pass failed; serving anyway")
        self.cognition.start()
        if self.automations is not None:
            self.automations.start()
        runtime.cognition = self.cognition.status()

    async def serve(self) -> None:
        tasks = {
            asyncio.create_task(gateway.run(self.agent), name=f"gw-{gateway.name}"): gateway
            for gateway in self.gateways
        }
        self.agent.start_reaper()
        failures = 0
        try:
            while not self.stopping:
                await asyncio.sleep(self.tick)
                done = [task for task in tasks if task.done()]
                if done:
                    for task in done:
                        gateway = tasks.pop(task)
                        log.info("gateway %s stopped", gateway.name)
                    self.stopping = True
                    break
                if self.lease.frozen():
                    log.info("freeze: stopping gateways until re-activated")
                    break
                if self.lease.renew():
                    failures = 0
                    self.agent.runtime.cognition = self.cognition.status()
                    continue
                failures += 1
                log.warning("lease renew failed (%d/%d)", failures, RENEW_FAILURES)
                if failures >= RENEW_FAILURES:
                    log.warning("lease lost; freeze")
                    break
        finally:
            # Intake stops here; the send path stays open for the shutdown notice.
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self.cognition.stop()
            if self.automations is not None:
                await self.automations.stop()
            await self.agent.stop_reaper()

    async def shutdown(self) -> None:
        """`running → draining → noticed → stopped` within `grace_seconds` (ADR-0011 §4.4)."""
        cfg = self.agent.config.turns.shutdown
        started = asyncio.get_running_loop().time()
        grace = max(0.0, float(cfg.grace_seconds))
        self.agent.begin_drain()
        self._set_shutting_down(True)
        pending: dict[str, str] = {}
        try:
            drain_budget = max(
                0.0, min(float(cfg.drain_seconds), grace - float(cfg.notice_seconds))
            )
            pending = await self.agent.drain(drain_budget)
            remaining = max(
                0.0, grace - (asyncio.get_running_loop().time() - started)
            )
            await self.agent.shutdown_notices(
                pending, min(float(cfg.notice_seconds), remaining)
            )
        except Exception:
            log.exception("shutdown phases failed; stopping anyway")
        finally:
            if self.automations is not None:
                self.automations.interrupt()
                await self.automations.stop()
            await self.cognition.stop(close_episodes=True)
            await self._stop_everything()
            self.agent.runtime.phase = "stopped"
            log.info(
                "stopped: %d unfinished turn(s) noticed, lease released", len(pending)
            )

    async def deactivate(self) -> None:
        """Freeze/lease-loss path: the gate goes down before the lease is released."""
        self.agent.mode = "standby"
        self.agent.runtime.mode = "standby"
        await self._stop_everything()
        log.info("standby: gateways stopped, lease released")

    async def _stop_everything(self) -> None:
        # The gate is lowered first: no send may slip out after the lease moves on.
        self.agent.mode = "standby"
        self.agent.runtime.mode = "standby"
        await self.cognition.stop()
        for gateway in self.gateways:
            try:
                await gateway.stop()
            except Exception:
                log.exception("failed to stop gateway %s", gateway.name)
        if self.agent.ambient is not None:
            try:
                await self.agent.ambient.stop()
            except Exception:
                log.exception("failed to stop ambient")
        if self.agent.vibe is not None:
            try:
                await self.agent.vibe.close()
            except Exception:
                log.exception("failed to save vibe")
        self.lease.release()
        self._set_shutting_down(False)

    def _set_shutting_down(self, value: bool) -> None:
        db = getattr(self.lease, "db", None)
        if db is None:
            return
        try:
            with db.connect() as conn:
                conn.execute(
                    text("UPDATE engine_control SET shutting_down = :value WHERE id = 1"),
                    {"value": bool(value)},
                )
        except Exception:
            log.debug("failed to set shutting_down=%s", value, exc_info=True)
