"""Forget, purge, veto, privacy changes, and rekey behind `MemoryStore`.

Each function takes the facade first so the fence and the one database handle stay
unchanged (ADR-0005, ADR-0037 §4.4).
"""

import logging
import re
import time
from collections.abc import Mapping, Sequence

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .. import embed, io
from ..character import lexicon
from ..schema import memories, memory_tombstones
from . import queries, records, writes
from .evidence import POLICY_VERSION
from .records import PurgeReport, _digest, _tokens
from .retention import retention_days_from

log = logging.getLogger(__name__)

async def forget(
    store,
    memory_id: str,
    *,
    requester_id: str = "",
    thread_id: str = "",
    allow_global: bool = False,
) -> bool:
    """Index -> canonical -> library copy -> content-free tombstone (ADR-0005 §4.5)."""
    row = await io.run(records._base_row, store, memory_id)
    if row is None:
        return False
    scope = str(row.get("scope") or "")
    if scope == "person" and str(row.get("person_id") or "") != str(requester_id or ""):
        return False
    if scope == "thread" and str(row.get("thread_id") or "") != str(thread_id or ""):
        return False
    if scope == "global" and not allow_global:
        return False
    await writes._drop_index(store, row)
    return await io.run(_forget_sync, store, row)


def _forget_candidates_sync(store, owner_id: str, thread_id: str) -> list[dict]:
    with store._database().connect() as conn:
        return queries.forget_candidates(conn, owner_id, thread_id)


_SEMANTIC_FLOOR = 0.55
_SEMANTIC_NARROW_FLOOR = 0.45
_SEMANTIC_MARGIN = 0.10


def _stem(token: str, suffixes: Sequence[str]) -> str:
    """Strip one possessive suffix from the pack so "gajiku" can meet "gaji"."""
    for suffix in suffixes:
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _stems(tokens: set[str], suffixes: Sequence[str]) -> set[str]:
    return {_stem(token, suffixes) for token in tokens}


def _semantic_matches_sync(
    rows: list[dict],
    query: str,
    stopwords,
    suffixes: Sequence[str],
    floor: float = _SEMANTIC_FLOOR,
) -> list[str]:
    """The one row a paraphrase or a possessive form can still mean, or [].

    Cosine similarity is the primary signal; a single suffix-stripped stem match is the
    fallback for short nouns the encoder cannot separate. Ambiguity stays ambiguity.
    """
    wanted = _stems(_tokens(query, stopwords), suffixes)
    stem_hits = [
        str(row["id"])
        for row in rows
        if wanted & _stems(_tokens(str(row.get("text") or ""), stopwords), suffixes)
    ]
    vectors = embed.encode([query])
    if not vectors:
        return stem_hits if len(stem_hits) == 1 else []
    scored: list[tuple[float, str]] = []
    for row in rows:
        vector = row.get("embedding")
        if vector is None:
            continue
        scored.append((embed.cosine(vectors[0], vector), str(row["id"])))
    if not scored:
        return stem_hits if len(stem_hits) == 1 else []
    scored.sort(key=lambda item: item[0], reverse=True)
    top_score, top_id = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if top_score >= floor and top_score - runner_up >= _SEMANTIC_MARGIN:
        return [top_id]
    return stem_hits if len(stem_hits) == 1 else []


def _proper_tokens(text: str) -> set[str]:
    """Capitalized words are likely names, so a forget request can prefer them."""
    return {word.lower() for word in re.findall(r"\b[A-Z][A-Za-z0-9_-]+\b", str(text or ""))}


def _token_match(wanted: set[str], found: set[str], unique: set[str]) -> bool:
    """A shared token that only one candidate owns, or a long enough prefix of one."""
    shared = wanted & found
    if shared & unique:
        return True
    return any(
        len(left) >= 4
        and len(right) >= 4
        and (left.startswith(right) or right.startswith(left))
        and (left in unique or right in unique)
        for left in wanted
        for right in found
    )


async def forget_by_intent(
    store, text: str, *, person_id: str, thread_id: str
) -> list[str]:
    """ADR-0005 §4.6: "forget that …" removes the one readable row it can mean.

    Only the speaker's own readable rows in `global` and this thread are candidates, matched
    by one shared content token of at least three characters. The language's stopwords are
    dropped first, so a request particle never turns every row into a candidate: the topic
    token is what matches. A request with no shared token falls back to the stored embeddings
    and possessive stems (ADR-0009 §4.4). Zero or several candidates is ambiguity, not a
    guess: nothing is deleted and the caller logs why.
    """
    if store.db is None:
        return []
    language = str(getattr(store, "_language", "") or "")
    pack = lexicon.load(language) if language else None
    stopwords = pack.stopwords if pack is not None else frozenset()
    suffixes = pack.possessives if pack is not None else frozenset()
    wanted = _tokens(text, stopwords)
    who = str(person_id or "").strip()
    if not wanted or not who:
        return []
    found = await io.run(_forget_candidates_sync, store, who, str(thread_id or ""))
    texts = {str(row["id"]): str(row.get("text") or "") for row in found}
    token_sets = {memory_id: _tokens(text, stopwords) for memory_id, text in texts.items()}
    proper = _proper_tokens(text)
    proper_matches = [
        memory_id
        for memory_id, tokens in token_sets.items()
        if proper & tokens and proper & _proper_tokens(texts[memory_id])
    ]
    counts: dict[str, int] = {}
    for tokens in token_sets.values():
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
    unique = {token for token, count in counts.items() if count == 1}
    matches = proper_matches or [
        memory_id
        for memory_id, tokens in token_sets.items()
        if _token_match(wanted, tokens, unique)
    ]
    if len(matches) > 1 and not proper_matches:
        narrowed = await io.run(
            _semantic_matches_sync,
            [row for row in found if str(row["id"]) in matches],
            text,
            stopwords,
            suffixes,
            _SEMANTIC_NARROW_FLOOR,
        )
        if narrowed:
            matches = narrowed
    if not matches:
        # ADR-0009 §4.4: a paraphrase or a possessive form is still a forget request; the
        # exact-token path stays first so ordinary requests never pay for an embedding.
        matches = await io.run(_semantic_matches_sync, found, text, stopwords, suffixes)
    if len(matches) != 1:
        log.info("forget intent ambiguous: %d candidates", len(matches))
        return []
    memory_id = matches[0]
    await store.veto(memory_id, subject_id=who)
    await store.forget(memory_id, requester_id=who, thread_id=thread_id, allow_global=True)
    return [memory_id]


def _forget_sync(store, row: Mapping) -> bool:
    with store._database().connect() as conn:
        store._fence(conn, "_forget_sync")
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
    store,
    *,
    dry_run: bool = False,
    batch: int | None = None,
    grace_days: float | None = None,
    now: float | None = None,
) -> PurgeReport:
    """ADR-0005 §4.5: expired, unpinned rows; index, library copy, canonical, tombstone."""
    if store.db is None:
        return PurgeReport(dry_run=dry_run)
    moment = time.time() if now is None else now
    limit = int(batch if batch is not None else store._policy.purge_batch)
    cutoff = store._policy.purge_cutoff(moment, grace_days)
    rows = await io.run(_purge_candidates_sync, store, cutoff, limit)
    report = PurgeReport(selected=len(rows), dry_run=dry_run)
    if dry_run:
        return report
    if not rows:
        await io.run(_purge_metrics_only_sync, store, moment)
        return report
    for row in rows:
        await writes._drop_index(store, row)
    purged = await io.run(_purge_rows_sync, store, rows, cutoff, moment)
    return PurgeReport(selected=len(rows), purged=purged, dry_run=False)


def _purge_candidates_sync(store, cutoff: float, limit: int) -> list[dict]:
    with store._database().connect() as conn:
        return queries.purge_candidates(conn, cutoff, limit)


def _purge_metrics_only_sync(store, now: float) -> int:
    with store._database().connect() as conn:
        return _purge_metrics(store, conn, now)


def _purge_rows_sync(store, rows: Sequence[Mapping], cutoff: float, moment: float) -> int:
    purged = 0
    with store._database().connect() as conn:
        store._fence(conn, "_purge_rows_sync")
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
    _purge_metrics_only_sync(store, moment)
    return purged


def _purge_metrics(store, conn, now: float) -> int:
    """ADR-0005 §4.7 / ADR-0019: cost rows expire by `engine.metrics.retention_days`."""
    try:
        return queries.purge_metrics(conn, now, retention_days_from(store.config))
    except Exception as exc:  # a metrics table problem never blocks memory retention
        log.warning("metrics purge skipped: %s", exc)
        return 0


async def veto(store, memory_id: str, *, subject_id: str, active: bool = True) -> bool:
    return await io.run(_veto_sync, store, memory_id, subject_id, active)


def _veto_sync(store, memory_id: str, subject_id: str, active: bool) -> bool:
    row = records._base_row(store, memory_id)
    if row is None or not subject_id or str(row.get("subject_id") or "") != str(subject_id):
        return False
    now = time.time()
    with store._database().connect() as conn:
        store._fence(conn, "_veto_sync")
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
    store,
    memory_id: str,
    *,
    level: int | None = None,
    confirmed: bool | None = None,
    subject_id: str | None = None,
    vetoed: bool | None = None,
    actor: str = "",
) -> bool:
    return await io.run(
        _set_privacy_sync,
        store,
        memory_id,
        level=level,
        confirmed=confirmed,
        subject_id=subject_id,
        vetoed=vetoed,
        actor=actor,
    )


def _set_privacy_sync(
    store,
    memory_id: str,
    *,
    level: int | None = None,
    confirmed: bool | None = None,
    subject_id: str | None = None,
    vetoed: bool | None = None,
    actor: str = "",
) -> bool:
    row = records._base_row(store, memory_id)
    if row is None:
        return False
    values: dict = {"updated_at": time.time()}
    if level is not None:
        if int(level) > 4:
            raise ValueError("privacy level 5/credentials are never stored")
        if str(row.get("state") or "") not in {"ok", "staged"}:
            raise ValueError("only a labelled memory can change level")
        values["level"] = int(level)
        values["privacy_policy"] = POLICY_VERSION
        plan = store._policy.plan(
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
    with store._database().connect() as conn:
        store._fence(conn, "_set_privacy_sync")
        queries.update_memory(conn, str(row["id"]), values)
    return True


async def rekey(store, old_id: str, new_id: str) -> int:
    """ADR-0001: identity rekey is an UPDATE; veto/audience/level/confirmed ride along."""
    if store.db is None or not old_id or not new_id or old_id == new_id:
        return 0
    rows = await io.run(_rekey_rows_sync, store, old_id)
    for row in rows:
        await writes._drop_index(store, row)
    return await io.run(_rekey_update_sync, store, old_id, new_id)


def _rekey_rows_sync(store, old_id: str) -> list[dict]:
    with store._database().connect() as conn:
        return queries.rekey_rows(conn, old_id)


def _rekey_update_sync(store, old_id: str, new_id: str) -> int:
    with store._database().connect() as conn:
        store._fence(conn, "_rekey_update_sync")
        return queries.rekey_update(conn, old_id, new_id)


def veto_subject(store, subject_id: str, *, by: str = "") -> int:
    """A subject veto wins across every scope: the rows disappear from retrieval."""
    if store.db is None or not str(subject_id or "").strip():
        return 0
    now = time.time()
    with store.db.connect() as conn:
        store._fence(conn, "veto_subject")
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
