"""Shared constants, result types, and row/UUID helpers behind `MemoryStore`.

No SQL lives here: each helper reads through the store's one database handle and
`memory/queries.py` (ADR-0001, ADR-0037 §4.4).
"""

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass

from . import MemoryHit, queries
from .integrity import TRUST_INFERENCE

DAY = 86400.0
MAX_TEXT = 4000
FULL_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
HEX_PREFIX = re.compile(r"^[0-9a-f]{1,32}$", re.IGNORECASE)
STATES = ("pending", "ok", "staged", "failed", "not_worth", "superseded")
SCOPES = ("global", "person", "thread")
AUDIENCES = ("private", "group", "shared")
TRANSITIONS = {
    "pending": {"ok", "failed", "not_worth"},
    "staged": {"ok", "failed", "not_worth"},
    "ok": {"superseded", "staged"},
    "failed": set(),
    "not_worth": set(),
    "superseded": set(),
}


@dataclass(frozen=True)
class MemoryCandidate:
    """One `memory_events` + `memories(pending)` pair from the intake transaction."""

    memory_id: str
    event_id: str
    thread_id: str
    person_id: str
    message_id: str
    text: str
    kind: str = "message"


@dataclass(frozen=True)
class PurgeReport:
    selected: int = 0
    purged: int = 0
    dry_run: bool = False


@dataclass(frozen=True)
class _WriteParams:
    body: str
    scope: str
    kind: str
    person_id: str
    thread_id: str
    audience: str
    route: str
    source: str
    refs: list[str]
    level: int
    owner: str
    subject: str
    confirmed: bool | None
    pinned: bool
    durability: str
    category: str
    task_status: str | None
    due_at: float | None
    asserted_at: float
    dedupe: bool
    external_id: str
    supersedes: str | None
    change: str


@dataclass(frozen=True)
class _Capture:
    person_id: str
    thread_id: str
    audience: str
    source: str
    level: int
    owner: str
    subject: str
    confirmed: bool
    refs: tuple[str, ...]
    category: str
    durability: str
    route: str


def _flat(value: object, limit: int = MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _digest(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


WORD = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    """Content tokens of four characters or more; short words never match.

    The length floor is the whole filter: it is language-neutral, so the engine holds no
    per-language stopword list (ADR-0008 keeps language data out of the core).
    """
    return {token for token in WORD.findall(str(text or "").lower()) if len(token) >= 4}


def _marked_supersede(row: Mapping) -> bool:
    return any(str(item).startswith("supersede:") for item in row.get("refs") or [])


def _hit(row: Mapping) -> MemoryHit:
    level = row.get("level")
    confirmed = row.get("confirmed")
    trust = row.get("trust")
    return MemoryHit(
        id=str(row.get("id") or ""),
        text=str(row.get("text") or ""),
        score=float(row.get("score") or 0.0),
        scope=str(row.get("scope") or "person"),
        person_id=str(row.get("person_id") or ""),
        thread_id=str(row.get("thread_id") or ""),
        created_at=float(row["created_at"]) if row.get("created_at") is not None else None,
        metadata={
            "kind": str(row.get("kind") or "fact"),
            "state": str(row.get("state") or ""),
            "via": str(row.get("via") or ""),
            "pinned": bool(row.get("pinned")),
            "category": str(row.get("category") or "general"),
            "durability": str(row.get("durability") or "normal"),
            "refs": row.get("refs") or [],
            "dispute_state": str(row.get("dispute_state") or "none"),
        },
        level=int(level) if level is not None else 0,
        owner_id=str(row.get("owner_id") or ""),
        subject_id=str(row.get("subject_id") or ""),
        confirmed=bool(confirmed) if confirmed is not None else False,
        vetoed=bool(row.get("vetoed")),
        kind=str(row.get("kind") or "fact"),
        trust=int(trust) if trust is not None else TRUST_INFERENCE,
        state=str(row.get("state") or ""),
        privacy_evidence=tuple(dict(item) for item in row.get("privacy_evidence") or ()),
        privacy_policy=str(row.get("privacy_policy") or ""),
    )


def _base_row(store, memory_id: str) -> dict | None:
    if store.db is None or not str(memory_id or "").strip():
        return None
    resolved = _resolve_id(store, memory_id)
    if not resolved:
        return None
    with store.db.connect() as conn:
        return queries.base_row(conn, resolved)


def _resolve_id(store, memory_id: str) -> str:
    raw = str(memory_id or "").strip().lstrip("#").strip().lower()
    if not raw or store.db is None:
        return ""
    if FULL_UUID.match(raw):
        return raw
    if not HEX_PREFIX.match(raw):
        return ""
    if len(raw) == 32:
        return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
    with store.db.connect() as conn:
        return queries.resolve_id(conn, raw)
