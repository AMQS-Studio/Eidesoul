"""Shared safety boundary: crisis, injection, and secrets.

Crisis and sensitive phrases are per-language data in `library/safety/<lang>.json`. The English
net below is the only compiled list, kept as the last line when a pack is missing (ADR-0033
§4.2/§8); every other language reads its own pack and never borrows. Injection phrases come
from `library/lexicon/<language>.json`, so a missing pack simply has none.
"""


import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..character import lexicon

SCHEMA_VERSION = 1
SAFETY_DIR = Path(__file__).resolve().parents[2] / "library" / "safety"
_OVERRIDE: Path | None = None
_NET = ("self-harm", "self harm", "suicide", "kill myself", "end my life", "want to die",
        "hurt myself", "overdose", "hang myself")
_NET_SENSITIVE = ("api key", "api_key", "api-key", "password", "passwd", "token", "secret",
                  "otp", "credential")
CRISIS = re.compile("|".join(re.escape(item) for item in _NET), re.IGNORECASE)
SENSITIVE = re.compile(
    "|".join(re.escape(item) for item in _NET_SENSITIVE), re.IGNORECASE
)


@dataclass(frozen=True)
class Safety:
    """One language's crisis and sensitive phrases; `ok` is false when the pack is missing."""

    language: str
    crisis: tuple[str, ...] = ()
    sensitive: tuple[str, ...] = ()
    ok: bool = True


def directory() -> Path:
    return _OVERRIDE or SAFETY_DIR


def configure(folder: str | Path | None = None) -> None:
    """Point the guard at another pack directory; a test seam, never a runtime setting."""
    global _OVERRIDE
    _OVERRIDE = Path(folder).expanduser() if folder else None
    _pack.cache_clear()


@lru_cache(maxsize=8)
def _pack(code: str, folder: str) -> Safety:
    path = Path(folder) / f"{code}.json"
    if not path.is_file():
        return Safety(language=code, ok=False)
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
        if body.get("version") != SCHEMA_VERSION or str(body.get("language")) != code:
            return Safety(language=code, ok=False)
        crisis = tuple(str(item).strip().casefold() for item in body.get("crisis") or ())
        sensitive = tuple(str(item).strip().casefold() for item in body.get("sensitive") or ())
        return Safety(language=code, crisis=crisis, sensitive=sensitive)
    except (OSError, ValueError):
        return Safety(language=code, ok=False)


def safety(language: str) -> Safety:
    """The pack for one catalog code, loaded lazily and cached; `ok` false when absent."""
    code = str(language or "").strip().lower()
    if not code:
        return Safety(language="", ok=False)
    return _pack(code, str(directory()))


def has_pack(language: str) -> bool:
    return safety(language).ok


@lru_cache(maxsize=8)
def _injection(language: str, folder: str, stamp: int) -> re.Pattern[str] | None:
    # `folder`/`stamp` are part of the cache key: a reconfigured or reloaded lexicon never serves
    # a pattern compiled from the previous pack.
    phrases = lexicon.load(language).injection
    if not phrases:
        return None
    body = "|".join(re.escape(item) for item in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{body})", re.IGNORECASE)


def injection(language: str) -> re.Pattern[str] | None:
    """Compiled injection pattern for one language; None when its pack carries no phrases."""
    code = str(language or "").strip().lower()
    if not code:
        return None
    return _injection(code, str(lexicon.directory()), lexicon.generation())


def is_crisis(text: str, language: str) -> bool:
    """Crisis detection reads the language pack; the English net is the last line."""
    body = str(text or "")
    pack = safety(language)
    phrases = pack.crisis or tuple(item.casefold() for item in _NET)
    if any(phrase in body.casefold() for phrase in phrases):
        return True
    return bool(CRISIS.search(body))


def is_injection(text: str, language: str) -> bool:
    pattern = injection(language)
    return bool(pattern and pattern.search(str(text or "")))


def safe_text(text: str, limit: int = 400, *, language: str) -> str:
    """Flatten text before storing it; secrets and injection attempts never reach memory."""
    flat = " ".join(str(text or "").split())
    phrases = safety(language).sensitive
    secret = any(phrase in flat.casefold() for phrase in phrases) or SENSITIVE.search(flat)
    if not flat or secret or is_injection(flat, language):
        return ""
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


CROSS_WORDS = 8
CROSS_MIN_CHARS = 40


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\w@#]+", str(text or "").casefold())


def cross_chat_leak(reply: str, sources: list[str]) -> bool:
    """True when the reply contains a verbatim quote from a cross-room read."""
    body = " ".join(_tokens(reply))
    if not body:
        return False
    for source in sources[:50]:
        norm = " ".join(_tokens(str(source)[:600]))
        if len(norm) >= CROSS_MIN_CHARS and norm in body:
            return True
        words = norm.split()
        if len(words) >= CROSS_WORDS:
            for index in range(len(words) - CROSS_WORDS + 1):
                if " ".join(words[index : index + CROSS_WORDS]) in body:
                    return True
    return False
