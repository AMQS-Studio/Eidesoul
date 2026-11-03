"""`MemoryStore`: the one canonical write port and the readable port of ADR-0001/0004/0005/0008.

Canonical rows live in Postgres (`memories`). Reads for prompts, recall, tools, and `/memori`
go through the `memories_readable` view only; the worker/admin uses the base table. Mem0 is an
extractor and an index behind this port: it may fail without losing a fact, and the index can
be rebuilt from canonical.
"""


import hashlib
import inspect
import logging
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .. import io
from ..config import PrivacyConfig
from ..db import Database
from ..guard import SENSITIVE, is_crisis, is_injection
from ..jobs.lease import Superseded
from ..schema import (
    memories,
    memories_readable,
    memory_index,
    memory_tombstones,
)
from . import MemoryContext, MemoryHit, gate, queries
from . import context as memory_context
from .integrity import (
    RULE_MIN_CORROBORATION_DAYS,
    TRUST_INFERENCE,
    TRUST_UNLABELED,
    WriteQuota,
    quarantine_kind,
    supersede_verdict,
    trust_for,
    via_of,
)
from .ledger import ActionLedger
from .privacy import PrivacyPolicy
from .retention import RetentionPlan, RetentionPolicy, retention_days_from

log = logging.getLogger(__name__)

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
        trust=int(row.get("trust") if row.get("trust") is not None else TRUST_INFERENCE),
        state=str(row.get("state") or ""),
    )


class MemoryStore:
    """The canonical memory port (ADR-0001); Mem0 is attached as extractor + index."""

    def __init__(
        self,
        db: Database | None,
        config=None,
        *,
        index=None,
        extractor=None,
        policy: RetentionPolicy | None = None,
        quota: WriteQuota | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self._index = index
        self._extractor = extractor
        self._policy = policy or RetentionPolicy()
        self._quota = quota or WriteQuota()
        self._limits = getattr(config, "memory", None)
        self._guard = None
        self.available = db is not None
        self.reason = "" if db is not None else "no database"
        self.label = "postgres"

    # ---------------------------------------------------------------- wiring

    def configure_limits(self, memory) -> None:
        self._limits = memory

    def bind_guard(self, guard) -> None:
        """ADR-0012 §4.3: the lease guard fences every canonical mutation once bound."""
        self._guard = guard

    def _fence(self, conn, operation: str) -> None:
        """Refuse the write when the live lease generation moved on (ADR-0012 §4.3).

        The guard records the refusal in the action ledger; the generation is then read on the
        writer's own connection, so the check and the write come from one snapshot. A store with
        no guard (admin CLI, tests, tools that never claim the lease) writes unconditionally.
        """
        if self._guard is None:
            return
        self._guard.require(operation)
        live = conn.execute(text("SELECT generation FROM engine_lease WHERE id = 1")).scalar()
        if not isinstance(live, int) or live != int(self._guard.generation):
            raise Superseded(
                f"lease generation moved on: held {self._guard.generation}, live {live}"
            )

    def bind(self, index=None, extractor=None, *, label: str = "") -> None:
        self._index = index
        self._extractor = extractor
        if label:
            self.label = label

    @property
    def _has_index(self) -> bool:
        return self._index is not None and bool(getattr(self._index, "available", False))

    @property
    def index(self):
        return self._index

    async def close(self) -> None:
        if self._index is not None and hasattr(self._index, "close"):
            await self._index.close()

    # ------------------------------------------------------------ policies

    @property
    def policy(self) -> RetentionPolicy:
        return self._policy

    def _audience(self, scope: str, audience: str) -> str:
        if audience in AUDIENCES:
            return audience
        return {"global": "shared", "person": "private", "thread": "group"}[scope]

    def _quota_counts(self, *, person_id: str, thread_id: str, now: float) -> tuple[int, int, int]:
        if self.db is None:
            return (0, 0, 0)
        with self.db.connect() as conn:
            return queries.quota_counts(
                conn, person_id=person_id, thread_id=thread_id, now=now
            )

    def _state_after_quota(
        self,
        *,
        state: str,
        kind: str,
        person_id: str,
        thread_id: str,
        category: str,
        via: str,
        pinned: bool,
        now: float,
    ) -> str:
        if state != "ok" or self.db is None:
            return state
        if self._quota.exempt(category=category, kind=kind, via=via, pinned=pinned):
            return state
        person_hour, person_day, thread_hour = self._quota_counts(
            person_id=person_id, thread_id=thread_id, now=now
        )
        if self._quota.overflow(
            person_hour=person_hour, person_day=person_day, thread_hour=thread_hour
        ):
            return "staged"
        return state

    # ------------------------------------------------------------ canonical write

    async def add(
        self,
        text: str,
        *,
        scope: str = "person",
        kind: str = "fact",
        person_id: str = "",
        thread_id: str = "",
        audience: str = "",
        source: str = "",
        metadata: dict | None = None,
        supersedes: str | None = None,
        infer: bool = False,
        level: int = 2,
        owner_id: str = "",
        subject_id: str = "",
        confirmed: bool = True,
    ) -> list[str]:
        """Conscious write (tool/command): canonical first, then the derived index."""
        old = (
            await io.run(self._superseded_row_sync, supersedes)
            if supersedes
            else None
        )
        memory_id = await io.run(
            self.write,
            text,
            scope=scope,
            kind=kind,
            person_id=person_id,
            thread_id=thread_id,
            audience=audience,
            via=via_of(source or "command"),
            source=source,
            level=level,
            owner_id=owner_id,
            subject_id=subject_id,
            confirmed=confirmed,
            supersedes=supersedes,
            asserted_at=None,
        )
        if old is not None:
            await self._drop_index(old)
        await self._index_row(memory_id)
        return [memory_id]

    def _superseded_row_sync(self, memory_id: str) -> dict | None:
        return self._base_row(self._resolve_id(memory_id))

    def _validated(
        self,
        *,
        text: str,
        level: int | None,
        scope: str,
        kind: str,
        source: str,
        via: str,
        person_id: str,
        owner_id: str,
        subject_id: str,
        corroboration_days: int,
    ) -> tuple[str, int, str, str, str, str]:
        body = _flat(text)
        if not body:
            raise ValueError("memory text is empty")
        if level is None:
            raise ValueError("a canonical ok/staged row needs a privacy level")
        number = int(level)
        if number > 4:
            raise ValueError("privacy level 5/credentials are never stored")
        if number < 0:
            raise ValueError("privacy level must be 0-4")
        if SENSITIVE.search(body):
            raise ValueError("text contains a secret/credential")
        if is_crisis(body):
            raise ValueError("crisis text is never stored in memory")
        if scope not in SCOPES:
            raise ValueError(f"unknown scope: {scope}")
        route = via_of(source or via, fallback=via)
        owner = str(owner_id or person_id or "").strip()
        subject = str(subject_id or owner or "").strip()
        wanted = quarantine_kind(
            kind,
            via=route,
            trust=trust_for(route, owner, subject),
            corroboration_days=corroboration_days,
        )
        return body, number, route, owner, subject, wanted

    def _prepare(
        self,
        *,
        body: str,
        kind: str,
        scope: str,
        person_id: str,
        thread_id: str,
        audience: str,
        route: str,
        source: str,
        refs: Sequence[str],
        level: int,
        owner: str,
        subject: str,
        confirmed: bool | None,
        pinned: bool,
        durability: str,
        category: str,
        task_status: str | None,
        due_at: float | None,
        asserted_at: float | None,
        dedupe: bool,
        external_id: str,
        supersedes: str | None,
        change: str,
        state: str,
    ) -> tuple[_WriteParams, str, RetentionPlan, float]:
        if state not in {"ok", "staged"}:
            raise ValueError(
                "write creates only ok or staged rows; use intake/transition otherwise"
            )
        now = time.time()
        is_pinned = bool(pinned) or str(category) in self._policy.pin_categories
        if state == "ok":
            state = self._state_after_quota(
                state=state,
                kind=kind,
                person_id=person_id,
                thread_id=thread_id,
                category=category,
                via=route,
                pinned=is_pinned,
                now=now,
            )
        plan = self._policy.plan(
            kind=kind,
            level=level,
            state=state,
            durability=durability,
            category=category,
            task_status=task_status,
            pinned=is_pinned,
            now=now,
        )
        if confirmed is None:
            confirmed = True if state == "ok" else None
        params = _WriteParams(
            body=body,
            scope=scope,
            kind=kind,
            person_id=str(person_id or ""),
            thread_id=str(thread_id or ""),
            audience=self._audience(scope, audience),
            route=route,
            source=str(source or route),
            refs=[_flat(item, 200) for item in refs if str(item or "").strip()],
            level=level,
            owner=owner,
            subject=subject,
            confirmed=confirmed,
            pinned=plan.pinned,
            durability=str(durability or "normal"),
            category=str(category or "general"),
            task_status=task_status,
            due_at=due_at,
            asserted_at=float(asserted_at if asserted_at is not None else now),
            dedupe=dedupe,
            external_id=str(external_id or ""),
            supersedes=supersedes,
            change=change,
        )
        return params, state, plan, now

    def _values(
        self, params: _WriteParams, state: str, plan: RetentionPlan, now: float
    ) -> dict:
        memory_id = str(uuid.uuid4())
        return {
            "id": memory_id,
            "scope": params.scope,
            "person_id": params.person_id,
            "thread_id": params.thread_id,
            "kind": params.kind,
            "text": params.body,
            "state": state,
            "level": params.level,
            "confirmed": params.confirmed,
            "owner_id": params.owner,
            "subject_id": params.subject,
            "via": params.route,
            "refs": params.refs,
            "trust": trust_for(params.route, params.owner, params.subject),
            "audience": params.audience,
            "source": params.source,
            "vetoed": False,
            "vetoed_by": "",
            "vetoed_at": 0.0,
            "pinned": plan.pinned,
            "durability": params.durability,
            "category": params.category,
            "use_count": 0,
            "distinct_use_days": 0,
            "last_used_at": 0.0,
            "max_lifetime_at": plan.max_lifetime_at,
            "expires_at": plan.expires_at,
            "supersedes": None,
            "superseded_by": None,
            "due_at": params.due_at,
            "task_status": params.task_status,
            "dispute_state": "none",
            "disputed_with": None,
            "dispute_reason": "",
            "disputed_at": 0.0,
            "asserted_at": params.asserted_at,
            "created_at": now,
            "updated_at": now,
        }

    def _existing(self, conn, params: _WriteParams) -> str:
        found = conn.execute(
            select(memories.c.id)
            .where(
                memories.c.text == params.body,
                memories.c.scope == params.scope,
                memories.c.person_id == params.person_id,
                memories.c.thread_id == params.thread_id,
                memories.c.state == "ok",
                memories.c.vetoed.is_(False),
            )
            .limit(1)
        ).scalar()
        return str(found) if found is not None else ""

    def _superseded(self, conn, params: _WriteParams) -> dict | None:
        if not params.supersedes:
            return None
        resolved = self._resolve_id(params.supersedes)
        if not resolved:
            raise ValueError("memory to supersede was not found")
        old = (
            conn.execute(
                select(memories)
                .where(memories.c.id == resolved)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if old is None:
            raise ValueError("memory to supersede was not found")
        verdict = supersede_verdict(
            old,
            owner_id=params.owner,
            subject_id=params.subject,
            scope=params.scope,
            kind=params.kind,
            thread_id=params.thread_id,
            change=params.change,
            task_status=params.task_status,
        )
        if not verdict.allowed:
            raise ValueError(verdict.reason or "supersede refused")
        return dict(old)

    def write(
        self,
        text: str,
        *,
        scope: str = "person",
        kind: str = "fact",
        person_id: str = "",
        thread_id: str = "",
        audience: str = "",
        via: str = "turn",
        source: str = "",
        refs: Sequence[str] = (),
        level: int | None = 2,
        owner_id: str = "",
        subject_id: str = "",
        confirmed: bool | None = None,
        pinned: bool = False,
        durability: str = "normal",
        category: str = "general",
        task_status: str | None = None,
        due_at: float | None = None,
        supersedes: str | None = None,
        change: str = "contradiction",
        asserted_at: float | None = None,
        state: str = "ok",
        corroboration_days: int = 0,
        external_id: str = "",
        dedupe: bool = False,
    ) -> str:
        """Insert one labelled canonical row; raises before touching anything forbidden."""
        if self.db is None:
            raise ValueError("memory store has no database")
        body, number, route, owner, subject, wanted = self._validated(
            text=text,
            level=level,
            scope=scope,
            kind=kind,
            source=source,
            via=via,
            person_id=person_id,
            owner_id=owner_id,
            subject_id=subject_id,
            corroboration_days=corroboration_days,
        )
        params, state, plan, now = self._prepare(
            body=body,
            kind=wanted,
            scope=scope,
            person_id=person_id,
            thread_id=thread_id,
            audience=audience,
            route=route,
            source=source,
            refs=refs,
            level=number,
            owner=owner,
            subject=subject,
            confirmed=confirmed,
            pinned=pinned,
            durability=durability,
            category=category,
            task_status=task_status,
            due_at=due_at,
            asserted_at=asserted_at,
            dedupe=dedupe,
            external_id=external_id,
            supersedes=supersedes,
            change=change,
            state=state,
        )
        values = self._values(params, state, plan, now)
        memory_id = str(values["id"])
        with self.db.connect() as conn:
            self._fence(conn, "write")
            if params.dedupe:
                found = self._existing(conn, params)
                if found:
                    if params.external_id:
                        self._link(conn, found, params.external_id)
                    return found
            old = self._superseded(conn, params)
            if old is not None:
                values["supersedes"] = str(old["id"])
            queries.insert_memory(conn, values)
            if old is not None:
                queries.supersede_row(
                    conn, str(old["id"]), memory_id, self._policy.superseded_expiry(now), now
                )
            if params.external_id:
                self._link(conn, memory_id, params.external_id)
        return memory_id


    # ------------------------------------------------------------ intake seam

    def intake(
        self,
        conn,
        *,
        thread_id: str,
        person_id: str,
        message_id: str,
        text: str,
        kind: str = "message",
        language: str = "id",
        audience: str = "private",
        asserted_at: float | None = None,
    ) -> MemoryCandidate | None:
        """Write `memory_events` + `memories(pending, level NULL)` on an open intake connection.

        The deterministic gate (ADR-0002 §4 stage 1) and the hard exclusion run before either
        row: a non-candidate, a credential/crisis/injection turn, or a candidate already
        journalled for this message never reaches the journal. The caller owns the transaction;
        a failure here must roll the message and turn back too.
        """
        self._fence(conn, "intake")
        capture = getattr(self._limits, "capture", None)
        decision = gate.structural(
            text,
            kind=kind,
            mode=getattr(capture, "mode", "balanced"),
            min_chars=getattr(capture, "min_chars", 8),
            language=language,
        )
        if not decision.admit:
            log.debug("capture gate: %s", decision.reason)
            return None
        body = _flat(text)
        digest = gate.content_hash(body)
        if gate.already_journalled(
            conn,
            thread_id=thread_id,
            message_id=message_id,
            kind=kind,
            digest=digest,
        ):
            return None
        now = time.time()
        memory_id = str(uuid.uuid4())
        event_id = str(uuid.uuid4())
        refs = [f"message:{_flat(message_id, 160)}"] if message_id else []
        queries.insert_candidate_row(
            conn,
            queries.candidate_values(
                memory_id=memory_id,
                body=body,
                person_id=str(person_id or ""),
                thread_id=str(thread_id or ""),
                kind=str(kind or "fact"),
                audience=self._audience("person", audience),
                refs=refs,
                asserted_at=float(asserted_at if asserted_at is not None else now),
                now=now,
                expires_at=self._policy.unlabeled_expiry(now),
            ),
        )
        queries.insert_event_row(
            conn,
            queries.event_values(
                event_id=event_id,
                memory_id=memory_id,
                thread_id=str(thread_id or ""),
                person_id=str(person_id or ""),
                message_id=str(message_id or ""),
                body=body,
                digest=digest,
                now=now,
            ),
        )
        return MemoryCandidate(
            memory_id=memory_id,
            event_id=event_id,
            thread_id=str(thread_id or ""),
            person_id=str(person_id or ""),
            message_id=str(message_id or ""),
            text=body,
            kind="message",
        )

    # ------------------------------------------------------------ state machine

    def _base_row(self, memory_id: str) -> dict | None:
        if self.db is None or not str(memory_id or "").strip():
            return None
        resolved = self._resolve_id(memory_id)
        if not resolved:
            return None
        with self.db.connect() as conn:
            return queries.base_row(conn, resolved)

    def _resolve_id(self, memory_id: str) -> str:
        raw = str(memory_id or "").strip().lstrip("#").strip().lower()
        if not raw or self.db is None:
            return ""
        if FULL_UUID.match(raw):
            return raw
        if not HEX_PREFIX.match(raw):
            return ""
        if len(raw) == 32:
            return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
        with self.db.connect() as conn:
            return queries.resolve_id(conn, raw)

    def _pending_ids(self, conn, thread_id: str, refs: Sequence[str]) -> list[str]:
        statement = select(memories.c.id).where(
            memories.c.state == "pending",
            memories.c.thread_id == str(thread_id or ""),
        )
        wanted = [f"message:{item}" for item in refs if str(item or "").strip()]
        if wanted:
            statement = statement.where(memories.c.refs.contains(wanted))
        statement = statement.with_for_update()
        return [str(row["id"]) for row in conn.execute(statement).mappings().all()]

    def _transition_values(
        self,
        row: Mapping,
        to_state: str,
        *,
        level: int | None,
        confirmed: bool | None,
        reason: str = "",
        now: float | None = None,
    ) -> dict:
        moment = time.time() if now is None else now
        allowed = TRANSITIONS.get(str(row.get("state") or ""), set())
        if to_state not in allowed:
            raise ValueError(f"illegal memory transition {row.get('state')} -> {to_state}")
        values: dict = {"state": to_state, "updated_at": moment}
        if to_state == "ok":
            if level is None and row.get("level") is None:
                raise ValueError("state ok needs a privacy level")
            if level is not None:
                if int(level) > 4:
                    raise ValueError("privacy level 5/credentials are never stored")
                values["level"] = int(level)
            final_level = int(level if level is not None else row["level"])
            final_confirmed = confirmed if confirmed is not None else row.get("confirmed")
            if final_confirmed is None:
                final_confirmed = True
            values["confirmed"] = bool(final_confirmed)
            plan = self._policy.plan(
                kind=str(row.get("kind") or "fact"),
                level=final_level,
                state="ok",
                durability=str(row.get("durability") or "normal"),
                category=str(row.get("category") or "general"),
                task_status=row.get("task_status"),
                pinned=bool(row.get("pinned")),
                now=moment,
            )
            values.update(
                pinned=plan.pinned,
                max_lifetime_at=plan.max_lifetime_at,
                expires_at=plan.expires_at,
            )
            values["trust"] = trust_for(
                str(row.get("via") or "turn"),
                str(row.get("owner_id") or ""),
                str(row.get("subject_id") or ""),
            )
            values["last_error"] = ""
        elif to_state in {"failed", "not_worth"}:
            values["level"] = None
            values["confirmed"] = None
            values["trust"] = TRUST_UNLABELED
            values["last_error"] = _flat(reason, 200)
            values["expires_at"] = self._policy.unlabeled_expiry(moment)
            values["pinned"] = False
        elif to_state == "superseded":
            values["pinned"] = False
            values["expires_at"] = self._policy.superseded_expiry(moment)
        return values

    def transition(
        self,
        memory_id: str,
        to_state: str,
        *,
        level: int | None = None,
        confirmed: bool | None = None,
        reason: str = "",
    ) -> bool:
        """The ADR-0004 six-state machine: only the listed edges exist, never a guessed level."""
        if self.db is None:
            return False
        resolved = self._resolve_id(memory_id)
        if not resolved:
            return False
        with self.db.connect() as conn:
            self._fence(conn, "transition")
            row = queries.locked(conn, resolved)
            if row is None:
                return False
            values = self._transition_values(
                row, to_state, level=level, confirmed=confirmed, reason=reason
            )
            queries.update_memory(conn, resolved, values)
        return True

    def label(
        self,
        memory_id: str,
        *,
        level: int,
        confirmed: bool = True,
        subject_id: str | None = None,
        category: str | None = None,
        durability: str | None = None,
        task_status: str | None = None,
    ) -> bool:
        """`pending`/`staged` -> `ok` with the assess verdict; the level is never defaulted."""
        if self.db is None:
            return False
        resolved = self._resolve_id(memory_id)
        if not resolved:
            return False
        with self.db.connect() as conn:
            self._fence(conn, "label")
            row = queries.locked(conn, resolved)
            if row is None:
                return False
            extra: dict = {}
            if subject_id is not None:
                extra["subject_id"] = str(subject_id or "")
            if category:
                extra["category"] = str(category)
            if durability:
                extra["durability"] = str(durability)
            if task_status is not None:
                extra["task_status"] = str(task_status or "")
            merged = {**dict(row), **extra}
            values = self._transition_values(
                merged, "ok", level=level, confirmed=confirmed, reason=""
            )
            if extra:
                queries.update_memory(conn, resolved, extra)
            queries.update_memory(conn, resolved, values)
        return True

    async def promote(self, memory_id: str) -> bool:
        changed = await io.run(self.transition, memory_id, "ok")
        if changed:
            await self._index_row(memory_id)
        return changed

    def mark_not_worth(self, memory_id: str, reason: str = "not worth") -> bool:
        return self.transition(memory_id, "not_worth", reason=reason)

    async def discard_candidate(
        self,
        thread_id: str,
        refs: Sequence[str] = (),
        *,
        state: str = "not_worth",
        reason: str = "not worth",
    ) -> int:
        """Move pending intake candidates to a terminal unreadable state (failed/not_worth)."""
        return await io.run(
            self._discard, thread_id, refs, state=state, reason=reason
        )

    def _discard(
        self,
        thread_id: str,
        refs: Sequence[str],
        *,
        state: str,
        reason: str,
    ) -> int:
        if self.db is None:
            return 0
        if state not in {"failed", "not_worth"}:
            raise ValueError("a pending candidate can only be dropped as failed or not_worth")
        now = time.time()
        with self.db.connect() as conn:
            self._fence(conn, "_discard")
            ids = self._pending_ids(conn, thread_id, refs)
            for memory_id in ids:
                row = queries.locked(conn, memory_id)
                if row is None:
                    continue
                values = self._transition_values(
                    row, state, level=None, confirmed=None, reason=reason, now=now
                )
                queries.update_memory(conn, memory_id, values, bump_label_attempts=True)
        return len(ids)

    async def drop_candidate(
        self,
        thread_id: str,
        refs: Sequence[str] = (),
        *,
        reason: str = "labeling failed",
    ) -> int:
        """Classifier failure drops the candidate: `failed`, level NULL, never a guessed level."""
        return await self.discard_candidate(thread_id, refs, state="failed", reason=reason)

    def _label_pending(
        self,
        thread_id: str,
        refs: Sequence[str],
        *,
        level: int,
        confirmed: bool,
        subject_id: str,
        category: str,
        durability: str,
    ) -> list[str]:
        now = time.time()
        with self.db.connect() as conn:
            self._fence(conn, "_label_pending")
            ids = self._pending_ids(conn, thread_id, refs)
            for memory_id in ids:
                row = queries.locked(conn, memory_id)
                if row is None:
                    continue
                merged = {
                    **dict(row),
                    "subject_id": str(subject_id or row.get("subject_id") or ""),
                    "category": str(category or row.get("category") or "general"),
                    "durability": str(durability or row.get("durability") or "normal"),
                }
                values = self._transition_values(
                    merged, "ok", level=int(level), confirmed=bool(confirmed), reason="", now=now
                )
                values.update(
                    subject_id=merged["subject_id"],
                    category=merged["category"],
                    durability=merged["durability"],
                )
                queries.update_memory(conn, memory_id, values)
        return ids

    # ------------------------------------------------------------ capture

    @staticmethod
    def _excluded(text: str, language: str) -> bool:
        body = _flat(text)
        return (
            not body
            or bool(SENSITIVE.search(body))
            or is_crisis(body)
            or is_injection(body, language)
        )

    @staticmethod
    def _user_text(messages: Sequence[Mapping]) -> str:
        for item in messages:
            if str(item.get("role") or "") == "user":
                return _flat(item.get("content"))
        return _flat(messages[0].get("content")) if messages else ""

    async def _store_facts(self, facts: Sequence[MemoryHit], ctx: _Capture) -> list[str]:
        ids: list[str] = []
        for fact in facts:
            try:
                ids.append(
                    await io.run(
                        self.write,
                        fact.text,
                        scope="person",
                        kind=str(fact.metadata.get("kind") or "fact"),
                        person_id=ctx.person_id,
                        thread_id=ctx.thread_id,
                        audience=ctx.audience,
                        via=ctx.route,
                        source=ctx.source,
                        refs=[f"message:{item}" for item in ctx.refs],
                        level=ctx.level,
                        owner_id=ctx.owner,
                        subject_id=ctx.subject,
                        confirmed=ctx.confirmed,
                        category=str(ctx.category or fact.metadata.get("category") or "general"),
                        durability=str(
                            ctx.durability or fact.metadata.get("durability") or "normal"
                        ),
                        dedupe=True,
                        external_id=str(fact.id or ""),
                    )
                )
            except ValueError as exc:
                log.debug("extracted fact rejected: %s", exc)
        await io.run(
            self._drop_pending,
            ctx.thread_id,
            ctx.refs,
            state="not_worth",
            reason="extracted",
        )
        for memory_id in ids:
            await self._index_row(memory_id)
        return ids

    async def _fallback_capture(self, user: str, ctx: _Capture) -> list[str]:
        labeled = await io.run(
            self._label_pending,
            ctx.thread_id,
            ctx.refs,
            level=ctx.level,
            confirmed=ctx.confirmed,
            subject_id=ctx.subject,
            category=ctx.category,
            durability=ctx.durability,
        )
        if labeled:
            for memory_id in labeled:
                await self._index_row(memory_id)
            return labeled
        try:
            memory_id = await io.run(
                self.write,
                user,
                scope="person",
                kind="fact",
                person_id=ctx.person_id,
                thread_id=ctx.thread_id,
                audience=ctx.audience,
                via=ctx.route,
                source=ctx.source,
                refs=[f"message:{item}" for item in ctx.refs],
                level=ctx.level,
                owner_id=ctx.owner,
                subject_id=ctx.subject,
                confirmed=ctx.confirmed,
                category=ctx.category or "general",
                durability=ctx.durability or "normal",
            )
        except ValueError:
            return []
        await self._index_row(memory_id)
        return [memory_id]

    async def capture(
        self,
        messages: list[dict],
        *,
        person_id: str,
        thread_id: str,
        audience: str,
        source: str = "turn",
        level: int | None = 2,
        owner_id: str = "",
        subject_id: str = "",
        confirmed: bool = True,
        refs: Sequence[str] = (),
        category: str = "",
        durability: str = "",
        worth: bool = True,
        language: str = "id",
    ) -> list[str]:
        """Label a turn candidate; Mem0 extraction is used when present, canonical never waits."""
        user = self._user_text(messages)
        if level is None or int(level) > 4 or int(level) < 0:
            return []
        if self._excluded(user, language):
            return []
        owner = str(owner_id or person_id or "")
        ctx = _Capture(
            person_id=str(person_id or ""),
            thread_id=str(thread_id or ""),
            audience=audience,
            source=source,
            level=int(level),
            owner=owner,
            subject=str(subject_id or owner),
            confirmed=bool(confirmed),
            refs=tuple(str(item) for item in refs if str(item or "").strip()),
            category=category,
            durability=durability,
            route=via_of(source),
        )
        if not worth:
            await io.run(
                self._drop_pending,
                ctx.thread_id,
                ctx.refs,
                state="not_worth",
                reason="not worth",
            )
            return []
        if self._extractor is not None and bool(getattr(self._extractor, "available", False)):
            try:
                facts = await self._extractor.capture(
                    messages,
                    person_id=ctx.person_id,
                    thread_id=ctx.thread_id,
                    audience=ctx.audience,
                    source=ctx.source,
                    level=ctx.level,
                    owner_id=ctx.owner,
                    subject_id=ctx.subject,
                    confirmed=ctx.confirmed,
                )
            except Exception as exc:  # Mem0/LLM trouble must not lose the canonical candidate
                log.warning("memory extraction skipped: %s: %s", type(exc).__name__, exc)
            else:
                if facts:
                    ids = await self._store_facts(facts, ctx)
                    if ids:
                        await io.run(self._raise_visibility, ctx.refs, ctx.level)
                    return ids
        ids = await self._fallback_capture(user, ctx)
        if ids:
            await io.run(self._raise_visibility, ctx.refs, ctx.level)
        return ids

    def _raise_visibility(self, refs: Sequence[str], level: int) -> None:
        """The stored level raises the turn's visibility so later reads respect it."""
        if self.db is None or not refs:
            return
        with self.db.connect() as conn:
            self._fence(conn, "_raise_visibility")
            queries.raise_visibility(conn, refs, level)

    def _drop_pending(
        self,
        thread_id: str,
        refs: Sequence[str],
        *,
        state: str,
        reason: str,
    ) -> int:
        if self.db is None:
            return 0
        now = time.time()
        with self.db.connect() as conn:
            self._fence(conn, "_drop_pending")
            ids = self._pending_ids(conn, thread_id, refs)
            for memory_id in ids:
                row = queries.locked(conn, memory_id)
                if row is None:
                    continue
                values = self._transition_values(
                    row, state, level=None, confirmed=None, reason=reason, now=now
                )
                queries.update_memory(conn, memory_id, values)
        return len(ids)

    # ------------------------------------------------------------ use / renewal

    def record_uses(
        self,
        turn_id: str,
        memory_ids: Sequence[str],
        *,
        delivered: bool = True,
    ) -> int:
        """ADR-0005 §4.3: one touch per memory per delivered turn, then renew deterministically."""
        if (
            self.db is None
            or not delivered
            or not turn_id
            or not self._policy.renew_on_use
            or not self._policy.enabled
        ):
            return 0
        wanted = list(dict.fromkeys(str(item) for item in memory_ids if str(item or "").strip()))
        if not wanted:
            return 0
        now = time.time()
        touched = 0
        with self.db.connect() as conn:
            self._fence(conn, "record_uses")
            for memory_id in wanted:
                if not queries.record_use(conn, memory_id, str(turn_id)):
                    continue
                row = queries.locked(conn, memory_id)
                if row is None or str(row.get("state") or "") != "ok":
                    continue
                values: dict = {
                    "use_count": memories.c.use_count + 1,
                    "distinct_use_days": queries.distinct_use_days(conn, memory_id),
                    "last_used_at": now,
                    "updated_at": now,
                }
                renewed = self._policy.renewed_expiry(
                    kind=str(row.get("kind") or "fact"),
                    level=int(row.get("level") or 0),
                    pinned=bool(row.get("pinned")),
                    confirmed=row.get("confirmed"),
                    max_lifetime_at=row.get("max_lifetime_at"),
                    last_used_at=float(row.get("last_used_at") or 0.0),
                    now=now,
                )
                if renewed is not None:
                    values["expires_at"] = renewed
                queries.update_memory(conn, memory_id, values)
                touched += 1
        return touched

    # ------------------------------------------------------------ purge / forget

    def _mapping_external_sync(self, memory_id: str) -> str:
        if self.db is None:
            return ""
        with self.db.connect() as conn:
            return queries.mapping_external(conn, memory_id)

    def _unlink_sync(self, memory_id: str) -> None:
        if self.db is None:
            return
        with self.db.connect() as conn:
            queries.unlink_index(conn, memory_id)

    def _link_sync(self, memory_id: str, external_id: str) -> None:
        if self.db is None:
            return
        with self.db.connect() as conn:
            self._link(conn, memory_id, external_id)

    async def _drop_index(self, row: Mapping) -> None:
        memory_id = str(row.get("id") or "")
        text = str(row.get("text") or "")
        external = await io.run(self._mapping_external_sync, memory_id)
        if self._has_index:
            if external and hasattr(self._index, "delete"):
                try:
                    await self._index.delete(external)
                except Exception as exc:
                    log.warning("index delete failed %s: %s", memory_id, exc)
            if hasattr(self._index, "purge_library"):
                try:
                    purged = self._index.purge_library(external, text)
                    if inspect.isawaitable(purged):
                        await purged
                except Exception as exc:
                    log.warning("library purge failed %s: %s", memory_id, exc)
        await io.run(self._unlink_sync, memory_id)

    async def _index_row(self, memory_id: str, *, external_id: str = "") -> str:
        if not self._has_index or self.db is None:
            return ""
        if not external_id:
            found = await io.run(self._mapping_external_sync, memory_id)
            if found:
                return found
        row = await io.run(self._base_row, memory_id)
        if row is None or str(row.get("state") or "") != "ok":
            return ""
        if not external_id:
            try:
                ids = await self._index.add(
                    row["text"],
                    scope=str(row["scope"]),
                    kind=str(row["kind"]),
                    person_id=str(row["person_id"]),
                    thread_id=str(row["thread_id"]),
                    audience=str(row["audience"]),
                    source=str(row["source"]),
                    metadata={"canonical_id": memory_id, "trust": int(row["trust"])},
                    infer=False,
                    level=int(row["level"]),
                    owner_id=str(row["owner_id"]),
                    subject_id=str(row["subject_id"]),
                    confirmed=bool(row["confirmed"]),
                )
            except Exception as exc:
                log.warning("memory index add failed %s: %s", memory_id, exc)
                return ""
            external_id = str(ids[0]) if ids else ""
        if external_id:
            await io.run(self._link_sync, memory_id, external_id)
        return external_id

    def _link(self, conn, memory_id: str, external_id: str) -> None:
        backend = str(getattr(self._index, "backend", "mem0"))
        model = str(getattr(self._index, "embedding_model", "") or "")
        dim = int(getattr(self._index, "dim", 384) or 384)
        values = {
            "memory_id": memory_id,
            "backend": backend,
            "external_id": str(external_id),
            "model": model,
            "dim": dim,
            "updated_at": time.time(),
        }
        conn.execute(
            pg_insert(memory_index)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["memory_id"],
                set_={key: value for key, value in values.items() if key != "memory_id"},
            )
        )

    async def forget(
        self,
        memory_id: str,
        *,
        requester_id: str = "",
        thread_id: str = "",
        allow_global: bool = False,
    ) -> bool:
        """Index -> canonical -> library copy -> content-free tombstone (ADR-0005 §4.5)."""
        row = await io.run(self._base_row, memory_id)
        if row is None:
            return False
        scope = str(row.get("scope") or "")
        if scope == "person" and str(row.get("person_id") or "") != str(requester_id or ""):
            return False
        if scope == "thread" and str(row.get("thread_id") or "") != str(thread_id or ""):
            return False
        if scope == "global" and not allow_global:
            return False
        await self._drop_index(row)
        return await io.run(self._forget_sync, row)

    def _forget_candidates_sync(self, owner_id: str, thread_id: str) -> list[dict]:
        with self.db.connect() as conn:
            return queries.forget_candidates(conn, owner_id, thread_id)

    async def forget_by_intent(
        self, text: str, *, person_id: str, thread_id: str
    ) -> list[str]:
        """ADR-0005 §4.6: "forget that …" removes the one readable row it can mean.

        Only the speaker's own readable rows in `global` and this thread are candidates, matched
        by one shared content token of at least four characters. Zero or several candidates is
        ambiguity, not a guess: nothing is deleted and the caller logs why.
        """
        if self.db is None:
            return []
        wanted = _tokens(text)
        who = str(person_id or "").strip()
        if not wanted or not who:
            return []
        found = await io.run(self._forget_candidates_sync, who, str(thread_id or ""))
        matches = [
            str(row["id"]) for row in found if wanted & _tokens(str(row.get("text") or ""))
        ]
        if len(matches) != 1:
            log.info("forget intent ambiguous: %d candidates", len(matches))
            return []
        memory_id = matches[0]
        await self.veto(memory_id, subject_id=who)
        await self.forget(memory_id, requester_id=who, thread_id=thread_id, allow_global=True)
        return [memory_id]

    def _forget_sync(self, row: Mapping) -> bool:
        with self.db.connect() as conn:
            self._fence(conn, "_forget_sync")
            removed = conn.execute(
                delete(memories)
                .where(memories.c.id == str(row["id"]))
                .returning(memories.c.id)
            ).scalar()
            if removed is None:
                return False
            conn.execute(
                pg_insert(memory_tombstones).values(
                    id=str(row["id"]),
                    kind=str(row.get("kind") or "fact"),
                    level=row.get("level"),
                    scope=str(row.get("scope") or "person"),
                    content_hash=_digest(str(row.get("text") or "")),
                    reason="forgot",
                    deleted_at=time.time(),
                )
            )
        return True

    async def purge(
        self,
        *,
        dry_run: bool = False,
        batch: int | None = None,
        grace_days: float | None = None,
        now: float | None = None,
    ) -> PurgeReport:
        """ADR-0005 §4.5: expired, unpinned rows; index, library copy, canonical, tombstone."""
        if self.db is None:
            return PurgeReport(dry_run=dry_run)
        moment = time.time() if now is None else now
        limit = int(batch if batch is not None else self._policy.purge_batch)
        cutoff = self._policy.purge_cutoff(moment, grace_days)
        rows = await io.run(self._purge_candidates_sync, cutoff, limit)
        report = PurgeReport(selected=len(rows), dry_run=dry_run)
        if dry_run:
            return report
        if not rows:
            await io.run(self._purge_metrics_only_sync, moment)
            return report
        for row in rows:
            await self._drop_index(row)
        purged = await io.run(self._purge_rows_sync, rows, cutoff, moment)
        return PurgeReport(selected=len(rows), purged=purged, dry_run=False)

    def _purge_candidates_sync(self, cutoff: float, limit: int) -> list[dict]:
        with self.db.connect() as conn:
            return queries.purge_candidates(conn, cutoff, limit)

    def _purge_metrics_only_sync(self, now: float) -> int:
        with self.db.connect() as conn:
            return self._purge_metrics(conn, now)

    def _purge_rows_sync(self, rows: Sequence[Mapping], cutoff: float, moment: float) -> int:
        purged = 0
        with self.db.connect() as conn:
            self._fence(conn, "_purge_rows_sync")
            for row in rows:
                if not queries.delete_row(conn, str(row["id"]), cutoff):
                    continue
                reason = (
                    "vetoed"
                    if bool(row.get("vetoed"))
                    else "superseded"
                    if str(row.get("state") or "") == "superseded"
                    else "expired"
                )
                queries.insert_tombstone(
                    conn,
                    {
                        "id": str(row["id"]),
                        "kind": str(row.get("kind") or "fact"),
                        "level": row.get("level"),
                        "scope": str(row.get("scope") or "person"),
                        "content_hash": _digest(str(row.get("text") or "")),
                        "reason": reason,
                        "deleted_at": moment,
                    },
                )
                purged += 1
        # Metrics retention runs in its own transaction: a stale/absent llm_calls must never
        # roll back the canonical deletes above (Postgres aborts the whole transaction).
        self._purge_metrics_only_sync(moment)
        return purged

    def _purge_metrics(self, conn, now: float) -> int:
        """ADR-0005 §4.7 / ADR-0019: cost rows expire by `engine.metrics.retention_days`."""
        try:
            return queries.purge_metrics(conn, now, retention_days_from(self.config))
        except Exception as exc:  # a metrics table problem never blocks memory retention
            log.warning("metrics purge skipped: %s", exc)
            return 0

    # ------------------------------------------------------------ veto / privacy

    async def veto(self, memory_id: str, *, subject_id: str, active: bool = True) -> bool:
        return await io.run(self._veto_sync, memory_id, subject_id, active)

    def _veto_sync(self, memory_id: str, subject_id: str, active: bool) -> bool:
        row = self._base_row(memory_id)
        if row is None or not subject_id or str(row.get("subject_id") or "") != str(subject_id):
            return False
        now = time.time()
        with self.db.connect() as conn:
            self._fence(conn, "_veto_sync")
            conn.execute(
                memories.update()
                .where(memories.c.id == str(row["id"]))
                .values(
                    vetoed=bool(active),
                    vetoed_by=str(subject_id) if active else "",
                    vetoed_at=now if active else 0.0,
                    updated_at=now,
                )
            )
        return True

    async def set_privacy(
        self,
        memory_id: str,
        *,
        level: int | None = None,
        confirmed: bool | None = None,
        subject_id: str | None = None,
        vetoed: bool | None = None,
        actor: str = "",
    ) -> bool:
        return await io.run(
            self._set_privacy_sync,
            memory_id,
            level=level,
            confirmed=confirmed,
            subject_id=subject_id,
            vetoed=vetoed,
            actor=actor,
        )

    def _set_privacy_sync(
        self,
        memory_id: str,
        *,
        level: int | None = None,
        confirmed: bool | None = None,
        subject_id: str | None = None,
        vetoed: bool | None = None,
        actor: str = "",
    ) -> bool:
        row = self._base_row(memory_id)
        if row is None:
            return False
        values: dict = {"updated_at": time.time()}
        if level is not None:
            if int(level) > 4:
                raise ValueError("privacy level 5/credentials are never stored")
            if str(row.get("state") or "") not in {"ok", "staged"}:
                raise ValueError("only a labelled memory can change level")
            values["level"] = int(level)
            plan = self._policy.plan(
                kind=str(row.get("kind") or "fact"),
                level=int(level),
                state=str(row["state"]),
                durability=str(row.get("durability") or "normal"),
                category=str(row.get("category") or "general"),
                task_status=row.get("task_status"),
                pinned=bool(row.get("pinned")),
            )
            values.update(
                pinned=plan.pinned,
                expires_at=plan.expires_at,
                max_lifetime_at=plan.max_lifetime_at,
            )
        if confirmed is not None:
            values["confirmed"] = bool(confirmed)
        if subject_id is not None:
            values["subject_id"] = str(subject_id or "")
        if vetoed is not None:
            values["vetoed"] = bool(vetoed)
            values["vetoed_by"] = str(actor or "") if vetoed else ""
            values["vetoed_at"] = time.time() if vetoed else 0.0
        with self.db.connect() as conn:
            self._fence(conn, "_set_privacy_sync")
            queries.update_memory(conn, str(row["id"]), values)
        return True

    # ------------------------------------------------------------ rekey

    async def rekey(self, old_id: str, new_id: str) -> int:
        """ADR-0001: identity rekey is an UPDATE; veto/audience/level/confirmed ride along."""
        if self.db is None or not old_id or not new_id or old_id == new_id:
            return 0
        rows = await io.run(self._rekey_rows_sync, old_id)
        for row in rows:
            await self._drop_index(row)
        return await io.run(self._rekey_update_sync, old_id, new_id)

    def _rekey_rows_sync(self, old_id: str) -> list[dict]:
        with self.db.connect() as conn:
            return queries.rekey_rows(conn, old_id)

    def _rekey_update_sync(self, old_id: str, new_id: str) -> int:
        with self.db.connect() as conn:
            self._fence(conn, "_rekey_update_sync")
            return queries.rekey_update(conn, old_id, new_id)

    # ------------------------------------------------------------ reads (view only)

    def readable(self, limit: int = 200) -> list[MemoryHit]:
        if self.db is None:
            return []
        with self.db.connect() as conn:
            found = queries.readable_rows(conn, limit)
        return [_hit(row) for row in found]

    def admin(self, *, state: str | None = None, limit: int = 200) -> list[MemoryHit]:
        """Worker/admin read of the base table (ADR-0004 §5.3); never for prompts."""
        if self.db is None:
            return []
        with self.db.connect() as conn:
            found = queries.admin_rows(conn, state, limit)
        return [_hit(row) for row in found]

    def backlog(self) -> dict[str, int]:
        if self.db is None:
            return {}
        with self.db.connect() as conn:
            return queries.backlog_rows(conn)

    def _rows(
        self, scope: str, query: str, person_id: str, thread_id: str, limit: int
    ) -> list[MemoryHit]:
        if self.db is None:
            return []
        with self.db.connect() as conn:
            found = queries.match_rows(conn, scope, query, person_id, thread_id, limit)
        return [_hit(row) for row in found]

    async def _search_scope(
        self, scope: str, query: str, person_id: str, thread_id: str, limit: int
    ) -> list[MemoryHit]:
        return await io.run(self._rows, scope, query, person_id, thread_id, limit)

    async def search(
        self,
        query: str,
        *,
        person_id: str = "",
        thread_id: str = "",
        limit: int = 12,
        scopes: tuple[str, ...] = SCOPES,
    ) -> list[MemoryHit]:
        return await memory_context.gather(
            self,
            query=query,
            person_id=person_id,
            thread_id=thread_id,
            limit=limit,
            scopes=scopes,
        )

    async def list(
        self,
        *,
        scope: str,
        person_id: str = "",
        thread_id: str = "",
        limit: int = 20,
    ) -> list[MemoryHit]:
        return await io.run(self._rows, scope, "", person_id, thread_id, limit)

    def get(self, memory_id: str) -> MemoryHit | None:
        if self.db is None:
            return None
        resolved = self._resolve_readable(memory_id)
        if not resolved:
            return None
        with self.db.connect() as conn:
            found = queries.get_view(conn, resolved)
        return _hit(found) if found else None

    def _resolve_readable(self, memory_id: str) -> str:
        raw = str(memory_id or "").strip().lstrip("#").strip().lower()
        if not raw or self.db is None:
            return ""
        if FULL_UUID.match(raw):
            return raw
        if not HEX_PREFIX.match(raw):
            return ""
        if len(raw) == 32:
            return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
        with self.db.connect() as conn:
            return queries.resolve_readable(conn, raw)

    async def context(
        self,
        *,
        person_id: str,
        thread_id: str,
        query: str,
        direct: bool | None = None,
        mood=None,
        max_items: int | None = None,
        max_chars: int | None = None,
    ) -> MemoryContext:
        """ADR-0006 §4.1: the ceiling is applied here, before anything is rendered.

        `direct` is required: a caller that does not say which audience it is building for
        cannot obtain a block, so a new read path cannot forget the ceiling.
        """
        if direct is None:
            raise ValueError("memory context needs direct (ADR-0006 §4.1)")
        limits = self._limits
        items = max_items or int(getattr(limits, "max_items", 12) or 12)
        chars = max_chars or int(getattr(limits, "max_chars", 3000) or 3000)
        policy = PrivacyPolicy(getattr(limits, "privacy", None) or PrivacyConfig())
        try:
            return await memory_context.build(
                self,
                person_id=person_id,
                thread_id=thread_id,
                query=query,
                direct=bool(direct),
                policy=policy,
                max_items=items,
                max_chars=chars,
            )
        except Exception as exc:
            log.debug("memory context skipped: %s", exc)
            return MemoryContext()

    # ------------------------------------------------------------ helpers for worker

    def pending(self, thread_id: str = "", limit: int = 200) -> list[dict]:
        """The labeling queue for ADR-0002/0003 (worker); the base table, never the view."""
        if self.db is None:
            return []
        with self.db.connect() as conn:
            return queries.pending(conn, thread_id, limit)
    # ------------------------------------------------------------ cognition worker

    def candidate_by_event(self, event_id: str) -> dict | None:
        """The pending canonical row behind a `capture` job (`payload_ref` = event id)."""
        if self.db is None or not str(event_id or "").strip():
            return None
        with self.db.connect() as conn:
            return queries.event_candidate(conn, event_id)

    def fail_candidate(self, memory_id: str, reason: str = "labeling failed") -> bool:
        """A failed assess item drops its candidate; it never guesses a level (ADR-0004)."""
        return self.transition(memory_id, "failed", reason=reason)

    def defer_candidate(self, memory_id: str, *, reason: str = "", days: float = 7.0) -> bool:
        """Hold a candidate without labeling it (a group ask, a third party the lever denies)."""
        if self.db is None:
            return False
        resolved = self._resolve_id(memory_id)
        if not resolved:
            return False
        moment = time.time()
        with self.db.connect() as conn:
            self._fence(conn, "defer_candidate")
            result = conn.execute(
                memories.update()
                .where(memories.c.id == resolved, memories.c.state == "pending")
                .values(
                    label_after=moment + max(1.0, float(days)) * DAY,
                    last_error=_flat(reason, 200),
                    updated_at=moment,
                )
            )
        return int(result.rowcount or 0) > 0

    def count_staged(self, *, scope: str = "") -> int:
        if self.db is None:
            return 0
        with self.db.connect() as conn:
            return queries.count_staged(conn, scope)

    def resolve_supersede_candidate(self, memory_id: str, source_id: str) -> bool:
        """The novelty stage said "same topic": the new statement replaces the old row."""
        if self.db is None:
            return False
        row = self._base_row(memory_id)
        source = self._base_row(source_id)
        if row is None or source is None:
            return False
        if str(row.get("state")) != "pending" or str(source.get("state")) != "ok":
            return False
        if str(source.get("via")) == "reflection":
            return False
        try:
            self.write(
                str(row.get("text") or ""),
                scope=str(source.get("scope") or "person"),
                kind=str(source.get("kind") or "fact"),
                person_id=str(row.get("person_id") or ""),
                thread_id=str(row.get("thread_id") or ""),
                audience=str(row.get("audience") or ""),
                via="turn",
                source="turn",
                refs=list(row.get("refs") or []),
                level=max(int(row.get("level") or 0), int(source.get("level") or 0)),
                owner_id=str(row.get("owner_id") or ""),
                subject_id=str(row.get("subject_id") or ""),
                confirmed=False,
                supersedes=source_id,
                change="contradiction",
                category=str(row.get("category") or "general"),
                durability=str(row.get("durability") or "normal"),
            )
        except ValueError as exc:
            log.debug("supersede candidate refused: %s", exc)
            return False
        self._drop_pending(
            str(row.get("thread_id") or ""),
            [item for item in row.get("refs") or [] if not str(item).startswith("supersede:")],
            state="not_worth",
            reason="superseded",
        )
        return True

    def scopes(self, *, limit: int = 200) -> list[str]:
        """Scopes that hold a live row: the reflection schedule input."""
        if self.db is None:
            return []
        with self.db.connect() as conn:
            found = queries.scopes(conn, limit)
        live: list[str] = []
        for row in found:
            scope = str(row["scope"] or "")
            if scope == "person" and row["person_id"]:
                live.append(f"person:{row['person_id']}")
            elif scope == "thread" and row["thread_id"]:
                live.append(f"thread:{row['thread_id']}")
            elif scope == "global":
                live.append("global")
        return sorted(set(live))

    def count_rows_since(self, *, scope: str = "", after: float = 0.0) -> int:
        if self.db is None:
            return 0
        with self.db.connect() as conn:
            return queries.count_rows_since(conn, scope, after)

    def pending_candidates(
        self, *, scope: str = "", limit: int = 20, now: float | None = None
    ) -> list[dict]:
        """Unlabeled candidates of one scope, oldest first (ADR-0002 stage 3 input)."""
        if self.db is None:
            return []
        moment = time.time() if now is None else float(now)
        with self.db.connect() as conn:
            rows = queries.pending_candidates(conn, scope, limit, moment)
        return [row for row in rows if not _marked_supersede(row)]

    def novelty_pool(
        self,
        *,
        scope: str,
        person_id: str = "",
        thread_id: str = "",
        limit: int = 200,
    ) -> list[dict]:
        """Readable rows in the candidate's own scopes: global + person + this thread."""
        if self.db is None:
            return []
        with self.db.connect() as conn:
            return queries.novelty_pool(conn, person_id, thread_id, limit)

    def label_candidate(
        self,
        memory_id: str,
        *,
        level: int,
        confirmed: bool,
        subject_id: str = "",
        category: str = "",
        durability: str = "",
        kind: str = "",
        text: str = "",
    ) -> bool:
        """Label one pending candidate from a validated assess item; never a guessed level."""
        if self.db is None:
            return False
        resolved = self._resolve_id(memory_id)
        if not resolved:
            return False
        now = time.time()
        with self.db.connect() as conn:
            self._fence(conn, "label_candidate")
            row = queries.locked(conn, resolved)
            if row is None or str(row.get("state") or "") != "pending":
                return False
            route = via_of(str(row.get("source") or ""), fallback=str(row.get("via") or "turn"))
            owner = str(row.get("owner_id") or "")
            subject = str(subject_id or row.get("subject_id") or "")
            # ADR-0008 §4.3: the stored kind passes the same quarantine the canonical write
            # applies, so a `rule` request from a turn is stored as a sourced fact.
            wanted_kind = quarantine_kind(
                str(kind or row.get("kind") or "fact"),
                via=route,
                trust=trust_for(route, owner, subject),
                corroboration_days=0,
            )
            merged = {
                **dict(row),
                "subject_id": subject,
                "category": str(category or row.get("category") or "general"),
                "durability": str(durability or row.get("durability") or "normal"),
                "kind": wanted_kind,
            }
            values = self._transition_values(
                merged, "ok", level=int(level), confirmed=bool(confirmed), reason="", now=now
            )
            extra: dict = {
                "subject_id": merged["subject_id"],
                "category": merged["category"],
                "durability": merged["durability"],
                "kind": merged["kind"],
                "label_attempts": memories.c.label_attempts + 1,
            }
            body = _flat(text)
            if body and SENSITIVE.search(body):
                return False
            if body:
                extra["text"] = body
            queries.update_memory(conn, resolved, extra)
            queries.update_memory(conn, resolved, values)
            refs = [
                str(item).split(":", 1)[1]
                for item in row.get("refs") or []
                if str(item).startswith("message:") and ":" in str(item)
            ]
            if refs:
                queries.raise_visibility(conn, refs, int(level))
        return True

    def mark_supersede_candidate(self, memory_id: str, source_id: str) -> bool:
        """Stage 2 says "known, same topic": keep the candidate for reflection, not the LLM."""
        if self.db is None:
            return False
        resolved = self._resolve_id(memory_id)
        if not resolved or not str(source_id or "").strip():
            return False
        with self.db.connect() as conn:
            self._fence(conn, "mark_supersede_candidate")
            row = (
                conn.execute(select(memories).where(memories.c.id == resolved).with_for_update())
                .mappings()
                .first()
            )
            if row is None or str(row.get("state") or "") != "pending":
                return False
            refs = list(row.get("refs") or [])
            marker = f"supersede:{source_id}"
            if marker not in refs:
                refs.append(marker)
            conn.execute(
                memories.update()
                .where(memories.c.id == resolved)
                .values(refs=refs, updated_at=time.time())
            )
        return True

    def supersede_candidates(self, *, scope: str = "", limit: int = 20) -> list[dict]:
        """Pending rows marked by the novelty stage; reflection resolves them (ADR-0010)."""
        if self.db is None:
            return []
        with self.db.connect() as conn:
            rows = queries.supersede_candidates(conn, scope, limit)
        return [row for row in rows if _marked_supersede(row)]

    def reflection_rows(self, *, scope: str = "global", limit: int = 200) -> list[dict]:
        """Worker read of the base table: only `ok`/`staged` rows of one scope."""
        if self.db is None:
            return []
        with self.db.connect() as conn:
            return queries.reflection_rows(conn, scope, limit)

    # ------------------------------------------------------------ reflection

    def apply_reflection(
        self,
        text: str,
        *,
        refs: Sequence[str],
        sources: Sequence[Mapping],
        scope: str,
        kind: str = "fact",
        person_id: str = "",
        thread_id: str = "",
        level: int | None = None,
        owner_id: str = "",
        subject_id: str = "",
        category: str = "general",
        durability: str = "normal",
        supersede: bool = False,
    ) -> str:
        """ADR-0010 §4.3/§4.9: the only derivative write, with mandatory sources."""
        if self.db is None:
            raise ValueError("memory store has no database")
        wanted = [str(item) for item in refs if str(item or "").strip()]
        if not wanted:
            raise ValueError("reflection needs refs")
        if not sources:
            raise ValueError("reflection needs its source rows")
        for source in sources:
            if str(source.get("state") or "") != "ok":
                raise ValueError("reflection reads only state=ok rows")
            if str(source.get("via") or "") == "reflection":
                raise ValueError("derive_depth 1: a derivative never derives another")
            if str(source.get("scope") or "") != str(scope):
                raise ValueError("reflection must stay in one scope")
            if str(scope) == "person" and str(source.get("person_id") or "") != str(person_id):
                raise ValueError("another person's scope is never read")
            if str(scope) == "thread" and str(source.get("thread_id") or "") != str(thread_id):
                raise ValueError("another thread's scope is never read")
        levels = [int(source.get("level") or 0) for source in sources]
        ceiling = max(levels)
        final = ceiling if level is None else int(level)
        if final < ceiling:
            raise ValueError("a derivative never lowers the level")
        strongest = next(
            source for source in sources if int(source.get("level") or 0) == ceiling
        )
        owner = str(owner_id or strongest.get("owner_id") or "")
        subject = str(subject_id or strongest.get("subject_id") or "")
        memory_id = self.write(
            text,
            scope=scope,
            kind=str(kind or strongest.get("kind") or "fact"),
            person_id=person_id,
            thread_id=thread_id,
            audience=str(strongest.get("audience") or ""),
            via="reflection",
            source="reflection",
            refs=wanted,
            level=final,
            owner_id=owner,
            subject_id=subject,
            confirmed=True,
            category=category or str(strongest.get("category") or "general"),
            durability=durability or str(strongest.get("durability") or "normal"),
            state="ok",
        )
        if supersede:
            self._merge_sources([str(source.get("id") or "") for source in sources], memory_id)
        return memory_id

    def _merge_sources(self, source_ids: Sequence[str], new_id: str) -> int:
        wanted = [item for item in source_ids if item]
        if self.db is None or not wanted:
            return 0
        now = time.time()
        with self.db.connect() as conn:
            self._fence(conn, "_merge_sources")
            result = conn.execute(
                memories.update()
                .where(
                    memories.c.id.in_(wanted),
                    memories.c.state == "ok",
                )
                .values(
                    state="superseded",
                    superseded_by=new_id,
                    pinned=False,
                    expires_at=self._policy.superseded_expiry(now),
                    updated_at=now,
                )
            )
        return int(result.rowcount or 0)

    def mark_disputed(
        self,
        left_id: str,
        right_id: str,
        *,
        reason: str = "deterministic:contradiction",
        now: float | None = None,
    ) -> bool:
        """`dispute_state='open'` on both rows; no new `state` value (ADR-0010 §4.6)."""
        if self.db is None:
            return False
        left, right = self._resolve_id(left_id), self._resolve_id(right_id)
        if not left or not right or left == right:
            return False
        moment = time.time() if now is None else float(now)
        with self.db.connect() as conn:
            self._fence(conn, "mark_disputed")
            rows = queries.pair_locked(conn, left, right)
            if len(rows) != 2 or any(str(row.get("state")) != "ok" for row in rows):
                return False
            # Pair by id: `IN (left, right)` returns rows in no guaranteed order, so
            # positional pairing can point a row at itself and leave both sides unweakened.
            for row in rows:
                other = right if str(row["id"]) == left else left
                queries.update_memory(
                    conn,
                    str(row["id"]),
                    {
                        "dispute_state": "open",
                        "disputed_with": other,
                        "dispute_reason": str(reason or "deterministic:contradiction")[:120],
                        "disputed_at": moment,
                        "updated_at": moment,
                    },
                )
        return True

    def resolve_dispute(
        self,
        left_id: str,
        right_id: str,
        *,
        winner: str | None = None,
        reason: str = "dispute_resolved",
    ) -> bool:
        """The subject's answer: a winner supersedes the loser, or both survive as resolved."""
        if self.db is None:
            return False
        left, right = self._resolve_id(left_id), self._resolve_id(right_id)
        if not left or not right:
            return False
        chosen = self._resolve_id(winner) if winner else ""
        if chosen and chosen not in {left, right}:
            return False
        now = time.time()
        with self.db.connect() as conn:
            self._fence(conn, "resolve_dispute")
            for memory_id in (left, right):
                values: dict = {
                    "dispute_state": "resolved",
                    "dispute_reason": str(reason or "dispute_resolved")[:120],
                    "updated_at": now,
                }
                if chosen and chosen != memory_id:
                    values.update(
                        state="superseded",
                        superseded_by=chosen,
                        pinned=False,
                        expires_at=self._policy.superseded_expiry(now),
                    )
                queries.update_memory(conn, memory_id, values)
        return True

    def stale_disputes(self, *, ttl_days: float = 30, now: float | None = None) -> int:
        """ADR-0010 §4.7: an unanswered dispute goes `stale`; the weaker side drops to staged."""
        if self.db is None or float(ttl_days) <= 0:
            return 0
        moment = time.time() if now is None else float(now)
        cutoff = moment - float(ttl_days) * DAY
        with self.db.connect() as conn:
            self._fence(conn, "stale_disputes")
            rows = queries.open_dispute_rows(conn, cutoff)
            for row in rows:
                values: dict = {"dispute_state": "stale", "updated_at": moment}
                partner = self._resolve_id(str(row.get("disputed_with") or ""))
                other = queries.unlocked(conn, partner) if partner else None
                if other is not None and int(row.get("trust") or 0) > int(
                    other.get("trust") or 0
                ):
                    values.update(
                        state="staged",
                        confirmed=None,
                        pinned=False,
                        expires_at=self._policy.unlabeled_expiry(moment),
                    )
                queries.update_memory(conn, str(row["id"]), values)
        return len(rows)

    # ----------------------------------------------------------- soul rules

    def sync_soul_rules(self, rules: Sequence[str], *, now: float | None = None) -> list[str]:
        """ADR-0008 §4.3: mirror the soul document's rules as `via=soul_doc` facts.

        One row per rule per UTC day, so a rule restated on separate days accumulates the
        corroboration `promote_soul_rules` needs. Nothing here creates a `rule`: the write
        quarantine always lands on `fact`.
        """
        if self.db is None:
            return []
        moment = time.time() if now is None else float(now)
        day_start = moment - (moment % DAY)
        written: list[str] = []
        for rule in rules or ():
            body = _flat(rule)
            if not body or SENSITIVE.search(body):
                continue
            with self.db.connect() as conn:
                if queries.soul_rule_seen(conn, body, day_start):
                    continue
            written.append(
                self.write(
                    body,
                    scope="global",
                    kind="fact",
                    via="soul_doc",
                    source="soul_doc",
                    refs=[f"soul:{_digest(body)[:16]}"],
                    level=0,
                    owner_id="soul",
                    subject_id="soul",
                    asserted_at=moment,
                    state="ok",
                )
            )
        return written

    def promote_soul_rules(self, *, now: float | None = None) -> int:
        """ADR-0008 §4.3: a soul rule restated on ≥2 UTC days becomes `kind=rule`.

        Grouping is by normalized text; the row set that earned it is updated together and the
        promotion lands in the action ledger as one `memory` row.
        """
        if self.db is None:
            return 0
        moment = time.time() if now is None else float(now)
        with self.db.connect() as conn:
            rows = queries.soul_rule_rows(conn)
        groups: dict[str, list[dict]] = {}
        for row in rows:
            key = " ".join(str(row.get("text") or "").lower().split())
            if key:
                groups.setdefault(key, []).append(row)
        promoted = 0
        for body in sorted(groups):
            group = groups[body]
            days = {
                time.strftime("%Y-%m-%d", time.gmtime(float(row.get("asserted_at") or 0)))
                for row in group
            }
            if len(days) < RULE_MIN_CORROBORATION_DAYS:
                continue
            level = min(int(row.get("level") or 0) for row in group)
            plan = self._policy.plan(kind="rule", level=level, state="ok", now=moment)
            ids = [str(row["id"]) for row in group]
            with self.db.connect() as conn:
                self._fence(conn, "promote_soul_rules")
                queries.set_kind_and_expiry(
                    conn,
                    ids,
                    kind="rule",
                    pinned=plan.pinned,
                    expires_at=plan.expires_at,
                    moment=moment,
                )
            digest = _digest(body)[:16]
            ActionLedger(self.db).log_action(
                "memory", digest, "memory", f"rule promoted: {digest}"
            )
            promoted += 1
        return promoted

    # ------------------------------------------------------- dispute questions

    def open_disputes(self, *, limit: int = 20) -> list[dict]:
        """Open dispute pairs, one entry each, oldest first (ADR-0010 §4.7).

        Both rows of a dispute point at each other, so a pair is emitted once and keyed by its
        sorted id pair. The ids are sorted the same way as the texts, which keeps the question
        wording stable across passes.
        """
        if self.db is None:
            return []
        with self.db.connect() as conn:
            rows = queries.open_disputes(conn, limit)
        found: dict[tuple[str, str], dict] = {}
        for row in rows:
            partner = self._base_row(str(row.get("disputed_with") or ""))
            if partner is None:
                continue
            pair = tuple(sorted((str(row["id"]), str(partner["id"]))))
            if pair in found:
                continue
            texts = {
                str(row["id"]): str(row.get("text") or ""),
                str(partner["id"]): str(partner.get("text") or ""),
            }
            found[pair] = {
                "ids": list(pair),
                "first": texts[pair[0]],
                "second": texts[pair[1]],
                "subject_id": str(row.get("subject_id") or partner.get("subject_id") or ""),
                "thread_id": str(row.get("thread_id") or partner.get("thread_id") or ""),
                "disputed_at": float(row.get("disputed_at") or 0),
            }
        return list(found.values())

    def disputes_asked_today(self, subject_id: str, *, now: float | None = None) -> int:
        """How many dispute questions this subject already got today (UTC day).

        One question marks both rows of its pair, so the count is over distinct normalized
        pairs, never rows; otherwise the budget would halve itself.
        """
        if self.db is None or not str(subject_id or "").strip():
            return 0
        moment = time.time() if now is None else float(now)
        with self.db.connect() as conn:
            return queries.disputes_asked_today(conn, subject_id, moment)

    def mark_dispute_asked(
        self, memory_ids: Sequence[str], *, now: float | None = None
    ) -> int:
        """Record that the dispute pair was asked; the cap reads this column."""
        if self.db is None:
            return 0
        wanted = [str(item) for item in memory_ids if str(item or "").strip()]
        if not wanted:
            return 0
        moment = time.time() if now is None else float(now)
        with self.db.connect() as conn:
            self._fence(conn, "mark_dispute_asked")
            result = conn.execute(
                memories.update()
                .where(memories.c.id.in_(wanted))
                .values(dispute_asked_at=moment, updated_at=moment)
            )
        return int(result.rowcount or 0)

    def promote_staged(self, *, limit: int = 20) -> int:
        """ADR-0010 §4.8: a staged row with ≥2 `ok` sources in its refs is promoted."""
        if self.db is None:
            return 0
        promoted = 0
        for row in self.pending(limit=limit):
            if str(row.get("state") or "") != "staged":
                continue
            refs = [str(item) for item in row.get("refs") or []]
            if len(refs) < 2:
                continue
            alive = self.db.fetch_all(
                select(memories.c.id).where(
                    memories.c.id.in_(refs), memories.c.state == "ok"
                )
            )
            if len(alive) < 2:
                continue
            if self.transition(str(row["id"]), "ok"):
                promoted += 1
        return promoted

    # ------------------------------------------------------------ group privacy

    def threads_of(self, person_id: str) -> set[str]:
        """The threads a person is a member of: the membership source for group retrieval."""
        if self.db is None or not str(person_id or "").strip():
            return set()
        with self.db.connect() as conn:
            return queries.threads_of(conn, person_id)

    def veto_subject(self, subject_id: str, *, by: str = "") -> int:
        """A subject veto wins across every scope: the rows disappear from retrieval."""
        if self.db is None or not str(subject_id or "").strip():
            return 0
        now = time.time()
        with self.db.connect() as conn:
            self._fence(conn, "veto_subject")
            result = conn.execute(
                memories.update()
                .where(
                    memories.c.subject_id == str(subject_id),
                    memories.c.vetoed.is_(False),
                )
                .values(
                    vetoed=True,
                    vetoed_by=str(by or subject_id),
                    vetoed_at=now,
                    dispute_state="resolved",
                    updated_at=now,
                )
            )
        return int(result.rowcount or 0)

    # ------------------------------------------------------------ maintenance

    async def backfill_index(self, *, limit: int = 200) -> int:
        """ADR-0003 slow lane: re-attach readable rows that lost their derived index entry."""
        if self.db is None or not self._has_index:
            return 0
        rows = self.db.fetch_all(
            select(memories_readable.c.id)
            .where(~memories_readable.c.id.in_(select(memory_index.c.memory_id)))
            .limit(max(1, int(limit)))
        )
        added = 0
        for row in rows:
            await self._index_row(str(row["id"]))
            added += 1
        return added
