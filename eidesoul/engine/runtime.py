"""Runtime facts for the Self prompt block and /status."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import Paths


def human_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


@dataclass
class RuntimeFacts:
    mode: str = "standby"
    started_at: float = field(default_factory=time.time)
    lease_host: str = ""
    lease_pid: int = 0
    lease_since: float = 0.0
    gateways: tuple[str, ...] = ()
    db_label: str = ""
    memory_status: str = "unavailable"
    pending_approvals: int = 0
    standby_refused: int = 0
    turn_timeout_total: int = 0
    notice_failed: int = 0
    last_notice_error: str = ""
    # ADR-0014 §4.5: body capabilities are counted, never assumed.
    gateway_core_ok: int = 0
    rich_missing_total: int = 0
    directory_missing_total: int = 0
    participants_unknown_total: int = 0
    phase: str = "running"  # running | draining | noticed | stopped (ADR-0011 §4.4)
    paths: Paths | None = None

    @property
    def uptime(self) -> str:
        return human_duration(time.time() - self.started_at)

    def lease_label(self) -> str:
        if not self.lease_host or not self.lease_pid:
            return "-"
        since = human_duration(time.time() - self.lease_since) if self.lease_since else "-"
        return f"{self.lease_host} pid {self.lease_pid} since {since} ago"

    def surface(self) -> str:
        return ", ".join(self.gateways) or "-"
