"""Prompt block competition: pinned blocks win, references compete (ADR-0015, ADR-0034).

The registry owns names, order, and salience weight. This module only answers one question:
which non-pinned, non-stable blocks fit the budget after stable parts have already been placed?
"""

from collections.abc import Sequence
from typing import Any

from ..character import lexicon
from .context import keyword_set

JOIN = "\n\n"


def block_score(text: str, query: str, bonus: float, *, language: str) -> float:
    """0..1.3: 0.7 * (keyword overlap / 3, max 1) + block bonus."""
    overlap = len(keyword_set(text, language) & keyword_set(query, language))
    return 0.7 * min(1.0, overlap / 3.0) + float(bonus)


def select(
    blocks: Sequence[tuple[Any, str]],
    query: str = "",
    *,
    bonuses: dict[str, float] | None = None,
    max_chars: int = 9000,
    enabled: bool = True,
    language: str,
) -> tuple[list[tuple[Any, str]], list[str]]:
    """(selected (part, text) in order, names of dropped blocks).

    A stable part is never passed here. Pinned parts are never dropped. Without a language
    pack the stopwords are unknown, so selection falls back to block length.
    """
    present = [(part, text) for part, text in blocks if text.strip()]
    kept = list(present)
    dropped: list[str] = []
    if not enabled:
        return kept, dropped
    budget = int(max_chars)
    if budget <= 0:
        dropped = [part.name for part, _ in kept if not part.pinned]
        kept = [(part, text) for part, text in kept if part.pinned]
        return kept, dropped
    scored = lexicon.load(language).ok
    bonus_map = bonuses or {}

    def weight(index: int) -> tuple[float, int]:
        part, text = kept[index]
        name = str(part.name)
        bonus = float(getattr(part, "bonus", 0.0)) + float(bonus_map.get(name, 0.0))
        score = block_score(text, query, bonus, language=language) if scored else float(len(text))
        return (score, index)

    while len(JOIN.join(text for _, text in kept)) > budget:
        candidates = [index for index, (part, _) in enumerate(kept) if not part.pinned]
        if not candidates:
            break
        weakest = min(candidates, key=weight)
        part, _ = kept.pop(weakest)
        dropped.append(str(part.name))
    return kept, dropped
