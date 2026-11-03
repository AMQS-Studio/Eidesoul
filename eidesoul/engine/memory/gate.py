"""ADR-0002 capture gate: the deterministic structural stage and the MiniLM novelty stage.

Stage 1 never calls a model and never reads a word list: the rules are length, provenance,
duplication, and the hard exclusion that already guards storage (credentials, crisis,
injection). Stage 2 reuses the shared 384-d embedder to decide "already known" before any
assess call is made; when the embedder is unavailable it degrades to exact-text equality
instead of guessing a similarity.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .. import embed
from ..guard import SENSITIVE, is_crisis, is_injection

MODES = ("off", "conservative", "balanced", "eager")
MESSAGE_KINDS = ("message",)
EXCLUDED_KINDS = ("command", "system", "tool", "notice")
DIGIT = re.compile(r"\d", re.UNICODE)
WORD = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class GateDecision:
    """`admit=False` means no `memory_events` row is written; the reason is for the log."""

    admit: bool
    reason: str = ""


@dataclass(frozen=True)
class Novelty:
    """`duplicate` = identical text; `known` = same topic, left for reflection to resolve."""

    verdict: str = "new"
    source_id: str = ""
    score: float = 0.0


def content_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _strong_signal(text: str, min_chars: int) -> bool:
    """A language-neutral signal: a number, a capitalised non-first token, or a long turn."""
    words = WORD.findall(text)
    if len(text) >= max(min_chars, 1) * 2:
        return True
    if DIGIT.search(text):
        return True
    return any(word[:1].isupper() for word in words[1:])


def structural(
    text: str,
    *,
    kind: str = "message",
    mode: str = "balanced",
    min_chars: int = 8,
    language: str = "id",
) -> GateDecision:
    """Stage 1 of ADR-0002 §4: no model, no per-language vocabulary."""
    body = " ".join(str(text or "").split())
    chosen = str(mode or "balanced").strip().lower()
    if chosen not in MODES:
        chosen = "balanced"
    if chosen == "off":
        return GateDecision(False, "capture off")
    if str(kind or "message") in EXCLUDED_KINDS:
        return GateDecision(False, f"not a turn: {kind}")
    if str(kind or "message") not in MESSAGE_KINDS:
        return GateDecision(False, f"unknown kind: {kind}")
    if not body:
        return GateDecision(False, "empty")
    if SENSITIVE.search(body):
        return GateDecision(False, "credential")
    if is_crisis(body):
        return GateDecision(False, "crisis")
    if is_injection(body, language):
        return GateDecision(False, "injection")
    floor = max(1, int(min_chars))
    if chosen == "eager":
        return GateDecision(True)
    if len(body) < floor:
        return GateDecision(False, "too short")
    if chosen == "conservative" and not _strong_signal(body, floor):
        return GateDecision(False, "no strong signal")
    return GateDecision(True)


def already_journalled(
    conn,
    *,
    thread_id: str,
    message_id: str,
    kind: str,
    digest: str,
) -> bool:
    """Dedupe per `(thread, message, kind)`: the same candidate is never written twice."""
    from sqlalchemy import select

    from ..schema import memory_events

    if not str(message_id or "").strip():
        return False
    found = conn.execute(
        select(memory_events.c.id)
        .where(
            memory_events.c.thread_id == str(thread_id or ""),
            memory_events.c.message_id == str(message_id or ""),
            memory_events.c.kind == str(kind or "message"),
            memory_events.c.content_hash == digest,
        )
        .limit(1)
    ).scalar()
    return found is not None


def _normal(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def novelty(
    text: str,
    candidates: Sequence[Mapping],
    *,
    threshold: float = 0.88,
) -> Novelty:
    """Stage 2 of ADR-0002 §4: compare with the readable rows of the candidate's scopes."""
    body = _normal(text)
    if not body:
        return Novelty("new")
    for row in candidates:
        if _normal(row.get("text")) == body:
            return Novelty("duplicate", str(row.get("id") or ""), 1.0)
    vectors = embed.encode([str(text or "")] + [str(row.get("text") or "") for row in candidates])
    if len(vectors) != len(candidates) + 1:
        return Novelty("new")
    head, tail = vectors[0], vectors[1:]
    best = Novelty("new")
    for row, vector in zip(candidates, tail, strict=False):
        score = embed.cosine(head, vector)
        if score >= float(threshold) and score > best.score:
            best = Novelty("known", str(row.get("id") or ""), score)
    return best
