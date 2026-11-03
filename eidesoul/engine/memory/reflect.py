"""ADR-0010 reflection: deterministic consolidation, contradiction candidates, dispute marking.

The deterministic layer runs without a model and only collects what it can prove: duplicate
texts in one scope, opposite polarity on the same predicate (using the language pack's
negations), a different number/date for an otherwise identical claim, and mutually exclusive
commitment statuses. The LLM, when present, only rules on those candidates; it never invents
rows. Every write goes through `MemoryStore.apply_reflection` with `refs` to its sources.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .. import lexicon
from .store import MemoryStore

log = logging.getLogger(__name__)

WORD = re.compile(r"\w+", re.UNICODE)
DIGITS = re.compile(r"\d+", re.UNICODE)
STATES = ("ok", "staged")

VERDICT_PROMPT = """You rule on pairs of memory entries that look contradictory.
Reply ONLY with JSON:
{"items":[{"id":"","verdict":"contradiction|contextual|unrelated","reason":""}]}
- "contradiction": both cannot hold at the same time for the same subject.
- "contextual": both are true in different contexts.
- "unrelated": the pair only shares a subject or a topic.
Return one item per input id, in the same order. Never invent an id. Keep "reason" under 80
characters and never quote a secret."""


@dataclass(frozen=True)
class Candidate:
    id: str
    left: str
    right: str
    rule: str
    subject: str = ""
    kind: str = "fact"

    @property
    def reason(self) -> str:
        return f"deterministic:{self.rule}"


@dataclass
class ReflectionReport:
    scope: str = ""
    candidates: int = 0
    writes: int = 0
    disputes: int = 0
    staged_promoted: int = 0
    stale: int = 0
    skipped: str = ""
    # ADR-0010 §4.7: the pairs this pass opened, in the order they were marked.
    opened: tuple[dict, ...] = ()
    notes: list[str] = field(default_factory=list)


def _normal(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _tokens(text: str) -> list[str]:
    return WORD.findall(_normal(text))


def _numbers(text: str) -> set[str]:
    return set(DIGITS.findall(str(text or "")))


def _same_claim(left: str, right: str) -> bool:
    return _normal(DIGITS.sub("#", _normal(left))) == _normal(DIGITS.sub("#", _normal(right)))


def opposite_polarity(left: str, right: str, negations: frozenset[str]) -> bool:
    """Same predicate, one side negated: one side has exactly one extra negation token."""
    first, second = _tokens(left), _tokens(right)
    if not first or not second:
        return False
    for head, other in ((first, second), (second, first)):
        if len(head) != len(other) + 1:
            continue
        missing = [word for word in other if word not in head]
        extra = [word for word in head if word not in other]
        if not missing and len(extra) == 1 and extra[0] in negations:
            return True
    return False


def _pair(left: Mapping, right: Mapping, language: str = "id") -> Candidate | None:
    if str(left.get("scope")) != str(right.get("scope")):
        return None
    if str(left.get("subject_id") or "") != str(right.get("subject_id") or ""):
        return None
    if str(left.get("kind") or "") != str(right.get("kind") or ""):
        return None
    if str(left.get("thread_id") or "") != str(right.get("thread_id") or ""):
        return None
    left_id, right_id = str(left.get("id") or ""), str(right.get("id") or "")
    if not left_id or not right_id or left_id == right_id:
        return None
    body_left, body_right = str(left.get("text") or ""), str(right.get("text") or "")
    kind = str(left.get("kind") or "fact")
    subject = str(left.get("subject_id") or "")
    base = {"left": left_id, "right": right_id, "subject": subject, "kind": kind}
    if kind == "commitment":
        left_status = str(left.get("task_status") or "")
        right_status = str(right.get("task_status") or "")
        if {left_status, right_status} == {"done", "pending"}:
            return Candidate(id=f"{left_id}:{right_id}", rule="exclusive_status", **base)
    if _same_claim(body_left, body_right) and _numbers(body_left) != _numbers(body_right):
        return Candidate(id=f"{left_id}:{right_id}", rule="exclusive_value", **base)
    negations = lexicon.load(language).negations
    if opposite_polarity(body_left, body_right, negations):
        return Candidate(id=f"{left_id}:{right_id}", rule="opposite_polarity", **base)
    return None


def candidates(rows: Sequence[Mapping], *, language: str = "id") -> list[Candidate]:
    """All deterministic contradiction candidates in one scope (no model, no writes)."""
    ordered = sorted(rows, key=lambda row: str(row.get("id") or ""))
    found: list[Candidate] = []
    seen: set[tuple[str, str]] = set()
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            pair = _pair(left, right, language)
            if pair is None:
                continue
            key = (pair.left, pair.right)
            if key in seen:
                continue
            seen.add(key)
            found.append(pair)
    return found


def duplicate_groups(rows: Sequence[Mapping]) -> list[list[Mapping]]:
    """Exact-text duplicates inside one scope/subject/kind: the merge target of a pass."""
    groups: dict[tuple, list[Mapping]] = {}
    for row in rows:
        if str(row.get("state") or "") != "ok":
            continue
        if str(row.get("via") or "") == "reflection":
            continue
        body = _normal(row.get("text"))
        if not body:
            continue
        key = (
            str(row.get("scope") or ""),
            str(row.get("person_id") or ""),
            str(row.get("thread_id") or ""),
            str(row.get("subject_id") or ""),
            str(row.get("kind") or ""),
            body,
        )
        groups.setdefault(key, []).append(row)
    return [group for group in groups.values() if len(group) > 1]


def _json(text: str) -> dict | None:
    raw = str(text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        body = json.loads(raw[start : end + 1])
    except (TypeError, ValueError):
        return None
    return body if isinstance(body, dict) else None


async def _rule(llm, pairs: Sequence[Candidate], rows: Sequence[Mapping]) -> set[str]:
    """The LLM only rules on candidates; a failed item is retried by the job, not guessed."""
    if llm is None or not pairs:
        return set()
    by_id = {str(row.get("id") or ""): row for row in rows}
    payload = {
        "items": [
            {
                "id": pair.id,
                "a": str(by_id.get(pair.left, {}).get("text") or ""),
                "b": str(by_id.get(pair.right, {}).get("text") or ""),
            }
            for pair in pairs
        ]
    }
    raw = await llm.complete(
        VERDICT_PROMPT, json.dumps(payload, ensure_ascii=False), max_tokens=600
    )
    body = _json(raw)
    if body is None:
        return set()
    wanted = {pair.id for pair in pairs}
    confirmed: set[str] = set()
    for item in body.get("items") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or "")
        if key in wanted and str(item.get("verdict") or "") == "contradiction":
            confirmed.add(key)
    return confirmed


def _scope_parts(scope: str) -> tuple[str, str, str]:
    head, _, value = str(scope or "").partition(":")
    if head == "person":
        return "person", value, ""
    if head == "thread":
        return "thread", "", value
    return "global", "", ""


def _merge_duplicates(
    store: MemoryStore,
    live: list[dict],
    report: ReflectionReport,
    *,
    max_writes: int,
    kind: str,
    person_id: str,
    thread_id: str,
) -> None:
    """ADR-0010 §4.2 duplicate merge: one writer, one target, no chain."""
    for group in duplicate_groups(live):
        if report.writes >= max_writes:
            report.skipped = "write budget"
            break
        sources = [row for row in group if str(row.get("state")) == "ok"]
        if len(sources) < 2:
            continue
        body = str(sources[0].get("text") or "")
        refs = [str(row.get("id") or "") for row in sources]
        try:
            store.apply_reflection(
                body,
                refs=refs,
                sources=sources,
                scope=str(sources[0].get("scope") or kind),
                kind=str(sources[0].get("kind") or "fact"),
                person_id=str(sources[0].get("person_id") or person_id),
                thread_id=str(sources[0].get("thread_id") or thread_id),
                level=max(int(row.get("level") or 0) for row in sources),
                owner_id=str(sources[0].get("owner_id") or ""),
                subject_id=str(sources[0].get("subject_id") or ""),
                category=str(sources[0].get("category") or "general"),
                durability=str(sources[0].get("durability") or "normal"),
                supersede=True,
            )
            report.writes += 1
            report.notes.append(f"merged {len(sources)} rows")
        except ValueError as exc:
            log.debug("reflection merge refused: %s", exc)
            report.notes.append(f"merge refused: {exc}")


async def run_pass(
    store: MemoryStore,
    *,
    scope: str,
    llm=None,
    language: str = "id",
    max_items: int = 40,
    max_writes: int = 20,
    min_candidates: int = 1,
    dispute_ttl_days: float = 30.0,
    derive_depth: int = 1,
    now: float | None = None,
) -> ReflectionReport:
    """One pass over one scope (ADR-0010 §4.2): one scope, one budget, one writer."""
    report = ReflectionReport(scope=str(scope or "global"))
    if store.db is None:
        report.skipped = "no database"
        return report
    if int(derive_depth) < 1:
        report.skipped = "derive_depth 0"
        return report
    moment = time.time() if now is None else float(now)
    rows = store.reflection_rows(scope=scope, limit=max(1, int(max_items)))
    if not rows:
        report.skipped = "no rows"
        return report
    live = [row for row in rows if str(row.get("state") or "") in STATES]
    if not live:
        report.skipped = "no live rows"
        return report
    kind, person_id, thread_id = _scope_parts(scope)

    _merge_duplicates(
        store,
        live,
        report,
        max_writes=max_writes,
        kind=kind,
        person_id=person_id,
        thread_id=thread_id,
    )

    pairs = candidates(live, language=language)
    report.candidates = len(pairs)
    if pairs:
        if llm is None:
            allowed = {pair.id for pair in pairs}
        elif len(pairs) >= max(1, int(min_candidates)):
            allowed = await _rule(llm, pairs, live)
        else:
            allowed = set()
            report.notes.append("below min_candidates")
        opened: list[dict] = []
        texts = {str(row.get("id") or ""): str(row.get("text") or "") for row in live}
        for pair in pairs:
            if pair.id not in allowed:
                continue
            if store.mark_disputed(pair.left, pair.right, reason=pair.reason, now=moment):
                report.disputes += 1
                opened.append(
                    {
                        "ids": [pair.left, pair.right],
                        "first": texts.get(pair.left, ""),
                        "second": texts.get(pair.right, ""),
                        "subject_id": str(pair.subject or ""),
                        "thread_id": str(
                            next(
                                (
                                    row.get("thread_id")
                                    for row in live
                                    if str(row.get("id") or "") == pair.left
                                ),
                                "",
                            )
                            or ""
                        ),
                    }
                )
        report.opened = tuple(opened)

    report.staged_promoted = store.promote_staged(limit=max_writes)
    report.stale = store.stale_disputes(ttl_days=float(dispute_ttl_days))
    return report
