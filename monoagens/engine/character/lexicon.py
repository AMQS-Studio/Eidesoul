"""Universal English lexicon: prompt word lists and examples with no per-language files.

The engine always has a valid baseline. Language-specific catalog data, when present, is
supplied by the operator through ``engine.language.lexicon_dir`` (a test/customization seam);
the built-in baseline stays English-only so the engine itself contains no non-English
literals. A custom pack that is missing or invalid falls back to the baseline and logs one
warning for ``/status`` through ``warnings()``.
"""

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
LIST_KEYS = (
    "negations",
    "stopwords",
    "assistant_tells",
    "injection",
    "affirmations",
    "laughter",
    "particles",
    "serious_registers",
    "research_time",
    "research_source",
    "research_high_stakes",
    "research_challenge",
)
# ADR-0006 §4.5: the conversational confirmation vocabulary is data, never a literal.
OPTIONAL_LIST_KEYS = ("confirm", "reject", "modify", "filler", "possessives")

# English-only universal baseline. A custom pack may override these lists for one code.
_BASELINE: dict[str, object] = {
    "negations": (
        "not",
        "no",
        "never",
        "dont",
        "don't",
        "cannot",
        "can't",
        "cant",
        "won't",
        "wont",
    ),
    "stopwords": (
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "have",
        "has",
        "had",
        "was",
        "were",
        "are",
        "you",
        "your",
        "yours",
        "just",
        "really",
        "like",
        "but",
        "not",
        "can",
        "could",
        "should",
        "would",
        "will",
        "please",
        "forget",
        "they",
        "them",
        "there",
        "then",
        "than",
        "what",
        "when",
        "which",
        "who",
        "how",
        "about",
        "into",
        "over",
        "under",
        "some",
        "any",
        "all",
    ),
    "assistant_tells": (
        "sure",
        "here are",
        "here is",
        "as an ai",
        "as an assistant",
        "hope this helps",
        "is there anything else",
        "i understand how you feel",
        "i hear you",
        "i'm here for you",
        "in conclusion",
        "overall",
        "happy to help",
    ),
    "injection": (
        "ignore previous",
        "ignore all previous",
        "system prompt",
        "jailbreak",
        "developer mode",
        "from now on you",
    ),
    "affirmations": ("yes", "ok", "okay", "sure", "y", "fine", "proceed", "go ahead"),
    "laughter": ("haha", "hehe", "hihi", "lol"),
    "particles": ("lol", "tbh", "imo", "ngl", "yeah", "well", "like"),
    "serious_registers": ("serious", "comforting", "direct"),
    "privacy_levels": {
        "secret": 4,
        "sensitive": 3,
        "personal": 2,
        "team": 1,
        "public": 0,
    },
    "research_time": (
        "now",
        "today",
        "latest",
        "current",
        "this week",
        "this month",
        "this year",
        "viral",
        "right now",
        "just now",
    ),
    "research_source": (
        "source",
        "verify",
        "fact check",
        "quote",
        "citation",
        "reference",
        "link",
        "evidence",
    ),
    "research_challenge": (
        "how come",
        "why is that",
        "why would",
        "really",
        "are you sure",
        "you sure",
        "serious",
        "that's wrong",
        "thats wrong",
        "wrong",
        "based on what",
        "explain that",
    ),
    "research_high_stakes": (
        "price",
        "schedule",
        "score",
        "result",
        "president",
        "minister",
        "exchange rate",
        "stock",
        "medicine",
        "dose",
        "law",
        "tax",
        "address",
        "contact",
    ),
    "confirm": ("yes", "ok", "okay", "send", "proceed", "go", "sure", "continue", "approve"),
    "reject": ("no", "cancel", "stop", "nope", "abort", "deny"),
    "modify": ("change", "edit", "instead", "revise", "correct"),
    "filler": ("please", "now", "just", "then", "go", "ahead"),
    "possessives": (),
}


@dataclass(frozen=True)
class Lexicon:
    """One language pack; `ok` is false only when the request had no language code."""

    language: str
    negations: frozenset[str] = frozenset()
    stopwords: frozenset[str] = frozenset()
    assistant_tells: frozenset[str] = frozenset()
    injection: frozenset[str] = frozenset()
    affirmations: frozenset[str] = frozenset()
    laughter: frozenset[str] = frozenset()
    particles: frozenset[str] = frozenset()
    serious_registers: frozenset[str] = frozenset()
    research_time: frozenset[str] = frozenset()
    research_source: frozenset[str] = frozenset()
    research_high_stakes: frozenset[str] = frozenset()
    research_challenge: frozenset[str] = frozenset()
    confirm: frozenset[str] = frozenset()
    reject: frozenset[str] = frozenset()
    modify: frozenset[str] = frozenset()
    filler: frozenset[str] = frozenset()
    possessives: frozenset[str] = frozenset()
    privacy_levels: Mapping[str, int] = field(default_factory=dict)
    ok: bool = True


_CACHE: dict[tuple[str, str], Lexicon] = {}
_OVERRIDE: Path | None = None
_WARNINGS: list[str] = []
_GENERATION = 0


def directory() -> Path:
    """The active custom pack directory, or the empty path when only the baseline is used."""
    return _OVERRIDE or Path()


def generation() -> int:
    """Counter bumped by every reload; derived caches key on it so they cannot serve stale data."""
    return _GENERATION


def configure(folder: str | Path | None = None) -> None:
    """Point the loader at an optional custom pack directory; the test seam."""
    global _OVERRIDE
    _OVERRIDE = Path(folder).expanduser() if folder else None
    reload()


def reload() -> None:
    """Drop every cached pack, the warnings, and bump the generation derived caches key on."""
    global _GENERATION
    _CACHE.clear()
    _WARNINGS.clear()
    _GENERATION += 1


def warnings() -> tuple[str, ...]:
    """Degradation messages since the last reload; `/status` shows them."""
    return tuple(_WARNINGS)


def load(language: str) -> Lexicon:
    """The baseline for one catalog code, loaded lazily and cached."""
    code = str(language or "").strip().lower()
    key = (code, str(directory()))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    pack = _read(code)
    _CACHE[key] = pack
    return pack


def _read(code: str) -> Lexicon:
    if not code:
        return _degraded(code, "lexicon: no language code; using the empty baseline")
    path = directory() / f"{code}.json"
    if _OVERRIDE is not None and path.is_file():
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
            return _pack(code, body)
        except (OSError, ValueError) as exc:
            _warn(f"lexicon: custom pack {path} is invalid: {exc}")
    return _baseline(code)


def _baseline(code: str) -> Lexicon:
    lists = {key: _words(_BASELINE.get(key), key) for key in LIST_KEYS}
    extra = {key: _optional_words(_BASELINE.get(key), key) for key in OPTIONAL_LIST_KEYS}
    return Lexicon(
        language=code,
        negations=lists["negations"],
        stopwords=lists["stopwords"],
        assistant_tells=lists["assistant_tells"],
        injection=lists["injection"],
        affirmations=lists["affirmations"],
        laughter=lists["laughter"],
        particles=lists["particles"],
        serious_registers=lists["serious_registers"],
        research_time=lists["research_time"],
        research_source=lists["research_source"],
        research_high_stakes=lists["research_high_stakes"],
        research_challenge=lists["research_challenge"],
        confirm=extra["confirm"],
        reject=extra["reject"],
        modify=extra["modify"],
        filler=extra["filler"],
        possessives=extra["possessives"],
        privacy_levels=_levels(_BASELINE.get("privacy_levels")),
    )


def _pack(code: str, body: object) -> Lexicon:
    if not isinstance(body, dict):
        raise ValueError("the pack must be a JSON object")
    if body.get("version") != SCHEMA_VERSION:
        raise ValueError(f"version must be {SCHEMA_VERSION}")
    if str(body.get("language") or "") != code:
        raise ValueError(f"language must be '{code}'")
    lists = {key: _words(body.get(key), key) for key in LIST_KEYS}
    extra = {key: _optional_words(body.get(key), key) for key in OPTIONAL_LIST_KEYS}
    levels = _levels(body.get("privacy_levels"))
    return Lexicon(
        language=code,
        negations=lists["negations"],
        stopwords=lists["stopwords"],
        assistant_tells=lists["assistant_tells"],
        injection=lists["injection"],
        affirmations=lists["affirmations"],
        laughter=lists["laughter"],
        particles=lists["particles"],
        serious_registers=lists["serious_registers"],
        research_time=lists["research_time"],
        research_source=lists["research_source"],
        research_high_stakes=lists["research_high_stakes"],
        research_challenge=lists["research_challenge"],
        confirm=extra["confirm"],
        reject=extra["reject"],
        modify=extra["modify"],
        filler=extra["filler"],
        possessives=extra["possessives"],
        privacy_levels=levels,
    )


def _words(value: object, key: str) -> frozenset[str]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"'{key}' must be a list of non-empty strings")
    return frozenset(item.strip().casefold() for item in value)


def _optional_words(value: object, key: str) -> frozenset[str]:
    """An optional list: a pack that predates the key degrades to empty, never fails."""
    if value is None:
        return frozenset()
    return _words(value, key)


def _levels(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError("'privacy_levels' must be a word -> level map")
    found: dict[str, int] = {}
    for word, level in value.items():
        if not isinstance(word, str) or not word.strip():
            raise ValueError("'privacy_levels' keys must be non-empty strings")
        if not isinstance(level, int) or not 0 <= level <= 4:
            raise ValueError("'privacy_levels' values must be integers 0-4")
        found[word.strip().casefold()] = level
    return found


def _warn(message: str) -> None:
    log.warning("%s", message)
    if message not in _WARNINGS:
        _WARNINGS.append(message)


def _degraded(code: str, message: str) -> Lexicon:
    _warn(message)
    return Lexicon(language=code, ok=False)
