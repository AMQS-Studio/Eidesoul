"""Local privacy evidence and deterministic policy (ADR-0038).

Detectors report *evidence*; only `decide()` writes a level and an action. The pattern layer
is deterministic and versioned with the language pack; the semantic layer reuses the one
MiniLM embedder and classifies by label centroids. Neither layer sees affect: a sentiment score
is not a confidentiality level (ADR-0038 §4.10).
"""

import hashlib
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from .. import embed

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
POLICY_VERSION = "privacy-2026-11-04.1"
POLICY_HASH = hashlib.sha256(POLICY_VERSION.encode("utf-8")).hexdigest()
MAX_CLASSIFY_CHARS = 4000
DEFAULT_DIR = Path(__file__).resolve().parents[2] / "library" / "privacy"
CREDENTIAL = "credential"
AMBIGUOUS = "ambiguous"
LABELS = (
    "credential",
    "health",
    "financial",
    "crime_admission",
    "self_harm",
    "violence",
    "sexual",
    "relationship_conflict",
    "third_party_personal",
    "legal",
)
LEVELS = {
    "credential": 4,
    "health": 4,
    "crime_admission": 4,
    "self_harm": 4,
    "violence": 4,
    "sexual": 4,
    "financial": 3,
    "relationship_conflict": 3,
    "third_party_personal": 3,
    "legal": 3,
}
ACTIONS = ("allow", "redact", "block", "ask", "drop")


@dataclass(frozen=True)
class Evidence:
    """One detector assertion; a span is null for a whole-text label."""

    detector: str
    version: str
    digest: str
    label: str
    score: float
    span: tuple[int, int] | None = None

    def record(self) -> dict:
        return {
            "detector": self.detector,
            "version": self.version,
            "digest": self.digest,
            "label": self.label,
            "score": round(float(self.score), 4),
            "span": list(self.span) if self.span is not None else None,
        }


@dataclass(frozen=True)
class PrivacyDecision:
    level: int = 2
    action: str = "allow"
    policy_version: str = POLICY_VERSION
    policy_hash: str = POLICY_HASH
    needs_review: bool = False
    ask: bool = False
    label: str = "none"


@dataclass(frozen=True)
class Pack:
    language: str
    labels: Mapping[str, Mapping[str, object]]
    ambiguous_terms: tuple[str, ...]
    affect_terms: tuple[str, ...]
    semantic_floor: float
    semantic_margin: float
    digest: str
    ok: bool = True


_CACHE: dict[str, Pack] = {}


def load_pack(language: str) -> Pack:
    code = str(language or "").strip().lower()
    if code in _CACHE:
        return _CACHE[code]
    path = DEFAULT_DIR / f"{code}.json"
    try:
        raw = path.read_bytes()
        body = json.loads(raw)
        if body.get("version") != SCHEMA_VERSION:
            raise ValueError(f"version must be {SCHEMA_VERSION}")
        labels = body.get("labels")
        if not isinstance(labels, dict):
            raise ValueError("labels must be a mapping")
        pack = Pack(
            language=code,
            labels=labels,
            ambiguous_terms=tuple(str(x) for x in body.get("ambiguous_terms") or ()),
            affect_terms=tuple(str(x) for x in body.get("affect_terms") or ()),
            semantic_floor=_score(body.get("semantic_floor")) or 0.34,
            semantic_margin=_score(body.get("semantic_margin")) or 0.03,
            digest=hashlib.sha256(raw).hexdigest(),
        )
    except (OSError, ValueError, TypeError) as exc:
        log.warning("privacy: no usable pack for %r: %s", code, exc)
        pack = Pack(code, {}, (), (), 1.0, 1.0, "", ok=False)
    _CACHE[code] = pack
    return pack


def _score(value: object) -> float:
    if isinstance(value, (int, float, str)):
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _strings(value: object) -> tuple[object, ...]:
    return tuple(value) if isinstance(value, (list, tuple)) else ()


class PatternDetector:
    """Deterministic regex layer from the versioned language pack."""

    detector = "regex"

    def detect(self, text: str, pack: Pack) -> list[Evidence]:
        found: list[Evidence] = []
        for label, body in pack.labels.items():
            for pattern in _strings(body.get("patterns")):
                try:
                    for match in re.finditer(str(pattern), text):
                        found.append(
                            Evidence(
                                self.detector,
                                f"pack-{SCHEMA_VERSION}",
                                pack.digest,
                                label,
                                1.0,
                                (match.start(), match.end()),
                            )
                        )
                except re.error:
                    log.warning("privacy: invalid pattern for %s", label)
        for term in pack.ambiguous_terms:
            for match in re.finditer(rf"\b{re.escape(term)}\b", text, re.IGNORECASE):
                found.append(
                    Evidence(
                        self.detector,
                        f"pack-{SCHEMA_VERSION}",
                        pack.digest,
                        AMBIGUOUS,
                        0.5,
                        (match.start(), match.end()),
                    )
                )
        return found


class SemanticDetector:
    """Nearest-centroid label layer on the shared MiniLM; abstains when the pack is empty."""

    detector = "encoder"

    @staticmethod
    @cache
    def _centroids(language: str) -> tuple[dict[str, list[float]], str, float, float]:
        pack = load_pack(language)
        rows: dict[str, list[float]] = {}
        texts: list[str] = []
        owners: list[str] = []
        for label, body in pack.labels.items():
            for example in _strings(body.get("examples")):
                text = str(example or "").strip()
                if text:
                    texts.append(text)
                    owners.append(label)
        vectors = embed.encode(texts) if texts else []
        for label in pack.labels:
            label_vectors = [
                vector
                for vector, owner in zip(vectors, owners, strict=False)
                if owner == label
            ]
            if label_vectors:
                dim = len(label_vectors[0])
                rows[label] = [
                    sum(vector[index] for vector in label_vectors) / len(label_vectors)
                    for index in range(dim)
                ]
        return rows, pack.digest, pack.semantic_floor, pack.semantic_margin

    def detect(self, text: str, pack: Pack) -> list[Evidence]:
        centroids, digest, floor, margin = self._centroids(pack.language)
        vector = embed.encode([text])
        if not vector or not centroids:
            return []
        scored = sorted(
            ((embed.cosine(vector[0], centroid), label) for label, centroid in centroids.items()),
            reverse=True,
        )
        best, label = scored[0]
        if best < floor:
            return []
        ambiguous = len(scored) > 1 and best - scored[1][0] < margin
        found = [
            Evidence(self.detector, "centroid-1", digest, label, best, None)
        ]
        if ambiguous:
            found.append(
                Evidence(self.detector, "centroid-1", digest, AMBIGUOUS, best, None)
            )
        return found


DETECTORS = (PatternDetector(), SemanticDetector())


def collect(text: str, language: str) -> tuple[list[Evidence], Pack]:
    pack = load_pack(language)
    body = str(text or "")[:MAX_CLASSIFY_CHARS]
    found: list[Evidence] = []
    if body:
        found.extend(DETECTORS[0].detect(body, pack))
        # A feeling word is not a proposition: run the semantic layer only when the text
        # asserts something beyond affect, or the deterministic layer already found a label.
        lowered = body.lower()
        affected = any(term in lowered for term in pack.affect_terms)
        ambiguous = any(item.label == AMBIGUOUS for item in found)
        sensitive = any(item.label in LEVELS for item in found)
        if not (affected or ambiguous) or sensitive:
            found.extend(DETECTORS[1].detect(body, pack))
    return found, pack


def decide(evidence: Sequence[Evidence]) -> PrivacyDecision:
    """The only level writer: max evidence level, credentials drop, ambiguity asks."""
    labels = [item.label for item in evidence]
    if CREDENTIAL in labels:
        return PrivacyDecision(
            level=LEVELS[CREDENTIAL],
            action="drop",
            needs_review=True,
            ask=False,
            label=CREDENTIAL,
        )
    strongest = max((LEVELS.get(label, 0) for label in labels), default=0)
    sensitive = [label for label in labels if label in LEVELS]
    ambiguous = AMBIGUOUS in labels
    level = max(strongest, 0) if sensitive else 2
    ask = bool(ambiguous or level >= 4)
    if sensitive:
        # deterministic labels win the displayed label; the score stays in evidence
        label = max(sensitive, key=lambda item: LEVELS.get(item, 0))
    else:
        label = AMBIGUOUS if ambiguous else "none"
    return PrivacyDecision(
        level=level,
        action="ask" if ask else "allow",
        needs_review=ask,
        ask=ask,
        label=label,
    )


def classify(text: str, language: str) -> tuple[list[Evidence], PrivacyDecision]:
    evidence, _pack = collect(text, language)
    return evidence, decide(evidence)


def policy_record(decision: PrivacyDecision) -> dict:
    return {
        "level": decision.level,
        "action": decision.action,
        "policy_version": decision.policy_version,
        "policy_hash": decision.policy_hash,
    }
