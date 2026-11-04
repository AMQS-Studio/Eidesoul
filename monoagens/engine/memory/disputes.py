"""Reflection, disputes, soul rules, and staged promotion behind `MemoryStore`.

Derivation stays in reflection (ADR-0010); functions take the facade first
(ADR-0037 §4.4).
"""

import logging
import time
from collections.abc import Mapping, Sequence

from sqlalchemy import select

from .. import clock
from ..guard import SENSITIVE
from ..schema import memories
from . import candidates, queries, records
from .integrity import RULE_MIN_CORROBORATION_DAYS
from .ledger import ActionLedger
from .records import DAY, _digest, _flat, _marked_supersede

log = logging.getLogger(__name__)

def resolve_supersede_candidate(store, memory_id: str, source_id: str) -> bool:
    """The novelty stage said "same topic": the new statement replaces the old row."""
    if store.db is None:
        return False
    row = records._base_row(store, memory_id)
    source = records._base_row(store, source_id)
    if row is None or source is None:
        return False
    if str(row.get("state")) != "pending" or str(source.get("state")) != "ok":
        return False
    if str(source.get("via")) == "reflection":
        return False
    try:
        store.write(
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
    candidates._drop_pending(store,
        str(row.get("thread_id") or ""),
        [item for item in row.get("refs") or [] if not str(item).startswith("supersede:")],
        state="not_worth",
        reason="superseded",
    )
    return True


def mark_supersede_candidate(store, memory_id: str, source_id: str) -> bool:
    """Stage 2 says "known, same topic": keep the candidate for reflection, not the LLM."""
    if store.db is None:
        return False
    resolved = records._resolve_id(store, memory_id)
    if not resolved or not str(source_id or "").strip():
        return False
    with store.db.connect() as conn:
        store._fence(conn, "mark_supersede_candidate")
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


def supersede_candidates(store, *, scope: str = "", limit: int = 20) -> list[dict]:
    """Pending rows marked by the novelty stage; reflection resolves them (ADR-0010)."""
    if store.db is None:
        return []
    with store.db.connect() as conn:
        rows = queries.supersede_candidates(conn, scope, limit)
    return [row for row in rows if _marked_supersede(row)]


def reflection_rows(store, *, scope: str = "global", limit: int = 200) -> list[dict]:
    """Worker read of the base table: only `ok`/`staged` rows of one scope."""
    if store.db is None:
        return []
    with store.db.connect() as conn:
        return queries.reflection_rows(conn, scope, limit)


def apply_reflection(
    store,
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
    if store.db is None:
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
    memory_id = store.write(
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
        _merge_sources(store, [str(source.get("id") or "") for source in sources], memory_id)
    return memory_id


def _merge_sources(store, source_ids: Sequence[str], new_id: str) -> int:
    wanted = [item for item in source_ids if item]
    if store.db is None or not wanted:
        return 0
    now = time.time()
    with store.db.connect() as conn:
        store._fence(conn, "_merge_sources")
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
                expires_at=store._policy.superseded_expiry(now),
                updated_at=now,
            )
        )
    return int(result.rowcount or 0)


def mark_disputed(
    store,
    left_id: str,
    right_id: str,
    *,
    reason: str = "deterministic:contradiction",
    now: float | None = None,
) -> bool:
    """`dispute_state='open'` on both rows; no new `state` value (ADR-0010 §4.6)."""
    if store.db is None:
        return False
    left, right = records._resolve_id(store, left_id), records._resolve_id(store, right_id)
    if not left or not right or left == right:
        return False
    moment = time.time() if now is None else float(now)
    with store.db.connect() as conn:
        store._fence(conn, "mark_disputed")
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
    store,
    left_id: str,
    right_id: str,
    *,
    winner: str | None = None,
    reason: str = "dispute_resolved",
) -> bool:
    """The subject's answer: a winner supersedes the loser, or both survive as resolved."""
    if store.db is None:
        return False
    left, right = records._resolve_id(store, left_id), records._resolve_id(store, right_id)
    if not left or not right:
        return False
    chosen = records._resolve_id(store, winner) if winner else ""
    if chosen and chosen not in {left, right}:
        return False
    now = time.time()
    with store.db.connect() as conn:
        store._fence(conn, "resolve_dispute")
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
                    expires_at=store._policy.superseded_expiry(now),
                )
            queries.update_memory(conn, memory_id, values)
    return True


def stale_disputes(store, *, ttl_days: float = 30, now: float | None = None) -> int:
    """ADR-0010 §4.7: an unanswered dispute goes `stale`; the weaker side drops to staged."""
    if store.db is None or float(ttl_days) <= 0:
        return 0
    moment = time.time() if now is None else float(now)
    cutoff = moment - float(ttl_days) * DAY
    with store.db.connect() as conn:
        store._fence(conn, "stale_disputes")
        rows = queries.open_dispute_rows(conn, cutoff)
        for row in rows:
            values: dict = {"dispute_state": "stale", "updated_at": moment}
            partner = records._resolve_id(store, str(row.get("disputed_with") or ""))
            other = queries.unlocked(conn, partner) if partner else None
            if other is not None and int(row.get("trust") or 0) > int(
                other.get("trust") or 0
            ):
                values.update(
                    state="staged",
                    confirmed=None,
                    pinned=False,
                    expires_at=store._policy.unlabeled_expiry(moment),
                )
            queries.update_memory(conn, str(row["id"]), values)
    return len(rows)


def sync_soul_rules(store, rules: Sequence[str], *, now: float | None = None) -> list[str]:
    """ADR-0008 §4.3: mirror the soul document's rules as `via=soul_doc` facts.

    One row per rule per UTC day, so a rule restated on separate days accumulates the
    corroboration `promote_soul_rules` needs. Nothing here creates a `rule`: the write
    quarantine always lands on `fact`.
    """
    if store.db is None:
        return []
    moment = time.time() if now is None else float(now)
    day_start = moment - (moment % DAY)
    written: list[str] = []
    for rule in rules or ():
        body = _flat(rule)
        if not body or SENSITIVE.search(body):
            continue
        with store.db.connect() as conn:
            if queries.soul_rule_seen(conn, body, day_start):
                continue
        written.append(
            store.write(
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


def promote_soul_rules(store, *, now: float | None = None, tz: str) -> int:
    """ADR-0008 §4.3: a soul rule restated on ≥2 UTC days becomes `kind=rule`.

    Grouping is by normalized text; the row set that earned it is updated together and the
    promotion lands in the action ledger as one `memory` row.
    """
    if store.db is None:
        return 0
    moment = time.time() if now is None else float(now)
    with store.db.connect() as conn:
        rows = queries.soul_rule_rows(conn)
    groups: dict[str, list[dict]] = {}
    for row in rows:
        key = " ".join(str(row.get("text") or "").lower().split())
        if key:
            groups.setdefault(key, []).append(row)
    promoted = 0
    for body in sorted(groups):
        group = groups[body]
        days = {clock.day(float(row.get("asserted_at") or 0), tz) for row in group}
        if len(days) < RULE_MIN_CORROBORATION_DAYS:
            continue
        level = min(int(row.get("level") or 0) for row in group)
        plan = store._policy.plan(kind="rule", level=level, state="ok", now=moment)
        ids = [str(row["id"]) for row in group]
        with store.db.connect() as conn:
            store._fence(conn, "promote_soul_rules")
            queries.set_kind_and_expiry(
                conn,
                ids,
                kind="rule",
                pinned=plan.pinned,
                expires_at=plan.expires_at,
                moment=moment,
            )
        digest = _digest(body)[:16]
        ActionLedger(store.db).log_action(
            "memory", digest, "memory", f"rule promoted: {digest}"
        )
        promoted += 1
    return promoted


def open_disputes(store, *, limit: int = 20) -> list[dict]:
    """Open dispute pairs, one entry each, oldest first (ADR-0010 §4.7).

    Both rows of a dispute point at each other, so a pair is emitted once and keyed by its
    sorted id pair. The ids are sorted the same way as the texts, which keeps the question
    wording stable across passes.
    """
    if store.db is None:
        return []
    with store.db.connect() as conn:
        rows = queries.open_disputes(conn, limit)
    found: dict[tuple[str, ...], dict] = {}
    for row in rows:
        partner = records._base_row(store, str(row.get("disputed_with") or ""))
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


def disputes_asked_today(store, subject_id: str, *, now: float | None = None) -> int:
    """How many dispute questions this subject already got today (UTC day).

    One question marks both rows of its pair, so the count is over distinct normalized
    pairs, never rows; otherwise the budget would halve itself.
    """
    if store.db is None or not str(subject_id or "").strip():
        return 0
    moment = time.time() if now is None else float(now)
    with store.db.connect() as conn:
        return queries.disputes_asked_today(conn, subject_id, moment)


def mark_dispute_asked(
    store, memory_ids: Sequence[str], *, now: float | None = None
) -> int:
    """Record that the dispute pair was asked; the cap reads this column."""
    if store.db is None:
        return 0
    wanted = [str(item) for item in memory_ids if str(item or "").strip()]
    if not wanted:
        return 0
    moment = time.time() if now is None else float(now)
    with store.db.connect() as conn:
        store._fence(conn, "mark_dispute_asked")
        result = conn.execute(
            memories.update()
            .where(memories.c.id.in_(wanted))
            .values(dispute_asked_at=moment, updated_at=moment)
        )
    return int(result.rowcount or 0)


def promote_staged(store, *, limit: int = 20) -> int:
    """ADR-0010 §4.8: a staged row with ≥2 `ok` sources in its refs is promoted."""
    if store.db is None:
        return 0
    promoted = 0
    for row in store.pending(limit=limit):
        if str(row.get("state") or "") != "staged":
            continue
        refs = [str(item) for item in row.get("refs") or []]
        if len(refs) < 2:
            continue
        alive = store.db.fetch_all(
            select(memories.c.id).where(
                memories.c.id.in_(refs), memories.c.state == "ok"
            )
        )
        if len(alive) < 2:
            continue
        if store.transition(str(row["id"]), "ok"):
            promoted += 1
    return promoted
