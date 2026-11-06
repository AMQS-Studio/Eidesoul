"""ADR-0010 reflection: deterministic consolidation, contradiction candidates, dispute marking.

The deterministic layer runs without a model and only collects what it can prove: duplicate
texts in one scope, opposite polarity on the same predicate (using the language pack's
negations), a different number/date for an otherwise identical claim, mutually exclusive
commitment statuses, and same-attribute statements that are close in the shared multilingual
vector space. The LLM, when present, only rules on those candidates; it never invents rows.
Every write goes through `MemoryStore.apply_reflection` with `refs` to its sources.
"""


import json
import logging
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .. import embed, io
from ..character import lexicon
from .store import MemoryStore

log = logging.getLogger(__name__)

WORD = re.compile(r"\w+", re.UNICODE)
DIGITS = re.compile(r"\d+", re.UNICODE)
CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
# A pair is a candidate when the two statements sit close enough in the shared multilingual
# space to talk about the same attribute, but not so close that one is a paraphrase of the
# other. Below the floor they are unrelated; above the ceiling the duplicate pass already owns
# them.
SIMILAR_FLOOR = 0.55
SIMILAR_CEILING = 0.94
# The "not" direction is learned from English foundation anchors (AGENTS §1: no foreign words
# in code). The multilingual encoder places a translated negation near its English anchor, so
# the difference vector recognizes a direct negation pair in any language.
POLARITY_AXIS_FLOOR = 0.45
POLARITY_ANCHORS = (
    ("the user likes it", "the user does not like it"),
    ("this is true", "this is not true"),
    ("the user wants it", "the user does not want it"),
    ("I agree with this", "I disagree with this"),
    ("the user is happy about it", "the user is unhappy about it"),
)
_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "again",
        "against",
        "also",
        "because",
        "been",
        "before",
        "being",
        "both",
        "could",
        "does",
        "doing",
        "during",
        "each",
        "from",
        "further",
        "have",
        "having",
        "here",
        "hers",
        "into",
        "its",
        "just",
        "more",
        "most",
        "much",
        "must",
        "only",
        "other",
        "ours",
        "over",
        "same",
        "should",
        "some",
        "such",
        "than",
        "that",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "under",
        "until",
        "very",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "will",
        "with",
        "would",
        "your",
        "yours",
    }
)
_AUXILIARIES = frozenset(
    {
        "am",
        "are",
        "be",
        "been",
        "being",
        "did",
        "do",
        "does",
        "had",
        "has",
        "have",
        "is",
        "was",
        "were",
    }
)
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
    return [word for word in WORD.findall(_normal(text)) if word not in _AUXILIARIES]


def _numbers(text: str) -> set[str]:
    return set(DIGITS.findall(str(text or "")))


def _same_claim(left: str, right: str) -> bool:
    return _normal(DIGITS.sub("#", _normal(left))) == _normal(DIGITS.sub("#", _normal(right)))


def _content_tokens(text: str) -> set[str]:
    return {
        word
        for word in WORD.findall(_normal(text))
        if word not in _STOPWORDS and (len(word) >= 3 or CJK.search(word))
    }


def _shared_topic(left: str, right: str) -> bool:
    """True when both statements name the same attribute: two shared content tokens, or
    half of the shorter statement (a short claim has little room to repeat itself)."""
    first, second = _content_tokens(left), _content_tokens(right)
    shared = first & second
    if not shared:
        return False
    shortest = min(len(first), len(second))
    return len(shared) >= 2 or (shortest > 0 and len(shared) / shortest >= 0.5)


def _polarity_axis() -> list[float]:
    """The English "not" direction, averaged over the foundation anchors ([] without a model)."""
    left = embed.encode([pair[0] for pair in POLARITY_ANCHORS])
    right = embed.encode([pair[1] for pair in POLARITY_ANCHORS])
    if not left or not right or len(left) != len(right) or len(left[0]) != len(right[0]):
        return []
    size = len(left[0])
    return [
        sum(float(a[index]) - float(b[index]) for a, b in zip(left, right, strict=True))
        / len(left)
        for index in range(size)
    ]


def opposite_in_vector_space(
    first: Sequence[float], second: Sequence[float], axis: Sequence[float]
) -> bool:
    """A direct negation pair in any language: the statement difference follows the axis."""
    if not axis or not first or not second or len(first) != len(second):
        return False
    difference = [float(a) - float(b) for a, b in zip(first, second, strict=True)]
    return embed.cosine(difference, axis) >= POLARITY_AXIS_FLOOR


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


def _same_identity(left: Mapping, right: Mapping) -> bool:
    """Same scope, subject, kind, and thread: two statements about the same thing."""
    return (
        str(left.get("scope")) == str(right.get("scope"))
        and str(left.get("subject_id") or "") == str(right.get("subject_id") or "")
        and str(left.get("kind") or "") == str(right.get("kind") or "")
        and str(left.get("thread_id") or "") == str(right.get("thread_id") or "")
    )


def _pair(
    left: Mapping,
    right: Mapping,
    language: str,
    vectors: Mapping[str, Sequence[float]] | None = None,
    axis: Sequence[float] | None = None,
) -> Candidate | None:
    if not _same_identity(left, right):
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
    first = (vectors or {}).get(left_id)
    second = (vectors or {}).get(right_id)
    if first is None or second is None:
        return None
    similarity = embed.cosine(first, second)
    if not SIMILAR_FLOOR <= similarity <= SIMILAR_CEILING:
        return None
    if not _shared_topic(body_left, body_right):
        return None
    rule = (
        "vector_opposite"
        if opposite_in_vector_space(first, second, axis or ())
        else "similar_claim"
    )
    return Candidate(id=f"{left_id}:{right_id}", rule=rule, **base)


def candidates(
    rows: Sequence[Mapping],
    *,
    language: str,
    vectors: Mapping[str, Sequence[float]] | None = None,
    axis: Sequence[float] | None = None,
) -> list[Candidate]:
    """All deterministic contradiction candidates in one scope (no model, no writes).

    `vectors` maps a row id to its multilingual embedding and `axis` is the English "not"
    direction; without them only the lexical rules run.
    """
    ordered = sorted(rows, key=lambda row: str(row.get("id") or ""))
    found: list[Candidate] = []
    seen: set[tuple[str, str]] = set()
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            pair = _pair(left, right, language, vectors, axis)
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
        body = _normal(str(row.get("text") or ""))
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


async def _space(rows: Sequence[Mapping]) -> tuple[dict[str, list[float]], list[float]]:
    """Embed one pass worth of statements and the English polarity axis.

    The encoder is CPU-heavy, so it runs off the event loop (AGENTS §2). An unavailable
    model yields empty results and the pass falls back to the lexical rules.
    """
    if len(rows) < 2:
        return {}, []
    texts = [str(row.get("text") or "") for row in rows]
    vectors = await io.run(embed.encode, texts)
    if not vectors or len(vectors) != len(rows):
        return {}, []
    found = {
        str(row.get("id") or ""): [float(value) for value in vector]
        for row, vector in zip(rows, vectors, strict=True)
    }
    axis = await io.run(_polarity_axis)
    return found, axis


async def run_pass(
    store: MemoryStore,
    *,
    scope: str,
    llm=None,
    language: str,
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

    vectors, axis = await _space(live)
    pairs = candidates(live, language=language, vectors=vectors, axis=axis)
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
