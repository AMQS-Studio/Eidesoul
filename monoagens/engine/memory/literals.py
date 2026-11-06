"""SQL literal rendering for canonical memory: user text and vectors never reach a query raw."""

from collections.abc import Sequence


def literal(value: object) -> str:
    """User text as data: a NUL byte poisons the driver and never belongs in a query."""
    return str(value or "").replace("\x00", "")


def like_term(term: str) -> str:
    """One `ILIKE` pattern with `%` and `_` escaped, so a literal word stays literal."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def vector(value: Sequence[float] | None) -> str | None:
    """A pgvector literal with six decimals, or None when there is nothing to search."""
    if not value:
        return None
    return "[" + ",".join(f"{float(item):.6f}" for item in value) + "]"
