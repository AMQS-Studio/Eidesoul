"""Shared safety boundary: crisis, injection, and secrets.

The crisis and secret patterns are safety nets that must run even when a language pack is
missing, so they stay here. Injection phrases are language data and come from
`library/lexicon/<language>.json`; a missing pack simply has none, so only these
language-independent patterns run.
"""


import re
from functools import lru_cache

from .. import lexicon

CRISIS = re.compile(
    r"(bunuh diri|mau mati|pengen mati|ingin mati|self[ -]?harm|nyakitin diri|overdosis|gantung diri)",
    re.IGNORECASE,
)
SENSITIVE = re.compile(r"(api[\s_-]?key|password|passwd|token|secret|otp|kredensial)", re.IGNORECASE)


@lru_cache(maxsize=8)
def _injection(language: str, folder: str, stamp: int) -> re.Pattern[str] | None:
    # `folder`/`stamp` are part of the cache key: a reconfigured or reloaded lexicon never serves
    # a pattern compiled from the previous pack.
    phrases = lexicon.load(language).injection
    if not phrases:
        return None
    body = "|".join(re.escape(item) for item in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{body})", re.IGNORECASE)


def injection(language: str = "id") -> re.Pattern[str] | None:
    """Compiled injection pattern for one language; None when its pack carries no phrases."""
    code = str(language or "").strip().lower()
    if not code:
        return None
    return _injection(code, str(lexicon.directory()), lexicon.generation())


def is_crisis(text: str) -> bool:
    return bool(CRISIS.search(str(text or "")))


def is_injection(text: str, language: str = "id") -> bool:
    pattern = injection(language)
    return bool(pattern and pattern.search(str(text or "")))


def safe_text(text: str, limit: int = 400, *, language: str = "id") -> str:
    """Flatten text before storing it; secrets and injection attempts never reach memory."""
    flat = " ".join(str(text or "").split())
    if not flat or SENSITIVE.search(flat) or is_injection(flat, language):
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
