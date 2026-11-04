"""One clock (ADR-0033 §4.13): every stamp and day bucket reads through `now(tz)`.

`tz` is a required soul value written by `monoagens init`; a missing or unknown zone is
refused with the value named, never replaced by a default zone.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def zone(name: object) -> ZoneInfo:
    """Resolve an IANA zone name; an empty value is an error, not a fallback."""
    text = str(name or "").strip()
    if not text:
        raise ValueError("a timezone is required (soul.timezone); no default zone is chosen")
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone: {text}") from exc


def now(tz: str) -> datetime:
    """Timezone-aware local time in the host's stated zone."""
    return datetime.now(zone(tz))


def today(tz: str) -> date:
    """The current day in the host's zone; the only day boundary in the engine."""
    return now(tz).date()


def stamp(value: float | None, tz: str, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format one epoch stamp in `tz`; `None` means now."""
    moment = now(tz) if value is None else datetime.fromtimestamp(float(value), zone(tz))
    return moment.strftime(fmt)


def day(value: float | None, tz: str) -> str:
    """The `YYYY-MM-DD` day bucket of one epoch stamp in `tz`."""
    return stamp(value, tz, "%Y-%m-%d")
