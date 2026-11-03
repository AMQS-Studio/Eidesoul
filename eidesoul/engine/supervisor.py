"""Supervisor: acquire the primary lease, serve gateways, deactivate to standby."""

from __future__ import annotations

import asyncio
import logging

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
        self.agent.lease = lease

    # ------------------------------------------------------------ lifecycle

    async def run(self) -> None:
        first = True
        while not self.stopping:
            takeover = self.takeover and first
            first = False
            holder = self.lease.acquire(takeover=takeover)
            if holder is not None:
                await self.activate(holder)
                await self.serve()
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

    async def activate(self, holder) -> None:
        self.agent.mode = "primary"
        runtime = self.agent.runtime
        runtime.mode = "primary"
        runtime.lease_host = holder.host
        runtime.lease_pid = holder.pid
        runtime.lease_since = holder.started_at
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

    async def serve(self) -> None:
        tasks = {
            asyncio.create_task(gateway.run(self.agent), name=f"gw-{gateway.name}"): gateway
            for gateway in self.gateways
        }
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
                    continue
                failures += 1
                log.warning("lease renew failed (%d/%d)", failures, RENEW_FAILURES)
                if failures >= RENEW_FAILURES:
                    log.warning("lease lost; freeze")
                    break
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def deactivate(self) -> None:
        self.agent.mode = "standby"
        self.agent.runtime.mode = "standby"
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
        log.info("standby: gateways stopped, lease released")
