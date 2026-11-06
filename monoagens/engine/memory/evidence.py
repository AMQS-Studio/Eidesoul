"""Local privacy evidence and deterministic policy (ADR-0038).

Detectors report *evidence*; only `decide()` writes a level and an action. The pattern layer
is deterministic and versioned with the built-in English policy; the entity layer uses Presidio
with the small multilingual spaCy model plus structural regex fallbacks; the semantic layer
reuses the one MiniLM embedder and classifies by English label centroids. Because MiniLM maps
50+ languages into one vector space, the English anchors serve every language. Neither layer
sees affect: a sentiment score is not a confidentiality level (ADR-0038 §4.10).
"""

import hashlib
import importlib.metadata
import importlib.util
import logging
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from .. import embed
from ..guard.patterns import TOKEN_SHAPES, entropy_secret

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
POLICY_VERSION = "privacy-2026-11-05.2"
POLICY_HASH = hashlib.sha256(POLICY_VERSION.encode("utf-8")).hexdigest()
MAX_CLASSIFY_CHARS = 4000
CREDENTIAL = "credential"
AMBIGUOUS = "ambiguous"
ENTITY = "entity"
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
    ENTITY,
)
LEVELS = {
    "credential": 5,
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

# A credential is a value shape, never a keyword: a 16-digit NIK, a provider token prefix, a
# PEM header, a DSN, or a high-entropy assignment value. These run for every language, so a
# policy cannot hide one behind ordinary words.
VALUE_SHAPES = (
    (CREDENTIAL, r"(?<!\d)\d{16}(?!\d)"),
    *((CREDENTIAL, pattern) for pattern in TOKEN_SHAPES),
)
_SHAPES = tuple((label, re.compile(pattern)) for label, pattern in VALUE_SHAPES)
# An ambiguous term needs its context words nearby; the bare word alone is not evidence.
AMBIGUOUS_WINDOW = 40

# English-only deterministic policy. A single baseline serves every language; the semantic
# layer matches other languages through the shared multilingual embedding space.
_LABELS: dict[str, dict[str, object]] = {
    "credential": {
        "level": 5,
        "patterns": (
            r"(?i)\b(password\w{0,2}|passwd)\b\s*(?:(?:is|are)\s+|[:=]\s*)\S+|\b(?:secret|api)[\s_-]?token\b",
            r"(?i)\b(password|passwd|api[\s_-]?key|token|secret)\b\s*[:=]\s*\S+",
            r"(?i)(?<![A-Za-z0-9])(-----BEGIN [A-Z ]+PRIVATE KEY-----)",
        ),
        "examples": (
            "my password is hunter2",
            "api key: sk-abc",
            "the secret token is here",
        ),
    },
    "health": {
        "level": 4,
        "patterns": (
            r"(?i)\b(sick|illness|diagnosis|hospital|depression|cancer|hiv|pregnant)\b",
        ),
        "examples": (
            "I am sick and going to hospital",
            "the doctor said I am depressed",
            "I am pregnant",
        ),
    },
    "financial": {
        "level": 3,
        "patterns": (
            r"(?i)\b(debt|loan|salary)\b|\b(?:bank|savings|checking)\s+account\b|\b(?:account|card)\s+(?:number|balance|details)\b|\bmy\s+(?:bank|balance|account)\b|\bcredit\s+card\b",
        ),
        "examples": (
            "my bank account is here",
            "I have a large debt",
            "my salary is small",
        ),
    },
    "crime_admission": {
        "level": 4,
        "patterns": (r"(?i)\b(stole|steal|shoplift|fraud|bribe|crime|prison)\b",),
        "examples": (
            "I stole from the shop",
            "I bribed the officer",
            "I committed fraud",
        ),
    },
    "self_harm": {
        "level": 4,
        "patterns": (r"(?i)\b(suicide|kill myself|self[\s-]?harm|hurt myself)\b",),
        "examples": (
            "I want to kill myself",
            "I hurt myself",
            "I think about suicide",
        ),
    },
    "violence": {
        "level": 4,
        "patterns": (
            r"(?i)\b(?:kill|murder)\b\s+"
            r"(?:him|her|them|me|you|us|someone|somebody|people|a person|my\s+\w+)"
            r"|\b(?:hit|punch|attack)\b\s+(?:him|her|them|me|you|us|someone|somebody|people)"
            r"|\bthreaten(?:ed|ing|s)?\b|\b(?:threat|threats)\s+(?:to|against)\b"
            r"|\b(?:violence|violent|assault(?:ed)?)\b",
        ),
        "examples": (
            "I want to kill him",
            "he hit me",
            "I was threatened",
        ),
    },
    "sexual": {
        "level": 4,
        "patterns": (r"(?i)\b(sex|sexual|nude|naked|intimate)\b",),
        "examples": (
            "I have a sexual problem",
            "a naked photo",
            "our intimate life",
        ),
    },
    "relationship_conflict": {
        "level": 3,
        "patterns": (
            r"(?i)\b(hate|fight|conflict|breakup|cheat|affair)\b"
            r"|\b(?:do not|don't)\s+like\s+(?:him|her|them|you|my\s+\w+)\b",
        ),
        "examples": (
            "I hate him",
            "we fought about money",
            "I do not like her",
        ),
    },
    "third_party_personal": {
        "level": 3,
        "patterns": (
            r"(?i)\b(my friend|my partner|my ex|my brother|my sister|my mother|my father)\b",
            r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b",
            r"\+?\d[\d\s-]{8,}\d",
        ),
        "examples": (
            "my friend has a problem",
            "my partner is sick",
            "my mother lives there",
        ),
    },
    "legal": {
        "level": 3,
        "patterns": (r"(?i)\b(lawyer|legal|lawsuit|court|contract)\b",),
        "examples": (
            "I am handling a lawsuit",
            "my lawyer said",
            "this legal problem",
        ),
    },
}
_AMBIGUOUS_TERMS = ("forced", "coerced", "fell")
_AMBIGUOUS_CONTEXT = {
    "forced": ("to", "into", "labour", "labor", "by", "out", "quit"),
    "coerced": ("into", "by", "confession", "sign"),
    "fell": ("from", "down", "off", "stairs", "yesterday", "sick", "asleep"),
}
_AFFECT_TERMS = (
    "sad",
    "angry",
    "happy",
    "upset",
    "afraid",
    "tired",
    "exhausted",
    "ashamed",
    "very",
    "really",
)
_DIGEST = hashlib.sha256(repr(_LABELS).encode("utf-8")).hexdigest()


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
    ambiguous_context: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    ok: bool = True


def load_pack(language: str) -> Pack:
    """The built-in English policy for any catalog code."""
    code = str(language or "").strip().lower() or "en"
    return Pack(
        language=code,
        labels=_LABELS,
        ambiguous_terms=_AMBIGUOUS_TERMS,
        affect_terms=_AFFECT_TERMS,
        semantic_floor=0.34,
        semantic_margin=0.03,
        digest=_DIGEST,
        ambiguous_context=_AMBIGUOUS_CONTEXT,
    )


def _strings(value: object) -> tuple[object, ...]:
    return tuple(value) if isinstance(value, (list, tuple)) else ()


class PatternDetector:
    """Deterministic regex layer from the built-in English policy."""

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
            wanted = pack.ambiguous_context.get(str(term).casefold())
            for match in re.finditer(rf"\b{re.escape(term)}\b", text, re.IGNORECASE):
                if wanted and not _context_near(text, match, wanted):
                    continue
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
        for label, pattern in _SHAPES:
            for match in pattern.finditer(text):
                found.append(
                    Evidence(
                        self.detector,
                        "value-1",
                        pack.digest,
                        label,
                        1.0,
                        (match.start(), match.end()),
                    )
                )
        for start, end in entropy_secret(text):
            found.append(
                Evidence(
                    self.detector,
                    "value-2",
                    pack.digest,
                    CREDENTIAL,
                    1.0,
                    (start, end),
                )
            )
        return found


def _context_near(text: str, match: re.Match, wanted: Sequence[str]) -> bool:
    """One context word within a small window of the ambiguous term, whole-token only."""
    window = text[
        max(0, match.start() - AMBIGUOUS_WINDOW) : match.end() + AMBIGUOUS_WINDOW
    ]
    return any(
        re.search(rf"\b{re.escape(word)}\b", window, re.IGNORECASE) for word in wanted
    )


class PrivacyModelError(RuntimeError):
    """The bundled entity model is missing, unreadable, or not the verified artifact."""


ENTITY_LANGUAGE = "xx"
ENTITY_MODEL = "xx_ent_wiki_sm"
ENTITY_MODEL_VERSION = "3.8.0"
# sha256 of the model tree without `__pycache__`/`.pyc`: config, vocab, tokenizer, and weights.
ENTITY_MODEL_DIGEST = "71c864dc34cdb39a0df38ae2799868bcc056ed98578e203989cff647c1acac54"
ENTITY_MAX_TOKENS = 512
ENTITY_BATCH = 8
ENTITY_OVERLAP = 32
ENTITY_ENTITIES = ("PERSON", "LOCATION", "ORGANIZATION", "NRP")
# Structural PII the NER model may miss, especially in short chat text.
ENTITY_PATTERNS = (
    ("email", re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"\+?\d[\d\s-]{8,}\d")),
    ("card", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")),
    ("ip", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
)


def _entity_model_root() -> Path:
    try:
        spec = importlib.util.find_spec("xx_ent_wiki_sm")
    except (ImportError, ValueError) as exc:
        raise PrivacyModelError("xx_ent_wiki_sm is not installed") from exc
    if spec is None or not spec.submodule_search_locations:
        raise PrivacyModelError("xx_ent_wiki_sm has no package path")
    return Path(next(iter(spec.submodule_search_locations))).resolve()


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file() or "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


@cache
def verify_entities() -> str:
    """Verify the installed model tree before the first inference (ADR-0038 §4.5)."""
    root = _entity_model_root()
    try:
        digest = _tree_digest(root)
    except OSError as exc:
        raise PrivacyModelError(f"cannot read {root}: {exc}") from exc
    if digest != ENTITY_MODEL_DIGEST:
        raise PrivacyModelError(
            f"{ENTITY_MODEL} digest {digest} does not match {ENTITY_MODEL_DIGEST}"
        )
    return digest


class _BlockOptionalModels:
    """Keep torch/transformers out of the privacy import (ADR-0038 §5.1)."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition(".")[0] in {"torch", "transformers"}:
            raise ImportError(f"{fullname} is not part of the privacy stack")
        return None


@contextmanager
def _without_optional_models() -> Iterator[None]:
    blocker = _BlockOptionalModels()
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        sys.meta_path.remove(blocker)


@cache
def _entity_engine() -> tuple[Any, Any]:
    """The one local Presidio analyzer and its vocabulary-free spaCy pipeline."""
    verify_entities()
    try:
        with _without_optional_models():
            import spacy
            from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
            from presidio_analyzer.nlp_engine import SpacyNlpEngine

            class SmallSpacyNlpEngine(SpacyNlpEngine):
                """Load the model's NER weights without its 300 MB vocabulary."""

                def load(self) -> None:
                    self.nlp = {}
                    for model in self.models:
                        self._validate_model_params(model)
                        self._download_spacy_model_if_needed(model["model_name"])
                        vocab = spacy.blank(model["lang_code"]).vocab
                        self.nlp[model["lang_code"]] = spacy.load(
                            model["model_name"], vocab=vocab, exclude=["vocab"]
                        )

            nlp_engine = SmallSpacyNlpEngine(
                models=[{"lang_code": ENTITY_LANGUAGE, "model_name": ENTITY_MODEL}]
            )
            nlp_engine.load()
            registry = RecognizerRegistry(supported_languages=[ENTITY_LANGUAGE])
            registry.add_nlp_recognizer(nlp_engine=nlp_engine)
            analyzer = AnalyzerEngine(
                registry=registry,
                nlp_engine=nlp_engine,
                supported_languages=[ENTITY_LANGUAGE],
            )
            return analyzer, nlp_engine.nlp[ENTITY_LANGUAGE]
    except PrivacyModelError:
        raise
    except Exception as exc:
        raise PrivacyModelError(f"cannot load {ENTITY_MODEL}: {exc}") from exc


def _entity_version() -> str:
    try:
        presidio = importlib.metadata.version("presidio-analyzer")
        spacy = importlib.metadata.version("spacy")
    except importlib.metadata.PackageNotFoundError as exc:
        raise PrivacyModelError("presidio-analyzer or spacy metadata is missing") from exc
    return f"presidio-{presidio}/spacy-{spacy}/{ENTITY_MODEL}-{ENTITY_MODEL_VERSION}/ner-1"


def _segments(nlp, text: str) -> list[tuple[int, int]]:
    """Token-bounded spans with a small overlap so an entity is not cut at a boundary."""
    tokens = list(nlp.make_doc(text))
    if not tokens:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    while start < len(tokens):
        end = min(start + ENTITY_MAX_TOKENS, len(tokens))
        first = tokens[start]
        last = tokens[end - 1]
        spans.append((first.idx, last.idx + len(last.text)))
        if end == len(tokens):
            break
        start = max(start + 1, end - ENTITY_OVERLAP)
    return spans


class EntityDetector:
    """Presidio + small multilingual spaCy NER plus structural regex fallbacks."""

    detector = "ner"

    def detect(self, text: str, pack: Pack) -> list[Evidence]:
        found: dict[tuple[int, int, str], Evidence] = {}
        for label, pattern in ENTITY_PATTERNS:
            for match in pattern.finditer(text):
                span = (match.start(), match.end())
                found[(span[0], span[1], label)] = Evidence(
                    "entity-regex",
                    "regex-1",
                    pack.digest,
                    ENTITY,
                    0.8,
                    span,
                )
        try:
            analyzer, nlp = _entity_engine()
        except PrivacyModelError:
            return list(found.values())
        version = _entity_version()
        spans = _segments(nlp, text)
        for offset in range(0, len(spans), ENTITY_BATCH):
            for start, end in spans[offset : offset + ENTITY_BATCH]:
                results = analyzer.analyze(
                    text=text[start:end],
                    language=ENTITY_LANGUAGE,
                    entities=list(ENTITY_ENTITIES),
                    score_threshold=0.0,
                )
                for item in results:
                    span = (start + item.start, start + item.end)
                    key = (span[0], span[1], item.entity_type)
                    found[key] = Evidence(
                        self.detector,
                        version,
                        ENTITY_MODEL_DIGEST,
                        ENTITY,
                        float(item.score),
                        span,
                    )
        return list(found.values())


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
        found = [Evidence(self.detector, "centroid-1", digest, label, best, None)]
        if ambiguous:
            found.append(
                Evidence(self.detector, "centroid-1", digest, AMBIGUOUS, best, None)
            )
        return found


DETECTORS = (PatternDetector(), EntityDetector(), SemanticDetector())

# ADR-0038 §4.3: a model may raise a level or abstain. Every evidence layer contributes to the
# verdict, so a credential or a sensitive label found by the entity or encoder layer is not
# lost when the regex layer stays silent. A layer that also reports AMBIGUOUS evidence has no
# confident separation between two labels, so its label is audit-only: only a layer whose best
# match clearly wins may set the level.
POLICY_DETECTORS = ("regex",)
MODEL_DETECTORS = ("entity-regex", "ner", "encoder")


def collect(text: str, language: str) -> tuple[list[Evidence], Pack]:
    pack = load_pack(language)
    body = str(text or "")[:MAX_CLASSIFY_CHARS]
    found: list[Evidence] = []
    if body:
        found.extend(DETECTORS[0].detect(body, pack))
        found.extend(DETECTORS[1].detect(body, pack))
        # A feeling word is not a proposition: run the semantic layer only when the text
        # asserts something beyond affect, or the deterministic layer already found a label.
        lowered = body.lower()
        affected = any(term in lowered for term in pack.affect_terms)
        ambiguous = any(item.label == AMBIGUOUS for item in found)
        sensitive = any(item.label in LEVELS for item in found)
        if not (affected or ambiguous) or sensitive:
            found.extend(DETECTORS[2].detect(body, pack))
    return found, pack


def decide(evidence: Sequence[Evidence], floor: float = 0.0) -> PrivacyDecision:
    """The only level writer: max policy evidence level, credentials drop, ambiguity asks.

    `floor` is the pack's semantic floor plus margin: a model layer below it or with an
    AMBIGUOUS sibling has no confident verdict, so its label stays audit-only.
    """
    unsure = {item.detector for item in evidence if item.label == AMBIGUOUS}
    labels = [
        item.label
        for item in evidence
        if item.detector in POLICY_DETECTORS
        or (
            item.detector in MODEL_DETECTORS
            and item.detector not in unsure
            and float(item.score or 0.0) >= floor
        )
    ]
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
    ambiguous = AMBIGUOUS in labels and any(
        item.detector in POLICY_DETECTORS for item in evidence
    )
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
    evidence, pack = collect(text, language)
    return evidence, decide(evidence, pack.semantic_floor + pack.semantic_margin)


def policy_record(decision: PrivacyDecision) -> dict:
    return {
        "level": decision.level,
        "action": decision.action,
        "policy_version": decision.policy_version,
        "policy_hash": decision.policy_hash,
    }
