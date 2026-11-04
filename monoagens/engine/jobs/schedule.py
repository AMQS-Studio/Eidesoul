"""Schedule and quiet-hours arithmetic for automations (ADR-0014 §4.3).

Pure functions: no store, no model, no clock of their own. `automation_manifest.py`,
`automation_store.py`, and `automation_worker.py` keep the manifest, the SQL, and the worker;
this module answers "when is the next occurrence" and "is the owner quiet right now"
(ADR-0031 §4.5: a module over the cap may be split).
"""

import hashlib
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import parse_quiet_hours  # noqa: F401 — re-exported for the manifest reader

DAY = 86400.0
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class ManifestError(ValueError):
    """A request that cannot be expressed honestly is refused, not stored half-way."""


def occurrence_key(automation_id: int | str, scheduled_at: float) -> str:
    """`hash(automation_id, scheduled_at_utc)`; stable across restarts and takeovers."""
    payload = f"{int(automation_id)}:{int(float(scheduled_at))}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def zone(name: object) -> ZoneInfo:
    """A manifest must state its zone (ADR-0033 §4.4); there is no default."""
    text = str(name or "").strip()
    if not text:
        raise ManifestError("event.tz is required; no default zone is chosen")
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ManifestError(f"unknown timezone: {text}") from exc


def _minutes(value: str) -> int:
    hour, _, minute = str(value).partition(":")
    return int(hour) * 60 + int(minute)


def _at_list(value: object) -> list[int]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)) or not value:
        raise ManifestError("schedule 'at' must be a non-empty list of HH:MM")
    found: list[int] = []
    for item in value:
        text = str(item or "").strip()
        try:
            hour, _, minute = text.partition(":")
            if len(text) != 5 or _ != ":":
                raise ValueError
            parsed = (int(hour), int(minute))
        except ValueError as exc:
            raise ManifestError(f"invalid time: {text}") from exc
        if not (0 <= parsed[0] <= 23 and 0 <= parsed[1] <= 59):
            raise ManifestError(f"invalid time: {text}")
        found.append(parsed[0] * 60 + parsed[1])
    return sorted(set(found))


def _days(value: object) -> tuple[str, int]:
    if value in (None, "", "daily"):
        return "daily", 1
    if isinstance(value, str):
        text = value.strip().lower()
        if text.startswith("every:"):
            try:
                step = int(text.split(":", 1)[1])
            except ValueError as exc:
                raise ManifestError("days 'every:N' needs a number") from exc
            if step < 1:
                raise ManifestError("days 'every:N' needs N >= 1")
            return "every", step
        names = [part.strip()[:3].lower() for part in text.split(",") if part.strip()]
        if names and all(name in WEEKDAYS for name in names):
            return "set", 0
        raise ManifestError("days must be 'daily', a day list, or 'every:N'")
    if isinstance(value, (list, tuple)):
        names = [str(item or "").strip()[:3].lower() for item in value]
        if names and all(name in WEEKDAYS for name in names):
            return "set", 0
    raise ManifestError("days must be 'daily', a day list, or 'every:N'")


def _day_names(value: object) -> set[str]:
    if isinstance(value, str):
        return {part.strip()[:3].lower() for part in value.split(",") if part.strip()}
    if isinstance(value, (list, tuple)):
        return {str(item or "").strip()[:3].lower() for item in value}
    return set()


def _date(value: object, field: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ManifestError(f"{field} must be YYYY-MM-DD") from exc


def in_quiet(epoch: float, tz_name: object, window: tuple[int, int] | None) -> bool:
    if window is None:
        return False
    moment = datetime.fromtimestamp(float(epoch), zone(tz_name))
    minute = moment.hour * 60 + moment.minute
    start, end = window
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def quiet_end(epoch: float, tz_name: object, window: tuple[int, int] | None) -> float:
    """First moment outside the quiet window, in the source timezone."""
    if window is None:
        return float(epoch)
    zoneinfo = zone(tz_name)
    moment = datetime.fromtimestamp(float(epoch), zoneinfo)
    _start, end = window
    minutes = end
    target = moment.replace(
        hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0
    )
    while target <= moment:
        target += timedelta(days=1)
    return target.timestamp()


def next_after(
    event: dict, after: float, *, anchor: float
) -> float | None:
    """The first occurrence strictly after `after`, or None when the schedule is finished."""
    zoneinfo = zone(event.get("tz"))
    starts = _date(event.get("start"), "start")
    until = _date(event.get("until"), "until")
    if event.get("every"):
        step = max(60, int(event["every"]))
        base = (
            datetime.combine(starts, clock_time(0, 0), tzinfo=zoneinfo).timestamp()
            if starts is not None
            else float(anchor)
        )
        if after < base:
            return base
        return base + (int((after - base) // step) + 1) * step
    times = sorted({_minutes(str(value)) for value in event.get("at") or []})
    if not times:
        return None
    mode, every = _days(event.get("days"))
    names = _day_names(event.get("days"))
    anchor_date = datetime.fromtimestamp(float(anchor), zoneinfo).date()
    moment = datetime.fromtimestamp(float(after), zoneinfo)
    for offset in range(0, 400):
        current = moment.date() + timedelta(days=offset)
        if starts is not None and current < starts:
            continue
        if until is not None and current > until:
            return None
        if mode == "set" and WEEKDAYS[current.weekday()] not in names:
            continue
        if mode == "every" and (current - anchor_date).days % max(1, every) != 0:
            continue
        for minute in times:
            candidate = datetime.combine(
                current, clock_time(minute // 60, minute % 60), tzinfo=zoneinfo
            ).timestamp()
            if candidate > after:
                return candidate
    return None


def _first_occurrence(event: dict, now: float, created_at: float) -> float | None:
    return next_after(event, float(now) - 1.0, anchor=created_at)
