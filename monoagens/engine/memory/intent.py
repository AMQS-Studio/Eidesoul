"""`remember`/`forget` intent prototypes on the shared embedder (ADR-0005 §4.6).

A pack holds short sentences for one language and one intent; the classifier embeds the incoming
text once, takes the best positive score per intent, and compares it against the best negative.
An intent fires only when its score clears the configured floor **and** beats every negative by
the configured margin, so a question ("do you remember…") or a negation never triggers a write or
a deletion. Nothing here calls a model and nothing here writes memory.
"""


import json
import logging
from functools import cache
from pathlib import Path

from .. import embed

log = logging.getLogger(__name__)

LIBRARY = Path(__file__).resolve().parents[2] / "library" / "intent"
KINDS = ("remember", "forget")
NEGATIVE = "negative"
FALLBACK = "en"
MIN_PROTOTYPES = 6


def _read(path: Path) -> dict:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("intent pack %s is unreadable: %s", path.name, exc)
        return {}
    return body if isinstance(body, dict) else {}


def _sentences(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        " ".join(str(item or "").split()) for item in value if str(item or "").strip()
    )


@cache
def load(language: str) -> dict:
    """One language's pack: `{kind: positives, "negative": (...)}}`; empty when absent.

    A language is complete or falls back to `en` as a whole: half a pack would score `remember`
    against a foreign negative set.
    """
    code = str(language or FALLBACK).strip().lower() or FALLBACK
    for wanted in (code, FALLBACK):
        found = _pack(wanted)
        if found:
            return found
    return {}


def _pack(code: str) -> dict:
    """Both intent files of one language, with one merged negative set for the margin test."""
    found: dict[str, tuple[str, ...]] = {}
    negatives: list[str] = []
    for kind in KINDS:
        path = LIBRARY / f"{kind}.{code}.json"
        if not path.is_file():
            return {}
        body = _read(path)
        positives = _sentences(body.get("prototypes"))
        rejected = _sentences(body.get("negative"))
        if len(positives) < MIN_PROTOTYPES or len(rejected) < MIN_PROTOTYPES:
            return {}
        found[kind] = positives
        negatives.extend(rejected)
    found[NEGATIVE] = tuple(dict.fromkeys(negatives))
    return found


@cache
def _encoded(language: str) -> dict[str, tuple[tuple[float, ...], ...]]:
    """Prototype vectors for one language, encoded once per process."""
    body = load(language)
    if not body:
        return {}
    labels: list[str] = []
    sentences: list[str] = []
    for kind in (*KINDS, NEGATIVE):
        for item in body.get(kind) or ():
            labels.append(kind)
            sentences.append(item)
    vectors = embed.encode(sentences)
    if len(vectors) != len(sentences):
        return {}
    grouped: dict[str, list[tuple[float, ...]]] = {}
    for label, vector in zip(labels, vectors, strict=True):
        grouped.setdefault(label, []).append(tuple(vector))
    return {key: tuple(value) for key, value in grouped.items()}


def classify(text: str, language: str = FALLBACK, settings=None) -> str:
    """`"remember"`, `"forget"`, or `""`; a disabled intent never fires (ADR-0005 §4.6)."""
    body = " ".join(str(text or "").split())
    if not body or settings is None:
        return ""
    encoded = _encoded(language)
    if not encoded:
        return ""
    vectors = embed.encode([body])
    if not vectors:
        return ""
    vector = vectors[0]
    negative = max(
        (embed.cosine(vector, item) for item in encoded.get(NEGATIVE) or ()), default=0.0
    )
    best = ""
    best_score = 0.0
    for kind in KINDS:
        if not bool(getattr(settings, f"{kind}_enabled", False)):
            continue
        floor = float(getattr(settings, f"{kind}_floor", 1.0))
        margin = float(getattr(settings, f"{kind}_margin", 1.0))
        top = max((embed.cosine(vector, item) for item in encoded.get(kind) or ()), default=0.0)
        if top < floor or top - negative < margin or top <= best_score:
            continue
        best, best_score = kind, top
    return best


def stats(language: str = "") -> list[dict]:
    """One row per kind of one language (or every installed language), for `scripts/intent.py`."""
    rows: list[dict] = []
    codes = [str(language)] if language else sorted(
        {path.stem.split(".", 1)[1] for path in LIBRARY.glob("*.json")}
    )
    for code in codes:
        body = load(code)
        for kind in (*KINDS, NEGATIVE):
            rows.append(
                {
                    "language": code,
                    "kind": kind,
                    "prototypes": len(body.get(kind) or ()),
                }
            )
    return rows
