"""Out-of-band alerting: consecutive failures become one admin message and one webhook POST."""


import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import httpx

from ..i18n import t

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger(__name__)

SOURCES = ("database", "llm", "gateway", "plugin", "mcp")
WEBHOOK_TIMEOUT = 5.0

Notifier = Callable[[str], Awaitable[None]]


class AlertManager:
    """One alert per source per cooldown, after `threshold` consecutive failures (ADR-0009)."""

    def __init__(self, config: Config, notify: Notifier | None = None) -> None:
        self.config = config
        self.notify = notify
        self._failures: dict[str, int] = {}
        self._last: dict[str, float] = {}

    def enabled(self, source: str) -> bool:
        """True when alerting is on and this source is listed in `engine.alerts.sources`."""
        alerts = self.config.alerts
        if not alerts.enabled:
            return False
        return bool(getattr(alerts.sources, source, False))

    def failures(self, source: str) -> int:
        """The current consecutive-failure streak; the operator reads it through `/alerts`."""
        return self._failures.get(source, 0)

    def clear(self, source: str) -> None:
        """A success breaks the streak; the next failure starts counting from one."""
        self._failures.pop(source, None)

    async def emit(self, source: str, message: str, *, key: str = "") -> bool:
        """Count one failure and deliver when the threshold and cooldown both allow it."""
        if not self.enabled(source):
            return False
        count = self._failures.get(source, 0) + 1
        self._failures[source] = count
        if count < max(1, int(self.config.alerts.threshold)):
            return False
        now = time.monotonic()
        cooldown = max(0.0, float(self.config.alerts.cooldown_seconds))
        if now - self._last.get(source, float("-inf")) < cooldown:
            return False
        self._last[source] = now
        self._failures[source] = 0
        log.error("alert (%s): %s", source, message)
        await self._deliver(source, message, key)
        return True

    async def _deliver(self, source: str, message: str, key: str) -> None:
        text = t("alerts.fired", self.config.soul.language, source=source, message=message)
        if self.config.alerts.notify_admin and self.notify is not None:
            try:
                await self.notify(text)
            except Exception:  # an alert path never breaks the caller
                log.exception("admin alert delivery failed")
        await self._webhook(source, message, key, text)

    async def _webhook(self, source: str, message: str, key: str, text: str) -> None:
        """POST a structured payload out of band; PostgreSQL may be the thing that is down."""
        env = str(self.config.alerts.webhook_url_env or "").strip()
        url = os.environ.get(env, "") if env else ""
        if not url:
            return
        payload = {
            "text": text,
            "source": source,
            "message": message,
            "key": key,
            "ts": time.time(),
        }
        try:
            async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT) as client:
                await client.post(url, json=payload)
        except Exception:  # the webhook is best-effort by design
            log.warning("alert webhook failed for %s", source, exc_info=True)


_CURRENT: AlertManager | None = None
_PENDING: set[asyncio.Task] = set()  # keep the loop from GC-ing a note


def install(manager: AlertManager | None) -> None:
    """Register the process-wide manager; a fresh boot replaces the previous one."""
    global _CURRENT
    _CURRENT = manager


def current() -> AlertManager | None:
    return _CURRENT


async def emit(source: str, message: str, *, key: str = "") -> bool:
    """Report one failure from any component; False when no manager is installed."""
    manager = _CURRENT
    if manager is None:
        return False
    return await manager.emit(source, message, key=key)


def clear(source: str) -> None:
    """Report a success from a synchronous component."""
    manager = _CURRENT
    if manager is not None:
        manager.clear(source)


def note(source: str, message: str) -> None:
    """Synchronous entry: schedule delivery on the running loop, never block the caller."""
    manager = _CURRENT
    if manager is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # no loop (a CLI check): the log line above is the whole report
        return
    task = loop.create_task(manager.emit(source, message))
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)
