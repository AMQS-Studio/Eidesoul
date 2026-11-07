"""`MemoryStore`: the one canonical write port and the readable port of ADR-0001/0004/0005/0008.

Canonical rows live in Postgres (`memories`). Reads for prompts, recall, tools, and `/memori`
go through the `memories_readable` view only; the worker/admin uses the base table. Retrieval
is native: cosine over `embedding` and lexical `search_tsv`, fused by RRF (ADR-0007).

The port stays one class; each behavior group lives in its own module and takes the facade as
its first argument (ADR-0007 §4.4).
"""


from collections.abc import Awaitable, Callable, Mapping, Sequence

from sqlalchemy import text

from ..access.identity import ID_PREFIX
from ..config import ConfigError
from ..db import Database
from ..embed import DIM as EMBED_DIM
from ..jobs.lease import FencedStore, Superseded
from . import (
    MemoryContext,
    MemoryHit,
    candidates,
    disputes,
    maintenance,
    queries,
    reads,
    records,
    writes,
)
from .integrity import WriteQuota
from .queries import MEMORY_DIM
from .records import AUDIENCES, SCOPES, MemoryCandidate, PurgeReport
from .retention import RetentionPolicy


class MemoryStore(FencedStore):
    """The canonical memory port (ADR-0001); an optional index/extractor may be attached."""

    def __init__(
        self,
        db: Database | None,
        config=None,
        *,
        index: object | None = None,
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
        self._tz = str(getattr(getattr(config, "soul", None), "timezone", "") or "")
        self._language = str(getattr(getattr(config, "soul", None), "language", "") or "")
        self._guard = None
        self.available = db is not None
        self.reason = "" if db is not None else "no database"
        self.label = "postgres"
        # ADR-0007 §4.3: the column is fixed at 384, so another embedder is refused at load.
        if int(EMBED_DIM or 0) != MEMORY_DIM:
            raise ConfigError(
                f"memory embedding dimension must be {MEMORY_DIM}, got {EMBED_DIM}"
            )

    # ---------------------------------------------------------------- wiring

    def configure_limits(self, memory) -> None:
        self._limits = memory

    def bind_guard(self, guard) -> None:
        """ADR-0002 §4.3: the lease guard fences every canonical mutation once bound."""
        self._guard = guard
        self.bind_lease(getattr(guard, "lease", None))

    def _fence_clause(self):
        """The one predicate a canonical write carries; `None` when no generation is bound."""
        return self.fence_clause()

    def _fence(self, conn, operation: str) -> None:
        """Refuse the write when the live lease generation moved on (ADR-0002 §4.3).

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

    def bind(self, index: object | None = None, extractor=None, *, label: str = "") -> None:
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

    def _database(self) -> Database:
        """The base table handle; every sync helper is reached only when `available` is true."""
        if self.db is None:  # pragma: no cover - callers refuse the work without a database
            raise ValueError("memory store has no database")
        return self.db

    async def close(self) -> None:
        close: Callable[[], Awaitable[object]] | None = getattr(self._index, "close", None)
        if close is not None:
            await close()

    # ------------------------------------------------------------ policies

    @property
    def policy(self) -> RetentionPolicy:
        return self._policy

    def _audience(self, scope: str, audience: str) -> str:
        if audience in AUDIENCES:
            return audience
        return {"global": "shared", "person": "private", "thread": "group"}[scope]

    def _canonical_subject(self, subject: str) -> str:
        """ADR-0007 §4.4: the subject is the canonical `per_…`, never a gateway address."""
        value = str(subject or "").strip()
        if not value or value.startswith(ID_PREFIX) or self.db is None:
            return value
        with self.db.connect() as conn:
            found = queries.person_for_address(conn, value)
        return found or value

    # ------------------------------------------------------------- records
    def _base_row(self, memory_id: str) -> dict | None:
        return records._base_row(self, memory_id)

    def _resolve_id(self, memory_id: str) -> str:
        return records._resolve_id(self, memory_id)

    # ------------------------------------------------------------- writes
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
        return await writes.add(
            self,
            text,
            scope=scope,
            kind=kind,
            person_id=person_id,
            thread_id=thread_id,
            audience=audience,
            source=source,
            metadata=metadata,
            supersedes=supersedes,
            infer=infer,
            level=level,
            owner_id=owner_id,
            subject_id=subject_id,
            confirmed=confirmed,
        )

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
        return writes.write(
            self,
            text,
            scope=scope,
            kind=kind,
            person_id=person_id,
            thread_id=thread_id,
            audience=audience,
            via=via,
            source=source,
            refs=refs,
            level=level,
            owner_id=owner_id,
            subject_id=subject_id,
            confirmed=confirmed,
            pinned=pinned,
            durability=durability,
            category=category,
            task_status=task_status,
            due_at=due_at,
            supersedes=supersedes,
            change=change,
            asserted_at=asserted_at,
            state=state,
            corroboration_days=corroboration_days,
            external_id=external_id,
            dedupe=dedupe,
        )

    def record_uses(
        self,
        turn_id: str,
        memory_ids: Sequence[str],
        *,
        delivered: bool = True,
    ) -> int:
        return writes.record_uses(self, turn_id, memory_ids, delivered=delivered)

    async def backfill_index(self, *, limit: int = 200) -> int:
        return await writes.backfill_index(self, limit=limit)

    # ------------------------------------------------------------- candidates
    def intake(
        self,
        conn,
        *,
        thread_id: str,
        person_id: str,
        message_id: str,
        text: str,
        kind: str = "message",
        language: str,
        audience: str = "private",
        asserted_at: float | None = None,
    ) -> MemoryCandidate | None:
        return candidates.intake(
            self,
            conn,
            thread_id=thread_id,
            person_id=person_id,
            message_id=message_id,
            text=text,
            kind=kind,
            language=language,
            audience=audience,
            asserted_at=asserted_at,
        )

    def transition(
        self,
        memory_id: str,
        to_state: str,
        *,
        level: int | None = None,
        confirmed: bool | None = None,
        reason: str = "",
    ) -> bool:
        return candidates.transition(
            self,
            memory_id,
            to_state,
            level=level,
            confirmed=confirmed,
            reason=reason,
        )

    async def promote(self, memory_id: str) -> bool:
        return await candidates.promote(self, memory_id)

    def mark_not_worth(self, memory_id: str, reason: str = "not worth") -> bool:
        return candidates.mark_not_worth(self, memory_id, reason)

    async def discard_candidate(
        self,
        thread_id: str,
        refs: Sequence[str] = (),
        *,
        state: str = "not_worth",
        reason: str = "not worth",
    ) -> int:
        return await candidates.discard_candidate(self, thread_id, refs, state=state, reason=reason)

    async def drop_candidate(
        self,
        thread_id: str,
        refs: Sequence[str] = (),
        *,
        reason: str = "labeling failed",
    ) -> int:
        return await candidates.drop_candidate(self, thread_id, refs, reason=reason)

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
        language: str,
    ) -> list[str]:
        return await candidates.capture(
            self,
            messages,
            person_id=person_id,
            thread_id=thread_id,
            audience=audience,
            source=source,
            level=level,
            owner_id=owner_id,
            subject_id=subject_id,
            confirmed=confirmed,
            refs=refs,
            category=category,
            durability=durability,
            worth=worth,
            language=language,
        )

    def pending(self, thread_id: str = "", limit: int = 200) -> list[dict]:
        return candidates.pending(self, thread_id, limit)

    def candidate_by_event(self, event_id: str) -> dict | None:
        return candidates.candidate_by_event(self, event_id)

    def fail_candidate(self, memory_id: str, reason: str = "labeling failed") -> bool:
        return candidates.fail_candidate(self, memory_id, reason)

    def defer_candidate(self, memory_id: str, *, reason: str = "", days: float = 7.0) -> bool:
        return candidates.defer_candidate(self, memory_id, reason=reason, days=days)

    def pending_candidates(
        self, *, scope: str = "", limit: int = 20, now: float | None = None
    ) -> list[dict]:
        return candidates.pending_candidates(self, scope=scope, limit=limit, now=now)

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
        evidence: Sequence[Mapping] = (),
        policy: str = "",
    ) -> bool:
        return candidates.label_candidate(
            self,
            memory_id,
            level=level,
            confirmed=confirmed,
            subject_id=subject_id,
            category=category,
            durability=durability,
            kind=kind,
            text=text,
            evidence=evidence,
            policy=policy,
        )

    # ------------------------------------------------------------- maintenance
    async def forget(
        self,
        memory_id: str,
        *,
        requester_id: str = "",
        thread_id: str = "",
        allow_global: bool = False,
    ) -> bool:
        return await maintenance.forget(
            self,
            memory_id,
            requester_id=requester_id,
            thread_id=thread_id,
            allow_global=allow_global,
        )

    async def forget_by_intent(
        self, text: str, *, person_id: str, thread_id: str
    ) -> list[str]:
        return await maintenance.forget_by_intent(
            self,
            text,
            person_id=person_id,
            thread_id=thread_id,
        )

    async def purge(
        self,
        *,
        dry_run: bool = False,
        batch: int | None = None,
        grace_days: float | None = None,
        now: float | None = None,
    ) -> PurgeReport:
        return await maintenance.purge(
            self,
            dry_run=dry_run,
            batch=batch,
            grace_days=grace_days,
            now=now,
        )

    async def veto(self, memory_id: str, *, subject_id: str, active: bool = True) -> bool:
        return await maintenance.veto(self, memory_id, subject_id=subject_id, active=active)

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
        return await maintenance.set_privacy(
            self,
            memory_id,
            level=level,
            confirmed=confirmed,
            subject_id=subject_id,
            vetoed=vetoed,
            actor=actor,
        )

    async def rekey(self, old_id: str, new_id: str) -> int:
        return await maintenance.rekey(self, old_id, new_id)

    def veto_subject(self, subject_id: str, *, by: str = "") -> int:
        return maintenance.veto_subject(self, subject_id, by=by)

    # ------------------------------------------------------------- reads
    def readable(self, limit: int = 200) -> list[MemoryHit]:
        return reads.readable(self, limit)

    def admin(self, *, state: str | None = None, limit: int = 200) -> list[MemoryHit]:
        return reads.admin(self, state=state, limit=limit)

    def backlog(self) -> dict[str, int]:
        return reads.backlog(self)

    async def search(
        self,
        query: str,
        *,
        person_id: str = "",
        thread_id: str = "",
        limit: int = 12,
        scopes: tuple[str, ...] = SCOPES,
        max_level: int = 4,
    ) -> list[MemoryHit]:
        return await reads.search(
            self,
            query,
            person_id=person_id,
            thread_id=thread_id,
            limit=limit,
            scopes=scopes,
            max_level=max_level,
        )

    async def list(
        self,
        *,
        scope: str,
        person_id: str = "",
        thread_id: str = "",
        limit: int = 20,
    ) -> list[MemoryHit]:
        return await reads.list_rows(
            self,
            scope=scope,
            person_id=person_id,
            thread_id=thread_id,
            limit=limit,
        )

    def get(self, memory_id: str) -> MemoryHit | None:
        return reads.get(self, memory_id)

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
        return await reads.context(
            self,
            person_id=person_id,
            thread_id=thread_id,
            query=query,
            direct=direct,
            mood=mood,
            max_items=max_items,
            max_chars=max_chars,
        )

    def count_staged(self, *, scope: str = "") -> int:
        return reads.count_staged(self, scope=scope)

    def scopes(self, *, limit: int = 200) -> list[str]:
        return reads.scopes(self, limit=limit)

    def count_rows_since(self, *, scope: str = "", after: float = 0.0) -> int:
        return reads.count_rows_since(self, scope=scope, after=after)

    def novelty_pool(
        self,
        *,
        scope: str,
        person_id: str = "",
        thread_id: str = "",
        limit: int = 200,
    ) -> list[dict]:
        return reads.novelty_pool(
            self,
            scope=scope,
            person_id=person_id,
            thread_id=thread_id,
            limit=limit,
        )

    def threads_of(self, person_id: str) -> set[str]:
        return reads.threads_of(self, person_id)

    # ------------------------------------------------------------- disputes
    def resolve_supersede_candidate(self, memory_id: str, source_id: str) -> bool:
        return disputes.resolve_supersede_candidate(self, memory_id, source_id)

    def mark_supersede_candidate(self, memory_id: str, source_id: str) -> bool:
        return disputes.mark_supersede_candidate(self, memory_id, source_id)

    def supersede_candidates(self, *, scope: str = "", limit: int = 20) -> list[dict]:
        return disputes.supersede_candidates(self, scope=scope, limit=limit)

    def reflection_rows(self, *, scope: str = "global", limit: int = 200) -> list[dict]:
        return disputes.reflection_rows(self, scope=scope, limit=limit)

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
        return disputes.apply_reflection(
            self,
            text,
            refs=refs,
            sources=sources,
            scope=scope,
            kind=kind,
            person_id=person_id,
            thread_id=thread_id,
            level=level,
            owner_id=owner_id,
            subject_id=subject_id,
            category=category,
            durability=durability,
            supersede=supersede,
        )

    def mark_disputed(
        self,
        left_id: str,
        right_id: str,
        *,
        reason: str = "deterministic:contradiction",
        now: float | None = None,
    ) -> bool:
        return disputes.mark_disputed(self, left_id, right_id, reason=reason, now=now)

    def resolve_dispute(
        self,
        left_id: str,
        right_id: str,
        *,
        winner: str | None = None,
        reason: str = "dispute_resolved",
    ) -> bool:
        return disputes.resolve_dispute(self, left_id, right_id, winner=winner, reason=reason)

    def stale_disputes(self, *, ttl_days: float = 30, now: float | None = None) -> int:
        return disputes.stale_disputes(self, ttl_days=ttl_days, now=now)

    def sync_soul_rules(self, rules: Sequence[str], *, now: float | None = None) -> list[str]:
        return disputes.sync_soul_rules(self, rules, now=now)

    def promote_soul_rules(self, *, now: float | None = None, tz: str) -> int:
        return disputes.promote_soul_rules(self, now=now, tz=tz)

    def open_disputes(self, *, limit: int = 20) -> list[dict]:
        return disputes.open_disputes(self, limit=limit)

    def disputes_asked_today(self, subject_id: str, *, now: float | None = None) -> int:
        return disputes.disputes_asked_today(self, subject_id, now=now)

    def mark_dispute_asked(
        self, memory_ids: Sequence[str], *, now: float | None = None
    ) -> int:
        return disputes.mark_dispute_asked(self, memory_ids, now=now)

    def promote_staged(self, *, limit: int = 20) -> int:
        return disputes.promote_staged(self, limit=limit)
