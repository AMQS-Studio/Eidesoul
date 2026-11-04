"""Efficiency evidence for one episode signature (ADR-0020 §4.6).

The comparison is deterministic and reads only rows the engine already keeps: token cost from
`llm_calls` joined by the episode's turns, tool steps and their failures from `turn_steps`, the
distinct argument shapes a run used, and whether the outcome left an artifact. No model decides
whether a signature is efficient; a model may only read this evidence and pick a ladder action.
"""


import logging
import time
from dataclasses import dataclass, field
from statistics import median

from sqlalchemy import func, select

from ..db import Database
from ..schema import episodes, llm_calls, message_media, messages, turn_steps, turns

log = logging.getLogger(__name__)

DAY = 86400.0


@dataclass(frozen=True)
class Evidence:
    """One signature's measurable record over the window."""

    signature: str = ""
    episode_ids: tuple[str, ...] = ()
    tokens: tuple[int, ...] = ()
    steps: int = 0
    failed_steps: int = 0
    arg_shapes: int = 0
    artifacts: int = 0
    person_ids: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return len(self.episode_ids)

    @property
    def median_tokens(self) -> float:
        return float(median(self.tokens)) if self.tokens else 0.0

    def as_dict(self) -> dict:
        return {
            "signature": self.signature,
            "episodes": self.count,
            "tokens_median": self.median_tokens,
            "steps": self.steps,
            "failed_steps": self.failed_steps,
            "arg_shapes": self.arg_shapes,
            "artifacts": self.artifacts,
        }


@dataclass(frozen=True)
class Window:
    """Closed episodes of one signature inside the window, newest last."""

    signature: str
    rows: tuple[dict, ...] = ()
    person_ids: tuple[str, ...] = field(default_factory=tuple)


def signatures(db: Database, *, window_days: float = 30.0, now: float | None = None) -> list[str]:
    """Distinct non-empty signatures of closed episodes inside the window."""
    if db is None:
        return []
    moment = time.time() if now is None else float(now)
    cutoff = moment - max(1.0, float(window_days)) * DAY
    rows = db.fetch_all(
        select(episodes.c.signature)
        .where(
            episodes.c.status == "closed",
            episodes.c.closed_at.is_not(None),
            episodes.c.closed_at >= cutoff,
            episodes.c.signature != "",
        )
        .distinct()
        .order_by(episodes.c.signature)
    )
    return [str(row["signature"]) for row in rows if str(row["signature"] or "")]


def window(
    db: Database,
    signature: str,
    *,
    window_days: float = 30.0,
    now: float | None = None,
    limit: int = 50,
) -> Window:
    """The closed episodes of one signature, bounded by the window and by `limit`."""
    if db is None or not str(signature or ""):
        return Window(signature=str(signature or ""))
    moment = time.time() if now is None else float(now)
    cutoff = moment - max(1.0, float(window_days)) * DAY
    rows = db.fetch_all(
        select(episodes)
        .where(
            episodes.c.signature == str(signature),
            episodes.c.status == "closed",
            episodes.c.closed_at.is_not(None),
            episodes.c.closed_at >= cutoff,
        )
        .order_by(episodes.c.closed_at.desc())
        .limit(max(1, int(limit)))
    )
    found = tuple(dict(row) for row in rows)
    people = tuple(dict.fromkeys(str(row.get("person_id") or "") for row in found))
    return Window(signature=str(signature), rows=found, person_ids=people)


def evidence(
    db: Database,
    signature: str,
    *,
    window_days: float = 30.0,
    now: float | None = None,
) -> Evidence:
    """Measure one signature: episodes, per-episode tokens, steps, failures, shapes, artifacts."""
    found = window(db, signature, window_days=window_days, now=now)
    if not found.rows:
        return Evidence(signature=str(signature or ""))
    episode_ids: list[str] = []
    tokens: list[int] = []
    steps = 0
    failed = 0
    shapes: set[str] = set()
    artifacts = 0
    for row in found.rows:
        episode_id = str(row.get("id") or "")
        if not episode_id:
            continue
        episode_ids.append(episode_id)
        turn_ids = _turn_ids(row)
        cost = _tokens(db, turn_ids)
        tokens.append(cost)
        step_rows = (
            db.fetch_all(
                select(turn_steps.c.status, turn_steps.c.tool, turn_steps.c.args_hash).where(
                    turn_steps.c.turn_id.in_(turn_ids)
                )
            )
            if turn_ids
            else []
        )
        steps += len(step_rows)
        failed += sum(1 for step in step_rows if str(step.get("status")) == "failed")
        shapes.update(str(step.get("args_hash") or "") for step in step_rows)
        artifacts += _artifacts(db, str(row.get("thread_id") or ""), turn_ids)
    return Evidence(
        signature=str(signature),
        episode_ids=tuple(episode_ids),
        tokens=tuple(tokens),
        steps=steps,
        failed_steps=failed,
        arg_shapes=len(shapes),
        artifacts=artifacts,
        person_ids=found.person_ids,
    )


def _turn_ids(row: dict) -> list[str]:
    return [
        str(item).split(":", 1)[1]
        for item in row.get("refs") or []
        if str(item).startswith("turn:") and ":" in str(item)
    ]


def _tokens(db: Database, turn_ids: list[str]) -> int:
    if not turn_ids:
        return 0
    found = db.fetch_one(
        select(
            func.coalesce(
                func.sum(
                    func.coalesce(llm_calls.c.tokens_in, 0)
                    + func.coalesce(llm_calls.c.tokens_out, 0)
                ),
                0,
            )
        ).where(llm_calls.c.turn_id.in_(turn_ids))
    )
    return int(next(iter(found.values()), 0) or 0) if found else 0


def _artifacts(db: Database, thread_id: str, turn_ids: list[str]) -> int:
    """An outgoing message with media inside the episode's window is an artifact."""
    if not thread_id or not turn_ids:
        return 0
    first = db.fetch_one(
        select(func.min(turns.c.created_at)).where(turns.c.id.in_(turn_ids))
    )
    started = float(next(iter(first.values()), 0) or 0) if first else 0.0
    found = db.fetch_one(
        select(func.count())
        .select_from(messages)
        .where(
            messages.c.thread_id == thread_id,
            messages.c.ts >= started,
            messages.c.outgoing.is_(True),
            select(message_media.c.message_id)
            .where(
                message_media.c.thread_id == messages.c.thread_id,
                message_media.c.message_id == messages.c.msg_id,
            )
            .exists(),
        )
    )
    return int(next(iter(found.values()), 0) or 0) if found else 0


def eligible(
    found: Evidence, *, min_episodes: int = 2, max_failed: int = 0
) -> bool:
    """The guard before a comparison: enough episodes and no failed run in the window."""
    return found.count >= max(1, int(min_episodes)) and int(found.failed_steps) <= int(max_failed)


def median_tokens(values) -> float:
    """`statistics.median`, tolerant of an empty series."""
    numbers = [float(value) for value in values or ()]
    return float(median(numbers)) if numbers else 0.0
