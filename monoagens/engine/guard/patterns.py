"""Shared safety boundary: crisis, injection, and secrets.

Crisis and sensitive phrases are per-language data in `library/safety/<lang>.json`. The English
net below is the only compiled list, kept as the last line when a pack is missing (ADR-0033
§4.2/§8); every other language reads its own pack and never borrows. Injection phrases come
from `library/lexicon/<language>.json`, so a missing pack simply has none.

Every phrase is matched whole: a keyword nested inside a longer ordinary word never fires, and a
crisis phrase is suppressed when a negation or a figurative idiom owns the same clause
(`dead battery`, `dying of laughter`).
Credentials need a value shape (an explicit `key = value`, a token prefix, or a PEM header), so
ordinary talk about passwords and tokens stays ordinary talk.
"""


import json
import math
import re
from collections import Counter
from collections.abc import Iterable
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
# Figurative uses of a crisis word: the phrase is the idiom, not a statement about dying.
_NET_FIGURATIVE = ("dead battery", "dead phone", "dead tired", "dead end", "dying of laughter",
                   "died laughing", "laughing to death", "laughed to death", "dead on arrival")
_NET_NEGATIONS = ("not", "no", "never", "dont", "don't", "cannot", "can't", "cant", "won't",
                  "wont")

# A credential is a shape, never a bare keyword: `key = value`, a suffixed password, a token
# prefix, or a PEM header. A forgotten password stays ordinary talk.
_CREDENTIAL_DIR = Path(__file__).resolve().parents[2] / "library" / "guard"
_CREDENTIAL_FALLBACK = {
    "strong_keys": (r"api[\s_-]?key",),
    "connectors": ("is", "are"),
}


@lru_cache(maxsize=1)
def _credential_terms() -> dict[str, tuple[str, ...]]:
    """Localized key/connector words are data in `library/guard/credentials.json`."""
    try:
        raw = json.loads((_CREDENTIAL_DIR / "credentials.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    terms = dict(_CREDENTIAL_FALLBACK)
    for key in terms:
        value = raw.get(key) if isinstance(raw, dict) else None
        if isinstance(value, list) and value and all(
            isinstance(item, str) and item for item in value
        ):
            terms[key] = tuple(value)
    return terms


_TERMS = _credential_terms()
_STRONG_KEYS = "(?:" + "|".join(_TERMS["strong_keys"]) + ")"
_SOFT_KEYS = r"(?:token|secret|otp|credential)"
_PASSWORD = r"pass(?:word|wd)"
_CONNECTORS = "(?:" + "|".join(_TERMS["connectors"]) + ")"
_STRONG_VALUE = r"(?:\s*[:=]\s*\S+|\s+\S+)"
_EXPLICIT_VALUE = r"\s*[:=]\s*\S+"
_VALUE_TOKEN = r"[A-Za-z0-9_+/=\-]{12,}"
# Provider and platform credential shapes; shared with the privacy evidence layer so both
# boundaries recognize the same tokens (ADR-0009 §4.1).
TOKEN_SHAPES = (
    r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{20,}",
    r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9_]{20,}",
    r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{20,}",
    r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}",
    r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_\-]{35}",
    r"(?<![A-Za-z0-9])xox[baprs]-[0-9A-Za-z\-]{10,}",
    r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s:@/]+:[^\s@/]+@",
)
_TOKEN_SHAPE = "(?:" + "|".join(TOKEN_SHAPES) + ")"

# A long unbroken value after `=`/`:` is a secret when it has enough character classes and
# per-character entropy; ordinary words, URLs, paths, and UUIDs stay below the floor.
_ENTROPY_FLOOR = 4.2
_ASSIGNED_VALUE = re.compile(r"[=:]\s*([A-Za-z0-9+/=_\-]{24,})")
_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _shannon_entropy(token: str) -> float:
    """Bits per character: uniform random text scores high, natural language scores low."""
    if not token:
        return 0.0
    counts = Counter(token)
    total = len(token)
    return -sum(
        (count / total) * math.log2(count / total) for count in counts.values()
    )


def _char_classes(token: str) -> int:
    """How many of upper/lower/digit/symbol the token uses."""
    return sum(
        (
            any(char.isupper() for char in token),
            any(char.islower() for char in token),
            any(char.isdigit() for char in token),
            any(not char.isalnum() for char in token),
        )
    )


def entropy_secret(text: str) -> list[tuple[int, int]]:
    """Spans of assignment values that look like random secrets, never ordinary prose."""
    found: list[tuple[int, int]] = []
    for match in _ASSIGNED_VALUE.finditer(str(text or "")):
        token = match.group(1)
        if "://" in token or "/" in token or _UUID.fullmatch(token):
            continue
        if _char_classes(token) < 3 or _shannon_entropy(token) < _ENTROPY_FLOOR:
            continue
        found.append((match.start(1), match.end(1)))
    return found

# One crisis span is suppressed only by a negation or an idiom in the same clause.
_NEGATION_WINDOW = 32
_IDIOM_WINDOW = 12
_CLAUSE_MARKS = ",;.!?"


def _bounded(items: Iterable[object]) -> re.Pattern[str] | None:
    """One whole-phrase alternation; a phrase starting or ending in punctuation keeps its edge."""
    phrases = sorted(
        {str(item).strip().casefold() for item in items if str(item).strip()},
        key=len,
        reverse=True,
    )
    parts: list[str] = []
    for phrase in phrases:
        escaped = re.escape(phrase)
        if phrase[:1].isalnum() and phrase[-1:].isalnum():
            parts.append(rf"\b{escaped}\b")
        else:
            parts.append(escaped)
    if not parts:
        return None
    return re.compile("|".join(parts), re.IGNORECASE)


CRISIS = _bounded(_NET)
# The conservative redaction net for logs and derived text: a bare mention is enough there.
SENSITIVE = re.compile(
    "|".join(rf"\b{re.escape(item)}\b" for item in _NET_SENSITIVE), re.IGNORECASE
)
# The precise check for capture: a credential must have a value shape.
_SHAPES = re.compile(
    rf"\b{_STRONG_KEYS}\b{_STRONG_VALUE}"
    rf"|\b{_PASSWORD}\w{{1,2}}\s+\S+"
    rf"|\b{_PASSWORD}\b(?:\s*[:=]\s*\S+|\s+{_CONNECTORS}\s+\S+)"
    rf"|\b{_SOFT_KEYS}\b(?:{_EXPLICIT_VALUE}|\s+{_VALUE_TOKEN})"
    rf"|{_TOKEN_SHAPE}",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Safety:
    """One language's phrases; `ok` is false when the pack is missing."""

    language: str
    crisis: tuple[str, ...] = ()
    sensitive: tuple[str, ...] = ()
    figurative: tuple[str, ...] = ()
    ok: bool = True


def directory() -> Path:
    return _OVERRIDE or SAFETY_DIR


def configure(folder: str | Path | None = None) -> None:
    """Point the guard at another pack directory; a test seam, never a runtime setting."""
    global _OVERRIDE
    _OVERRIDE = Path(folder).expanduser() if folder else None
    _pack.cache_clear()
    _crisis.cache_clear()
    _sensitive.cache_clear()
    _figurative.cache_clear()


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
        figurative = tuple(
            str(item).strip().casefold() for item in body.get("figurative") or ()
        )
        return Safety(
            language=code, crisis=crisis, sensitive=sensitive, figurative=figurative
        )
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


@lru_cache(maxsize=16)
def _crisis(code: str, folder: str) -> re.Pattern[str] | None:
    pack = _pack(code, folder)
    return _bounded(pack.crisis or _NET)


@lru_cache(maxsize=16)
def _sensitive(code: str, folder: str) -> re.Pattern[str] | None:
    pack = _pack(code, folder)
    phrases = _bounded(pack.sensitive)
    if phrases is None:
        return None
    return re.compile(rf"(?:{phrases.pattern}){_EXPLICIT_VALUE}", re.IGNORECASE)


@lru_cache(maxsize=16)
def _figurative(code: str, folder: str) -> re.Pattern[str] | None:
    pack = _pack(code, folder)
    return _bounded(pack.figurative or _NET_FIGURATIVE)


@lru_cache(maxsize=16)
def _negation(code: str, folder: str, stamp: int) -> re.Pattern[str] | None:
    words = lexicon.load(code).negations or frozenset(_NET_NEGATIONS)
    if not words:
        return None
    body = "|".join(rf"\b{re.escape(word)}\b" for word in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?:{body})(?:\s+\w+){{0,2}}\s*$", re.IGNORECASE)


@lru_cache(maxsize=8)
def _injection(language: str, folder: str, stamp: int) -> re.Pattern[str] | None:
    # `folder`/`stamp` are part of the cache key: a reconfigured or reloaded lexicon never serves
    # a pattern compiled from the previous pack.
    phrases = lexicon.load(language).injection
    if not phrases:
        return None
    body = "|".join(re.escape(item) for item in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.IGNORECASE)


def injection(language: str) -> re.Pattern[str] | None:
    """Compiled injection pattern for one language; None when its pack carries no phrases."""
    code = str(language or "").strip().lower()
    if not code:
        return None
    return _injection(code, str(lexicon.directory()), lexicon.generation())


def _clause(text: str, start: int, end: int) -> tuple[int, int]:
    """The punctuation-delimited clause around a span; an idiom never reaches past it."""
    left = max(text.rfind(mark, 0, start) for mark in _CLAUSE_MARKS) + 1
    right = min(
        (found for mark in _CLAUSE_MARKS if (found := text.find(mark, end)) >= 0),
        default=len(text),
    )
    return left, right


def _suppressed(
    text: str,
    start: int,
    end: int,
    idioms: re.Pattern[str] | None,
    negation: re.Pattern[str] | None,
) -> bool:
    """A figurative idiom in the clause, or a negation just before the phrase, owns the match."""
    left, right = _clause(text, start, end)
    if idioms is not None:
        for idiom in idioms.finditer(text):
            if idiom.end() < start - _IDIOM_WINDOW or idiom.start() > end + _IDIOM_WINDOW:
                continue
            if idiom.start() >= left and idiom.end() <= right:
                return True
    if negation is not None:
        window = text[max(left, start - _NEGATION_WINDOW):start]
        if negation.search(window):
            return True
    return False


def is_sensitive(text: str, language: str) -> bool:
    """A credential shape, a token prefix, a PEM header, an entropy secret, or a pack pair."""
    body = str(text or "")
    if not body:
        return False
    if _SHAPES.search(body) or entropy_secret(body):
        return True
    code = str(language or "").strip().lower()
    pattern = _sensitive(code, str(directory())) if code else None
    return bool(pattern and pattern.search(body))


def is_crisis(text: str, language: str) -> bool:
    """Crisis detection reads the language pack; the English net is the last line."""
    body = " ".join(str(text or "").split())
    if not body:
        return False
    code = str(language or "").strip().lower()
    folder = str(directory())
    if code:
        idioms = _figurative(code, folder)
        negation = _negation(code, str(lexicon.directory()), lexicon.generation())
        patterns = (_crisis(code, folder), CRISIS)
    else:
        idioms = _bounded(_NET_FIGURATIVE)
        negation = None
        patterns = (CRISIS,)
    for pattern in patterns:
        if pattern is None:
            continue
        for match in pattern.finditer(body):
            if _suppressed(body, match.start(), match.end(), idioms, negation):
                continue
            return True
    return False


def is_injection(text: str, language: str) -> bool:
    pattern = injection(language)
    return bool(pattern and pattern.search(str(text or "")))


def safe_text(text: str, limit: int = 400, *, language: str) -> str:
    """Flatten text before storing it; secrets and injection attempts never reach memory."""
    flat = " ".join(str(text or "").split())
    if not flat or is_sensitive(flat, language) or is_injection(flat, language):
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
