"""Supervisor: acquire the primary lease, serve gateways, and shut down in phases.

Neat exit follows ADR-0011 §4.4: `running → draining → noticed → stopped`. Intake stops
first, `engine_control.shutting_down` is raised for every instance, in-flight turns get
`drain_seconds`, one notice per chat is sent inside `notice_seconds`, and only then is the
gate lowered and the lease released (ADR-0017 §4.4: gate down before release).
"""

from __future__ import annotations

import asyncio
import logging
import signal

from sqlalchemy import text

from .automation import AutomationWorker
from .cognition import CognitionWorker
from .lease import Lease

log = logging.getLogger(__name__)

TICK = 5.0
RENEW_FAILURES = 3


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
        self.cognition = CognitionWorker(
            getattr(agent, "db", None),
            agent.config,
            getattr(agent, "memory", None),
            llm=getattr(agent, "llm", None),
            skills=getattr(agent, "skills", None),
            history=getattr(agent, "history", None),
            language=getattr(getattr(agent, "soul", None), "language", "id"),
            known_tools=tuple(getattr(getattr(agent, "tools", None), "by_name", {}) or ()),
            ask=getattr(agent, "ask_memory", None),
        )
        self.agent.cognition = self.cognition
        self.automations = (
            AutomationWorker(agent) if getattr(agent, "db", None) is not None else None
        )

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
