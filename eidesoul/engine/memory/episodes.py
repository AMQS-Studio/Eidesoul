"""ADR-0020 §4.2 episodes: deterministic open/close from the turn log, then one `distill` job.

An episode is the assessment unit: a run of turns in one thread that may do one job. Nothing
here calls a model. The six closure signals are computed from `turns`, `turn_steps`,
`messages` (artifact), `thread_eras` (topic), `memories` (a closed commitment), and the clean
shutdown. Only the result is stored (`episodes`), and only an episode with tool work or a
correction becomes a `distill` job.
"""


import logging
import re
import time
from dataclasses import dataclass

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db import Database
from ..schema import episodes, memories, message_media, messages, thread_eras, turn_steps, turns

log = logging.getLogger(__name__)

MARKER_MAX_CHARS = 24


@dataclass(frozen=True)
class Episode:
    id: str
    thread_id: str
    person_id: str
    started_at: float
    status: str = "open"
    close_signal: str = ""
    turns: int = 0
    tool_calls: int = 0
    signature: str = ""
    refs: tuple[str, ...] = ()
    distilled_at: float = 0.0
    closed_at: float | None = None


def _episode_row(row) -> Episode:
    return Episode(
        id=str(row["id"]),
        thread_id=str(row["thread_id"] or ""),
        person_id=str(row["person_id"] or ""),
        started_at=float(row["started_at"] or 0.0),
        status=str(row["status"] or "open"),
        close_signal=str(row["close_signal"] or ""),
        turns=int(row["turns"] or 0),
        tool_calls=int(row["tool_calls"] or 0),
        signature=str(row["signature"] or ""),
        refs=tuple(str(item) for item in row["refs"] or []),
        distilled_at=float(row["distilled_at"] or 0.0),
        closed_at=float(row["closed_at"]) if row["closed_at"] else None,
    )


def _insert(db: Database, values: dict) -> None:
    with db.connect() as conn:
        conn.execute(
            pg_insert(episodes)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["id"])
        )


def _patch(db: Database, episode_id: str, values: dict) -> None:
    with db.connect() as conn:
        conn.execute(
            update(episodes).where(episodes.c.id == str(episode_id)).values(**values)
        )


def _open_episode(db: Database, thread_id: str) -> dict | None:
    return db.fetch_one(
        select(episodes)
        .where(episodes.c.thread_id == thread_id, episodes.c.status == "open")
        .order_by(episodes.c.started_at.desc())
        .limit(1)
    )


def _threads(db: Database) -> list[str]:
    rows = db.fetch_all(select(turns.c.thread_id).distinct().order_by(turns.c.thread_id))
    return [str(row["thread_id"]) for row in rows]


def _start(db: Database, thread_id: str) -> dict | None:
    last_closed = db.fetch_one(
        select(episodes.c.closed_at)
        .where(episodes.c.thread_id == thread_id, episodes.c.status == "closed")
        .order_by(episodes.c.closed_at.desc())
        .limit(1)
    )
    after = float(last_closed["closed_at"] or 0.0) if last_closed is not None else 0.0
    first = db.fetch_one(
        select(turns)
        .where(turns.c.thread_id == thread_id, turns.c.created_at > after)
        .order_by(turns.c.created_at)
        .limit(1)
    )
    if first is None:
        return None
    _insert(
        db,
        {
            "id": str(first["id"]),
            "thread_id": thread_id,
            "person_id": str(first["person_id"] or ""),
            "status": "open",
            "close_signal": "",
            "signature": "",
            "turns": 0,
            "tool_calls": 0,
            "refs": [],
            "distilled_at": 0.0,
            "started_at": float(first["created_at"] or time.time()),
            "closed_at": None,
        },
    )
    return _open_episode(db, thread_id)


def _evidence(
    db: Database, thread_id: str, started_at: float, ended_at: float | None = None
) -> tuple[list[dict], list[dict]]:
    """Turn and step evidence of one window; `ended_at` bounds a closed episode."""
    window = [turns.c.thread_id == thread_id, turns.c.created_at >= started_at]
    if ended_at is not None:
        window.append(turns.c.created_at <= float(ended_at))
    rows = db.fetch_all(select(turns).where(*window).order_by(turns.c.created_at))
    ids = [str(row["id"]) for row in rows]
    steps = (
        db.fetch_all(
            select(turn_steps)
            .where(turn_steps.c.turn_id.in_(ids))
            .order_by(turn_steps.c.turn_id, turn_steps.c.step_index)
        )
        if ids
        else []
    )
    return rows, steps


def _target_of(tool: str) -> str:
    """ADR-0020 §4.6: the target comes from the handler's declaration, not a table."""
    try:
        from ..tools import REGISTRY

        spec = REGISTRY.by_name.get(tool)
    except Exception:  # a missing registry must not break a signature
        return tool
    return (spec.target if spec is not None else "") or tool


def _signature(steps: list[dict], thread_id: str = "") -> str:
    """`scope=…;tools=…;targets=…`: the comparable identity of one episode's work.

    Tool names are collapsed to one entry per run (a retry is not a different procedure), and
    the target types come from each handler's declaration, so the signature never carries a
    path or a text.
    """
    names: list[str] = []
    targets: list[str] = []
    for step in steps:
        tool = str(step.get("tool") or "")
        if not tool:
            continue
        if tool not in names:
            names.append(tool)
        target = str(step.get("target") or "") or _target_of(tool)
        if target not in targets:
            targets.append(target)
    scope = "thread" if str(thread_id or "") else "global"
    return f"scope={scope};tools={','.join(names)};targets={','.join(targets)}"


def _correction(steps: list[dict]) -> bool:
    """Error → repair: a failed step followed by an executed one of the same tool."""
    failed = {str(step.get("tool") or "") for step in steps if str(step.get("status")) == "failed"}
    done = {str(step.get("tool") or "") for step in steps if str(step.get("status")) == "executed"}
    return bool(failed & done)


def _short_marker(db: Database, thread_id: str, after: float) -> bool:
    """A short user turn after tool work reads as a completion; no word list is consulted."""
    row = db.fetch_one(
        select(messages.c.text)
        .where(
            messages.c.thread_id == thread_id,
            messages.c.ts >= after,
            messages.c.outgoing.is_(False),
            messages.c.deleted.is_(False),
        )
        .order_by(messages.c.ts.desc())
        .limit(1)
    )
    if row is None:
        return False
    body = " ".join(str(row["text"] or "").split())
    return 0 < len(body) <= MARKER_MAX_CHARS and "?" not in body


def _artifact(db: Database, thread_id: str, after: float) -> float | None:
    row = db.fetch_one(
        select(func.max(messages.c.ts).label("last")).where(
            messages.c.thread_id == thread_id,
            messages.c.ts >= after,
            messages.c.outgoing.is_(True),
            exists(
                select(message_media.c.message_id).where(
                    message_media.c.thread_id == messages.c.thread_id,
                    message_media.c.message_id == messages.c.msg_id,
                    message_media.c.kind == "media",
                )
            ),
        )
    )
    value = row.get("last") if row else None
    return float(value) if value else None


def _topic_changed(db: Database, thread_id: str, after: float) -> bool:
    found = db.fetch_one(
        select(thread_eras.c.id)
        .where(thread_eras.c.thread_id == thread_id, thread_eras.c.started_at > after)
        .limit(1)
    )
    return found is not None


def _commitment_closed(db: Database, after: float) -> bool:
    found = db.fetch_one(
        select(memories.c.id)
        .where(
            memories.c.kind == "commitment",
            memories.c.task_status == "done",
            memories.c.state == "ok",
            memories.c.updated_at > after,
        )
        .limit(1)
    )
    return found is not None


def _signal(
    db: Database,
    episode: Episode,
    *,
    last_turn: float,
    now: float,
    idle_minutes: float,
    idle_after_send_minutes: float,
    tool_calls: int,
) -> str:
    sent = _artifact(db, episode.thread_id, episode.started_at)
    if sent is not None and now - sent >= idle_after_send_minutes * 60.0:
        return "artifact"
    if tool_calls and _short_marker(db, episode.thread_id, last_turn):
        return "marker"
    if now - last_turn >= idle_minutes * 60.0:
        return "idle"
    if _topic_changed(db, episode.thread_id, episode.started_at):
        return "topic"
    if _commitment_closed(db, episode.started_at):
        return "commitment"
    return ""


def scan(
    db: Database,
    *,
    now: float | None = None,
    idle_minutes: float = 10.0,
    idle_after_send_minutes: float = 3.0,
) -> list[str]:
    """Open/refresh/close episodes; returns the ids that just closed (idempotent per scan)."""
    if db is None:
        return []
    moment = time.time() if now is None else float(now)
    closed: list[str] = []
    for thread_id in _threads(db):
        row = _open_episode(db, thread_id) or _start(db, thread_id)
        if row is None:
            continue
        episode = _episode_row(row)
        turn_rows, steps = _evidence(db, thread_id, episode.started_at)
        if not turn_rows:
            continue
        tool_calls = sum(1 for step in steps if str(step.get("status") or "") == "executed")
        refs = [f"turn:{turn['id']}" for turn in turn_rows]
        refs.extend(f"tool:{step.get('tool')}" for step in steps if str(step.get("tool") or ""))
        _patch(
            db,
            episode.id,
            {
                "turns": len(turn_rows),
                "tool_calls": tool_calls,
                "signature": _signature(steps, thread_id),
                "refs": refs,
            },
        )
        last_turn = max(float(turn["created_at"] or 0.0) for turn in turn_rows)
        signal = _signal(
            db,
            episode,
            last_turn=last_turn,
            now=moment,
            idle_minutes=float(idle_minutes),
            idle_after_send_minutes=float(idle_after_send_minutes),
            tool_calls=tool_calls,
        )
        if not signal:
            continue
        _patch(
            db,
            episode.id,
            {"status": "closed", "close_signal": signal, "closed_at": moment},
        )
        closed.append(episode.id)
    return closed


def close_all(db: Database, *, now: float | None = None, signal: str = "shutdown") -> list[str]:
    """Clean shutdown closes open episodes instead of discarding them (signal 5)."""
    if db is None:
        return []
    moment = time.time() if now is None else float(now)
    rows = db.fetch_all(select(episodes).where(episodes.c.status == "open"))
    closed: list[str] = []
    for row in rows:
        _patch(
            db,
            str(row["id"]),
            {"status": "closed", "close_signal": signal, "closed_at": moment},
        )
        closed.append(str(row["id"]))
    return closed


def admission(episode: Episode, steps: list[dict]) -> tuple[bool, str]:
    """ADR-0020 §4.3: ordinary chat is never assessed; tool work or a repair is."""
    if episode.tool_calls >= 1:
        return True, "tool_work"
    if _correction(steps):
        return True, "correction"
    return False, "no tool work"


def slug(signature: str, limit: int = 60) -> str:
    """A readable procedure name: the tool chain of a structured signature, else the text."""
    raw = str(signature or "")
    if "tools=" in raw:
        raw = raw.split("tools=", 1)[1].split(";", 1)[0] or raw
    body = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    if not body:
        body = "episode"
    return body[:limit].strip("-") or "episode"


def steps_of(db: Database, episode_id: str) -> list[dict]:
    """The tool evidence of one episode, ordered, for the distill assessment."""
    episode = db.fetch_one(select(episodes).where(episodes.c.id == str(episode_id)))
    if episode is None:
        return []
    closed = episode.get("closed_at")
    _, steps = _evidence(
        db,
        str(episode["thread_id"]),
        float(episode["started_at"]),
        float(closed) if closed else None,
    )
    return steps


def get(db: Database, episode_id: str) -> Episode | None:
    row = db.fetch_one(select(episodes).where(episodes.c.id == str(episode_id)))
    return _episode_row(row) if row is not None else None


def mark_distilled(db: Database, episode_id: str, *, now: float | None = None) -> None:
    """One distill per episode: the timestamp is the idempotency guard after the job key."""
    _patch(
        db,
        episode_id,
        {"distilled_at": time.time() if now is None else float(now)},
    )
