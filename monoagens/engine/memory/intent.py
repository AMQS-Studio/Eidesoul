"""`remember`/`forget` intent prototypes on the shared embedder (ADR-0005 §4.6).

The classifier embeds the incoming text once, takes the best positive score per intent, and
compares it against the best negative. An intent fires only when its score clears the configured
floor **and** beats every negative by the configured margin, so a question ("do you remember…")
or a negation never triggers a write or a deletion. The built-in bank is English-only; MiniLM's
multilingual space lets it score every catalog language without per-language files.
"""

import json
import logging
from functools import cache
from pathlib import Path

from .. import embed

log = logging.getLogger(__name__)

KINDS = ("remember", "forget")
NEGATIVE = "negative"
FALLBACK = "en"
MIN_PROTOTYPES = 6

# English-only semantic anchors. The shared multilingual embedder maps every language into the
# same vector space, so one canonical bank scores all languages without per-language files.
DEFAULT_BANK: dict[str, tuple[str, ...]] = {
    "remember": (
        "remember that I like black coffee",
        "please keep in mind that I work night shifts",
        "note that my sister lives in Boston",
        "for future reference I hate loud places",
        "don't forget I am allergic to peanuts",
        "I want you to remember my favorite color is blue",
        "keep this in mind: I start work at seven",
        "save this fact about me for later",
    ),
    "forget": (
        "forget that I said I moved to Boston",
        "delete the memory about my old job",
        "please remove what I told you about my ex",
        "stop remembering that I smoke",
        "erase the note about my salary",
        "clear the memory of my previous address",
        "that thing I told you is not true anymore, drop it",
        "you should not keep the fact that I quit",
        "forget my salary",
        "delete what I told you about my phone number",
    ),
    "negative": (
        "what do you remember about me",
        "I forgot what I was going to say",
        "can you remind me tomorrow morning",
        "I can't remember where I put the keys",
        "why did you forget that",
        "how does your memory work",
        "never mind, it does not matter",
        "I forgot my keys at home",
        "what is my name",
        "tell me about yourself",
    ),
}


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
def load(language: str, folder: Path | None = None) -> dict:
    """The built-in English bank, or one custom pack from an explicit test directory."""
    if folder is not None:
        code = str(language or FALLBACK).strip().lower() or FALLBACK
        for wanted in (code, FALLBACK):
            found = _pack(wanted, Path(folder))
            if found:
                return found
    return dict(DEFAULT_BANK)


def _pack(code: str, folder: Path) -> dict:
    """Both intent files of one custom pack, with one merged negative set for the margin test."""
    found: dict[str, tuple[str, ...]] = {}
    negatives: list[str] = []
    for kind in KINDS:
        path = folder / f"{kind}.{code}.json"
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
    """One row per kind of the built-in bank (or one explicit language), for scripts."""
    rows: list[dict] = []
    codes = [str(language)] if language else [FALLBACK]
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
