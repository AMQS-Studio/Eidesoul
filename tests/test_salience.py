"""Prompt block competition: pinned parts always fit, references compete when tight (ADR-0034)."""

from monoagens.engine.turn.context import keyword_set
from monoagens.engine.turn.prompts import PromptPart
from monoagens.engine.turn.salience import select


def part(name: str, *, pinned: bool = False, bonus: float = 0.0, order: int = 0) -> PromptPart:
    return PromptPart(name, False, order, bonus=bonus, pinned=pinned, text="x")


def names(kept: list[tuple[PromptPart, str]]) -> list[str]:
    return [item.name for item, _ in kept]


def test_all_blocks_fit_are_kept_in_input_order() -> None:
    blocks = [
        (part("base", pinned=True, order=0), "B"),
        (part("memory", order=3), "M"),
        (part("skills", order=4), "S"),
        (part("topic", order=1), "T"),
        (part("emotion", order=2), "E"),
    ]

    kept, dropped = select(blocks, max_chars=9000, language="id")

    assert dropped == []
    assert names(kept) == ["base", "memory", "skills", "topic", "emotion"]


def test_disabled_salience_never_drops_blocks() -> None:
    blocks = [
        (part("base", pinned=True), "b" * 100),
        (part("memory"), "m" * 5000),
        (part("topic"), "t" * 5000),
    ]

    kept, dropped = select(blocks, max_chars=200, enabled=False, language="id")

    assert dropped == []
    assert names(kept) == ["base", "memory", "topic"]


def test_a_zero_budget_means_unlimited_and_drops_nothing() -> None:
    """P2.1: 0 is unlimited, never "drop every volatile block"."""
    blocks = [
        (part("base", pinned=True), "b" * 100),
        (part("memory"), "m" * 5000),
        (part("topic"), "t" * 5000),
    ]

    kept, dropped = select(blocks, max_chars=0, enabled=True, language="id")

    assert dropped == []
    assert names(kept) == ["base", "memory", "topic"]


def test_an_unscored_budget_drops_the_longest_block_first() -> None:
    """Without a language pack the fallback must shed the biggest block, not the smallest."""
    blocks = [
        (part("base", pinned=True), "b" * 10),
        (part("memory"), "m" * 400),
        (part("topic"), "t" * 100),
    ]

    kept, dropped = select(blocks, max_chars=200, language="xx-unknown")

    assert names(kept) == ["base", "topic"]
    assert dropped == ["memory"]


def test_tight_budget_keeps_pinned_blocks_only() -> None:
    blocks = [
        (part("base", pinned=True), "b" * 100),
        (part("emotion"), "e" * 100),
        (part("memory"), "m" * 400),
        (part("topic"), "t" * 400),
    ]

    kept, dropped = select(blocks, max_chars=300, language="id")

    assert names(kept) == ["base"]
    assert set(dropped) == {"emotion", "memory", "topic"}


def test_keyword_overlap_survives_before_unrelated_block() -> None:
    blocks = [
        (part("base", pinned=True), "b" * 10),
        (part("topic"), "hujan " * 5),
        (part("memory"), "kopi " * 5),
    ]

    kept, dropped = select(blocks, "kopi enak", max_chars=50, language="id")

    assert names(kept) == ["base", "memory"]
    assert dropped == ["topic"]


def test_metadata_bonus_keeps_skills_but_never_drops_memory() -> None:
    blocks = [
        (part("base", pinned=True), "b" * 10),
        (part("memory"), "m" * 40),
        (part("skills", bonus=0.2), "s" * 40),
    ]

    kept, dropped = select(blocks, max_chars=55, language="id")

    assert names(kept) == ["base", "skills"]
    assert dropped == ["memory"]


def test_tiebreak_is_stable_when_nothing_matches() -> None:
    blocks = [
        (part("base", pinned=True), "b" * 10),
        (part("topic"), "t" * 40),
        (part("memory"), "m" * 40),
    ]

    kept, dropped = select(blocks, "zzz qqq", max_chars=55, language="id")

    assert names(kept) == ["base", "memory"]
    assert dropped == ["topic"]


def test_keyword_set_skips_stopwords_and_short_words() -> None:
    assert keyword_set("the quick brown foxes jumped", language="en") == {
        "quick",
        "brown",
        "foxes",
        "jumped",
    }
