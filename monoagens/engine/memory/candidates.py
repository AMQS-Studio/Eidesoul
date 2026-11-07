"""Intake, capture, the unlabeled state machine, and the labeling queue.

The facade owns the transaction and the fence; these helpers take it as their first
argument (ADR-0002/0004, ADR-0007 §4.4).
"""

import logging
import time
import uuid
from collections.abc import Mapping, Sequence

from sqlalchemy import select

from .. import embed, io
from ..guard import SENSITIVE, is_crisis, is_injection
from ..schema import memories
from . import MemoryHit, gate, queries, records, writes
from .integrity import TRUST_UNLABELED, quarantine_kind, trust_for, via_of
from .records import (
    DAY,
    TRANSITIONS,
    MemoryCandidate,
    _Capture,
    _flat,
    _marked_supersede,
)

log = logging.getLogger(__name__)

def intake(
    store,
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
    """Write `memory_events` + `memories(pending, level NULL)` on an open intake connection.

    The deterministic gate (ADR-0002 §4 stage 1) and the hard exclusion run before either
    row: a non-candidate, a credential/crisis/injection turn, or a candidate already
    journalled for this message never reaches the journal. The caller owns the transaction;
    a failure here must roll the message and turn back too.
    """
    store._fence(conn, "intake")
    capture = getattr(store._limits, "capture", None)
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
            audience=store._audience("person", audience),
            refs=refs,
            asserted_at=float(asserted_at if asserted_at is not None else now),
            now=now,
            expires_at=store._policy.unlabeled_expiry(now),
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


def _pending_ids(store, conn, thread_id: str, refs: Sequence[str]) -> list[str]:
    statement = select(memories.c.id).where(
        memories.c.state == "pending",
        memories.c.thread_id == str(thread_id or ""),
    )
    wanted = [f"message:{item}" for item in refs if str(item or "").strip()]
    if wanted:
        statement = statement.where(memories.c.refs.contains(wanted))
    statement = statement.with_for_update()
    return [str(row["id"]) for row in conn.execute(statement).mappings().all()]


def _vector(text: str) -> list[float] | None:
    """The shared 384-dim embedding, or None when the embedder cannot provide it."""
    body = str(text or "").strip()
    if not body:
        return None
    encoded = embed.encode([body])
    if not encoded or len(encoded[0]) != queries.MEMORY_DIM:
        return None
    return encoded[0]


def _transition_values(
    store,
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
        plan = store._policy.plan(
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
        # ADR-0007 §4.1: the row becomes readable here, so it gains its vector here.
        values["embedding"] = _vector(str(row.get("text") or ""))
    elif to_state in {"failed", "not_worth"}:
        values["level"] = None
        values["confirmed"] = None
        values["trust"] = TRUST_UNLABELED
        values["last_error"] = _flat(reason, 200)
        values["expires_at"] = store._policy.unlabeled_expiry(moment)
        values["pinned"] = False
    elif to_state == "superseded":
        values["pinned"] = False
        values["expires_at"] = store._policy.superseded_expiry(moment)
    return values


def transition(
    store,
    memory_id: str,
    to_state: str,
    *,
    level: int | None = None,
    confirmed: bool | None = None,
    reason: str = "",
) -> bool:
    """The ADR-0004 six-state machine: only the listed edges exist, never a guessed level."""
    if store.db is None:
        return False
    resolved = records._resolve_id(store, memory_id)
    if not resolved:
        return False
    with store.db.connect() as conn:
        store._fence(conn, "transition")
        row = queries.locked(conn, resolved)
        if row is None:
            return False
        values = _transition_values(store,
            row, to_state, level=level, confirmed=confirmed, reason=reason
        )
        queries.update_memory(conn, resolved, values)
    return True


async def promote(store, memory_id: str) -> bool:
    changed = await io.run(store.transition, memory_id, "ok")
    if changed:
        await writes._index_row(store, memory_id)
    return changed


def mark_not_worth(store, memory_id: str, reason: str = "not worth") -> bool:
    return store.transition(memory_id, "not_worth", reason=reason)


async def discard_candidate(
    store,
    thread_id: str,
    refs: Sequence[str] = (),
    *,
    state: str = "not_worth",
    reason: str = "not worth",
) -> int:
    """Move pending intake candidates to a terminal unreadable state (failed/not_worth)."""
    return await io.run(
        _discard, store, thread_id, refs, state=state, reason=reason
    )


def _discard(
    store,
    thread_id: str,
    refs: Sequence[str],
    *,
    state: str,
    reason: str,
) -> int:
    if store.db is None:
        return 0
    if state not in {"failed", "not_worth"}:
        raise ValueError("a pending candidate can only be dropped as failed or not_worth")
    now = time.time()
    with store.db.connect() as conn:
        store._fence(conn, "_discard")
        ids = _pending_ids(store, conn, thread_id, refs)
        for memory_id in ids:
            row = queries.locked(conn, memory_id)
            if row is None:
                continue
            values = _transition_values(store,
                row, state, level=None, confirmed=None, reason=reason, now=now
            )
            queries.update_memory(conn, memory_id, values, bump_label_attempts=True)
    return len(ids)


async def drop_candidate(
    store,
    thread_id: str,
    refs: Sequence[str] = (),
    *,
    reason: str = "labeling failed",
) -> int:
    """Classifier failure drops the candidate: `failed`, level NULL, never a guessed level."""
    return await store.discard_candidate(thread_id, refs, state="failed", reason=reason)


def _label_pending(
    store,
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
    with store._database().connect() as conn:
        store._fence(conn, "_label_pending")
        ids = _pending_ids(store, conn, thread_id, refs)
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
            values = _transition_values(store,
                merged, "ok", level=int(level), confirmed=bool(confirmed), reason="", now=now
            )
            values.update(
                subject_id=merged["subject_id"],
                category=merged["category"],
                durability=merged["durability"],
            )
            queries.update_memory(conn, memory_id, values)
    return ids


def _excluded(text: str, language: str) -> bool:
    body = _flat(text)
    return (
        not body
        or bool(SENSITIVE.search(body))
        or is_crisis(body, language)
        or is_injection(body, language)
    )


def _user_text(messages: Sequence[Mapping]) -> str:
    for item in messages:
        if str(item.get("role") or "") == "user":
            return _flat(item.get("content"))
    return _flat(messages[0].get("content")) if messages else ""


async def _store_facts(store, facts: Sequence[MemoryHit], ctx: _Capture) -> list[str]:
    ids: list[str] = []
    for fact in facts:
        try:
            ids.append(
                await io.run(
                    store.write,
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
        _drop_pending,
        store,
        ctx.thread_id,
        ctx.refs,
        state="not_worth",
        reason="extracted",
    )
    for memory_id in ids:
        await writes._index_row(store, memory_id)
    return ids


async def _fallback_capture(store, user: str, ctx: _Capture) -> list[str]:
    labeled = await io.run(
        _label_pending,
        store,
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
            await writes._index_row(store, memory_id)
        return labeled
    try:
        memory_id = await io.run(
            store.write,
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
    await writes._index_row(store, memory_id)
    return [memory_id]


async def capture(
    store,
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
    """Label a turn candidate; a configured extractor is used when present and never blocks."""
    user = _user_text(messages)
    if level is None or int(level) > 4 or int(level) < 0:
        return []
    if _excluded(user, language):
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
            _drop_pending,
            store,
            ctx.thread_id,
            ctx.refs,
            state="not_worth",
            reason="not worth",
        )
        return []
    if store._extractor is not None and bool(getattr(store._extractor, "available", False)):
        try:
            facts = await store._extractor.capture(
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
        except Exception as exc:  # extractor/LLM trouble must not lose the canonical candidate
            log.warning("memory extraction skipped: %s: %s", type(exc).__name__, exc)
        else:
            if facts:
                ids = await _store_facts(store, facts, ctx)
                if ids:
                    await io.run(_raise_visibility, store, ctx.refs, ctx.level)
                return ids
    ids = await _fallback_capture(store, user, ctx)
    if ids:
        await io.run(_raise_visibility, store, ctx.refs, ctx.level)
    return ids


def _raise_visibility(store, refs: Sequence[str], level: int) -> None:
    """The stored level raises the turn's visibility so later reads respect it."""
    if store.db is None or not refs:
        return
    with store.db.connect() as conn:
        store._fence(conn, "_raise_visibility")
        queries.raise_visibility(conn, refs, level)


def _drop_pending(
    store,
    thread_id: str,
    refs: Sequence[str],
    *,
    state: str,
    reason: str,
) -> int:
    if store.db is None:
        return 0
    now = time.time()
    with store.db.connect() as conn:
        store._fence(conn, "_drop_pending")
        ids = _pending_ids(store, conn, thread_id, refs)
        for memory_id in ids:
            row = queries.locked(conn, memory_id)
            if row is None:
                continue
            values = _transition_values(store,
                row, state, level=None, confirmed=None, reason=reason, now=now
            )
            queries.update_memory(conn, memory_id, values)
    return len(ids)


def pending(store, thread_id: str = "", limit: int = 200) -> list[dict]:
    """The labeling queue for ADR-0002/0003 (worker); the base table, never the view."""
    if store.db is None:
        return []
    with store.db.connect() as conn:
        return queries.pending(conn, thread_id, limit)


def candidate_by_event(store, event_id: str) -> dict | None:
    """The pending canonical row behind a `capture` job (`payload_ref` = event id)."""
    if store.db is None or not str(event_id or "").strip():
        return None
    with store.db.connect() as conn:
        return queries.event_candidate(conn, event_id)


def fail_candidate(store, memory_id: str, reason: str = "labeling failed") -> bool:
    """A failed assess item drops its candidate; it never guesses a level (ADR-0004)."""
    return store.transition(memory_id, "failed", reason=reason)


def defer_candidate(store, memory_id: str, *, reason: str = "", days: float = 7.0) -> bool:
    """Hold a candidate without labeling it (a group ask, a third party the lever denies)."""
    if store.db is None:
        return False
    resolved = records._resolve_id(store, memory_id)
    if not resolved:
        return False
    moment = time.time()
    with store.db.connect() as conn:
        store._fence(conn, "defer_candidate")
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


def pending_candidates(
    store, *, scope: str = "", limit: int = 20, now: float | None = None
) -> list[dict]:
    """Unlabeled candidates of one scope, oldest first (ADR-0002 stage 3 input)."""
    if store.db is None:
        return []
    moment = time.time() if now is None else float(now)
    with store.db.connect() as conn:
        rows = queries.pending_candidates(conn, scope, limit, moment)
    return [row for row in rows if not _marked_supersede(row)]


def label_candidate(
    store,
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
    """Label one pending candidate from a validated assess item; never a guessed level."""
    if store.db is None:
        return False
    resolved = records._resolve_id(store, memory_id)
    if not resolved:
        return False
    now = time.time()
    with store.db.connect() as conn:
        store._fence(conn, "label_candidate")
        row = queries.locked(conn, resolved)
        if row is None or str(row.get("state") or "") != "pending":
            return False
        route = via_of(str(row.get("source") or ""), fallback=str(row.get("via") or "turn"))
        owner = str(row.get("owner_id") or "")
        subject = store._canonical_subject(str(subject_id or row.get("subject_id") or ""))
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
        values = _transition_values(store,
            merged, "ok", level=int(level), confirmed=bool(confirmed), reason="", now=now
        )
        extra: dict = {
            "subject_id": merged["subject_id"],
            "category": merged["category"],
            "durability": merged["durability"],
            "kind": merged["kind"],
            "label_attempts": memories.c.label_attempts + 1,
            "privacy_evidence": [dict(item) for item in evidence],
            "privacy_policy": str(policy or ""),
        }
        body = _flat(text)
        if body and SENSITIVE.search(body):
            return False
        if body:
            extra["text"] = body
            extra["embedding"] = _vector(body)
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
