"""Global-workspace style prompt block competition: mandatory blocks win, the rest compete."""

from __future__ import annotations

from . import lexicon
from .context import keyword_set

MANDATORY = (
    "base",
    "character",
    "research",
    "self",
    "roles",
    "safety",
    "away",
    "emotion",
    "vibe",
    "memory",
    "actions",
)
COMPETING = ("topic", "currents", "style", "person_style", "skills")
ORDER = (
    "base",
    "character",
    "research",
    "self",
    "roles",
    "safety",
    "away",
    "emotion",
    "vibe",
    "topic",
    "currents",
    "style",
    "person_style",
    "memory",
    "actions",
    "skills",
)
JOIN = "\n\n"


def block_score(text: str, query: str, bonus: float, *, language: str = "id") -> float:
    """0..1.3: 0.7 * (keyword overlap / 3, max 1) + block bonus."""
    overlap = len(keyword_set(text, language) & keyword_set(query, language))
    return 0.7 * min(1.0, overlap / 3.0) + float(bonus)


def select(
    blocks: dict[str, str],
    query: str = "",
    *,
    bonuses: dict[str, float] | None = None,
    max_chars: int = 9000,
    enabled: bool = True,
    language: str = "id",
) -> tuple[list[tuple[str, str]], list[str]]:
    """(selected blocks in ORDER, names of the dropped blocks).

    Without a language pack the stopwords are unknown, so keyword scoring cannot separate the
    candidates: selection falls back to block length (the longest block is dropped first).
    """
    present = {name: text for name, text in blocks.items() if text.strip()}
    kept = [name for name in ORDER if name in present]
    dropped: list[str] = []
    if not enabled or int(max_chars) <= 0:
        return [(name, present[name]) for name in kept], dropped
    scored = lexicon.load(language).ok
    bonus_map = bonuses or {}

    def weight(name: str) -> tuple[float, int]:
        score = (
            block_score(present[name], query, bonus_map.get(name, 0.0), language=language)
            if scored
            else float(len(present[name]))
        )
        return (score, ORDER.index(name))

    while len(JOIN.join(present[name] for name in kept)) > int(max_chars):
        candidates = [name for name in kept if name in COMPETING]
        if not candidates:
            break  # mandatory blocks are never dropped
        weakest = min(candidates, key=weight)
        kept.remove(weakest)
        dropped.append(weakest)
    return [(name, present[name]) for name in kept], dropped
