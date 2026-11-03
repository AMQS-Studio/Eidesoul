"""Language packs: per-language word lists and prompt examples from `library/lexicon/`.

A pack is data, not code. It holds the negations, stopwords, assistant tells, injection
phrases, and prompt examples for one catalog code; the engine loads the pack for
`soul.language` lazily and caches it. A missing or invalid pack degrades explicitly and never
borrows another language's list: language-independent guard patterns still run, prompt salience
falls back to length-based selection, and prompt examples are omitted. One warning is logged
and kept for `/status` through `warnings()`.
"""


import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DEFAULT_DIR = Path(__file__).resolve().parents[2] / "library" / "lexicon"
LIST_KEYS = ("negations", "stopwords", "assistant_tells", "injection")


@dataclass(frozen=True)
class Lexicon:
    """One language pack; `ok` is false when the pack loaded in the degraded state."""

    language: str
    negations: frozenset[str] = frozenset()
    stopwords: frozenset[str] = frozenset()
    assistant_tells: frozenset[str] = frozenset()
    injection: frozenset[str] = frozenset()
    prompt_examples: Mapping[str, str] = field(default_factory=dict)
    ok: bool = True

    def example(self, purpose: str) -> str:
        """One prompt example, or "" when this pack omits it (never another language's)."""
        return str(self.prompt_examples.get(purpose) or "").strip()


_CACHE: dict[tuple[str, str], Lexicon] = {}
_OVERRIDE: Path | None = None
_WARNINGS: list[str] = []
_GENERATION = 0


def directory() -> Path:
    """The active pack directory: the test override when set, otherwise the package data."""
    return _OVERRIDE or DEFAULT_DIR


def generation() -> int:
    """Counter bumped by every reload; derived caches key on it so they cannot serve stale data."""
    return _GENERATION


def configure(folder: str | Path | None = None) -> None:
    """Point the loader at another pack directory; the test `engine.language.lexicon_dir` seam."""
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
    """The pack for one catalog code, loaded lazily and cached."""
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
        return _degraded(code, "lexicon: no language code; language-specific data is off")
    path = directory() / f"{code}.json"
    if not path.is_file():
        return _degraded(
            code, f"lexicon: no pack for '{code}' at {path}; language-specific data is off"
        )
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
        return _pack(code, body)
    except (OSError, ValueError) as exc:
        return _degraded(code, f"lexicon: pack {path} is invalid: {exc}")


def _pack(code: str, body: object) -> Lexicon:
    if not isinstance(body, dict):
        raise ValueError("the pack must be a JSON object")
    if body.get("version") != SCHEMA_VERSION:
        raise ValueError(f"version must be {SCHEMA_VERSION}")
    if str(body.get("language") or "") != code:
        raise ValueError(f"language must be '{code}'")
    lists = {key: _words(body.get(key), key) for key in LIST_KEYS}
    examples = body.get("prompt_examples")
    if not isinstance(examples, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in examples.items()
    ):
        raise ValueError("prompt_examples must be a string map")
    return Lexicon(language=code, prompt_examples=dict(examples), **lists)


def _words(value: object, key: str) -> frozenset[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"'{key}' must be a list of non-empty strings")
    return frozenset(item.strip().casefold() for item in value)


def _degraded(code: str, message: str) -> Lexicon:
    log.warning("%s", message)
    if message not in _WARNINGS:
        _WARNINGS.append(message)
    return Lexicon(language=code, ok=False)
