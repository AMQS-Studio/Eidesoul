"""Deterministic retention and decay policy (ADR-0005 §4).

The policy is pure: it turns `(kind, level, durability, category, task_status, pinned)` into
the row's `expires_at`/`max_lifetime_at`, and a touch into a renewal. `MemoryStore` writes the
result; the nightly purge reads it. No model, no clock of its own: callers pass `now`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

DAY = 86400.0

# ADR-0005 §4.2 base TTL in days. `identity` is a category/Tier-1 pin, not a kind.
BASE_TTL_DAYS: dict[str, dict[str, int]] = {
    "preference": {"0-2": 365, "3": 180, "4": 90},
    "fact": {"0-2": 365, "3": 120, "4": 60},
    "rule": {"0-2": 365, "3": 180, "4": 90},
    "commitment": {"0-2": 365, "3": 180, "4": 90},
    "event": {"0-2": 90, "3": 60, "4": 30},
    "note": {"0-2": 30, "3": 14, "4": 7},
}

# ADR-0005 §5 `max_lifetime_days`.
MAX_LIFETIME_DAYS: dict[str, int] = {"0-2": 3650, "3": 1095, "4": 365}

CORE_CATEGORIES = ("identity", "health", "safety")
OPEN_TASK_STATUSES = (None, "", "open", "pending")


def bucket(level: int) -> str:
    return "0-2" if int(level) <= 2 else str(int(level))


def base_ttl_days(kind: str, level: int) -> int:
    table = BASE_TTL_DAYS.get(str(kind or "fact"), BASE_TTL_DAYS["fact"])
    return table[bucket(level)]


@dataclass(frozen=True)
class RetentionPlan:
    pinned: bool
    expires_at: float | None
    max_lifetime_at: float | None


@dataclass(frozen=True)
class RetentionPolicy:
    """ADR-0005 defaults; a config layer may pass a tuned instance later."""

    enabled: bool = True
    pending_ttl_days: int = 7
    superseded_ttl_days: int = 30
    renew_on_use: bool = True
    renew_min_interval_hours: float = 12.0
    count_only_delivered: bool = True
    max_lifetime_days: dict[str, int] = field(
        default_factory=lambda: dict(MAX_LIFETIME_DAYS)
    )
    pin_categories: tuple[str, ...] = CORE_CATEGORIES
    commitment_pin_when_open: bool = True
    purge_grace_days: float = 1.0
    purge_batch: int = 500

    def unlabeled_expiry(self, now: float | None = None) -> float:
        """`pending`/`failed`/`not_worth`: the ADR-0004 7-day TTL."""
        return (now if now is not None else time.time()) + self.pending_ttl_days * DAY

    def superseded_expiry(self, now: float | None = None) -> float:
        return (now if now is not None else time.time()) + self.superseded_ttl_days * DAY

    def max_lifetime_at(self, level: int, now: float | None = None) -> float:
        days = self.max_lifetime_days.get(bucket(level), MAX_LIFETIME_DAYS[bucket(level)])
        return (now if now is not None else time.time()) + days * DAY

    def plan(
        self,
        *,
        kind: str,
        level: int,
        state: str = "ok",
        durability: str = "normal",
        category: str = "general",
        task_status: str | None = None,
        pinned: bool = False,
        now: float | None = None,
    ) -> RetentionPlan:
        """The deterministic expiry for a labelled row; `staged` follows the `kind` TTL."""
        moment = now if now is not None else time.time()
        lifetime = self.max_lifetime_at(level, moment)
        if not self.enabled:
            return RetentionPlan(pinned=False, expires_at=None, max_lifetime_at=None)
        if state == "staged":
            # Already labelled but held back by the write quota: readable never, TTL by kind.
            return RetentionPlan(
                pinned=False,
                expires_at=min(moment + base_ttl_days(kind, level) * DAY, lifetime),
                max_lifetime_at=lifetime,
            )
        hold = bool(
            pinned
            or str(category or "") in self.pin_categories
            or (
                self.commitment_pin_when_open
                and str(kind or "") == "commitment"
                and task_status in OPEN_TASK_STATUSES
            )
        )
        if str(durability or "") == "core":
            return RetentionPlan(
                pinned=hold,
                expires_at=None if hold else lifetime,
                max_lifetime_at=lifetime,
            )
        if hold:
            return RetentionPlan(pinned=True, expires_at=None, max_lifetime_at=lifetime)
        return RetentionPlan(
            pinned=False,
            expires_at=min(moment + base_ttl_days(kind, level) * DAY, lifetime),
            max_lifetime_at=lifetime,
        )

    def renewed_expiry(
        self,
        *,
        kind: str,
        level: int,
        pinned: bool,
        confirmed: bool | None,
        max_lifetime_at: float | None,
        last_used_at: float,
        now: float,
    ) -> float | None:
        """ADR-0005 §4.3: no renewal for a pin, L4 without a pin/confirmation, or too soon."""
        if not self.enabled or not self.renew_on_use or pinned:
            return None
        if int(level) >= 4 and not (pinned or confirmed):
            return None
        if last_used_at and now - float(last_used_at) < self.renew_min_interval_hours * 3600.0:
            return None
        target = now + base_ttl_days(kind, level) * DAY
        if max_lifetime_at is not None:
            target = min(target, float(max_lifetime_at))
        return target

    def purge_cutoff(self, now: float | None = None, grace_days: float | None = None) -> float:
        moment = now if now is not None else time.time()
        grace = self.purge_grace_days if grace_days is None else float(grace_days)
        return moment - max(0.0, grace) * DAY


def retention_days_from(config, default: int = 90) -> int:
    """`engine.metrics.retention_days` once W6/ADR-0019 lands the config block."""
    metrics = getattr(config, "metrics", None)
    value = getattr(metrics, "retention_days", None)
    try:
        return max(1, int(value if value is not None else default))
    except (TypeError, ValueError):
        return default
