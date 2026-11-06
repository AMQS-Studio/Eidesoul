"""Local privacy evidence and deterministic policy (ADR-0038).

Detectors report *evidence*; only `decide()` writes a level and an action. The pattern layer
is deterministic and versioned with the language pack; the entity layer uses Presidio with the
small multilingual spaCy model; the semantic layer reuses the one MiniLM embedder and classifies
by label centroids. Neither layer sees affect: a sentiment score is not a confidentiality level
(ADR-0038 §4.10).
"""

import hashlib
import importlib.metadata
import importlib.util
import json
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
DEFAULT_DIR = Path(__file__).resolve().parents[2] / "library" / "privacy"
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

# A credential is a value shape, never a keyword: a 16-digit NIK, a provider token prefix, a
# PEM header, a DSN, or a high-entropy assignment value. These run for every language, so a
# pack cannot hide one behind ordinary words.
VALUE_SHAPES = (
    (CREDENTIAL, r"(?<!\d)\d{16}(?!\d)"),
    *((CREDENTIAL, pattern) for pattern in TOKEN_SHAPES),
)
_SHAPES = tuple((label, re.compile(pattern)) for label, pattern in VALUE_SHAPES)
# An ambiguous term needs its context words nearby; the bare word alone is not evidence.
AMBIGUOUS_WINDOW = 40


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
            ambiguous_context=_context(body.get("ambiguous_context")),
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


def _context(value: object) -> dict[str, tuple[str, ...]]:
    """`{term: (word, ...)}`; a malformed entry is dropped, never guessed."""
    if not isinstance(value, dict):
        return {}
    found: dict[str, tuple[str, ...]] = {}
    for term, words in value.items():
        name = str(term or "").strip().casefold()
        wanted = tuple(str(item).strip().casefold() for item in _strings(words))
        if name and wanted:
            found[name] = tuple(item for item in wanted if item)
    return found


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
    """Presidio + small multilingual spaCy NER; spans are evidence, never a verdict."""

    detector = "ner"

    def detect(self, text: str, pack: Pack) -> list[Evidence]:
        analyzer, nlp = _entity_engine()
        version = _entity_version()
        found: dict[tuple[int, int, str], Evidence] = {}
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
        found = [
            Evidence(self.detector, "centroid-1", digest, label, best, None)
        ]
        if ambiguous:
            found.append(
                Evidence(self.detector, "centroid-1", digest, AMBIGUOUS, best, None)
            )
        return found


DETECTORS = (PatternDetector(), EntityDetector(), SemanticDetector())

# ADR-0038 §4.3: a model may raise a level or abstain. The nearest-centroid layer is still
# uncalibrated short-text evidence (ADR-0038 status: the linear head and production calibration
# are the remaining work), so it is recorded for audit but may not set a level or ask the sender
# until that calibration exists. Only the deterministic layer owns the verdict.
POLICY_DETECTORS = ("regex",)


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


def decide(evidence: Sequence[Evidence]) -> PrivacyDecision:
    """The only level writer: max policy evidence level, credentials drop, ambiguity asks."""
    labels = [item.label for item in evidence if item.detector in POLICY_DETECTORS]
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
