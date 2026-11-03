"""Resolver contract: the soul text picks packs, ties are refused, existing packs are kept."""


import json

import pytest
import yaml

from eidesoul.admin import library
from eidesoul.engine import knowledge, patterns


def _write(path, body: dict) -> None:
    path.write_text(json.dumps(body), encoding="utf-8")


def pattern_pack(folder, name: str, language: str, words: list[str]) -> None:
    _write(
        folder / f"{name}.json",
        {
            "version": 1,
            "model": patterns.MODEL,
            "language": language,
            "lexicon": {"roast": list(words)},
            "patterns": [],
        },
    )


def fact_pack(folder, name: str, language: str, keys: list[str]) -> None:
    _write(
        folder / f"{name}.json",
        {
            "version": 1,
            "model": knowledge.MODEL,
            "language": language,
            "facts": [{"id": f"{name}-one", "topic": "T1", "keys": list(keys), "text": "t"}],
        },
    )


def clear_caches() -> None:
    library._packs.cache_clear()
    library._kinds.cache_clear()
    patterns.load_pack.cache_clear()
    knowledge.load_pack.cache_clear()


@pytest.fixture
def temp_library(tmp_path, monkeypatch):
    """Point both catalogs at a temporary library and drop the resolver caches."""
    pattern_dir = tmp_path / "patterns"
    fact_dir = tmp_path / "facts"
    pattern_dir.mkdir()
    fact_dir.mkdir()
    monkeypatch.setattr(patterns, "LIBRARY", pattern_dir)
    monkeypatch.setattr(knowledge, "LIBRARY", fact_dir)
    clear_caches()
    yield pattern_dir, fact_dir
    clear_caches()


def test_keyword_tier_accepts_the_pack_whose_tokens_appear(temp_library) -> None:
    pattern_dir, _ = temp_library
    pattern_pack(pattern_dir, "jawatimur", "id", ["cangkrukan", "kopi"])
    pattern_pack(pattern_dir, "bali", "id", ["megibung", "nyepi"])

    found = library.suggest(
        "aku suka cangkrukan sambil ngopi", library.packs_for("id"), use_embedding=False
    )

    assert [(item.pack, item.kind) for item in found] == [("jawatimur", "patterns")]
    assert "cangkrukan" in found[0].reason


def test_one_pack_per_kind_is_accepted(temp_library) -> None:
    pattern_dir, fact_dir = temp_library
    pattern_pack(pattern_dir, "jawatimur", "id", ["cangkrukan"])
    fact_pack(fact_dir, "indonesia", "id", ["surabaya"])

    found = library.suggest(
        "anak surabaya suka cangkrukan", library.packs_for("id"), use_embedding=False
    )

    assert [(item.pack, item.kind) for item in found] == [
        ("jawatimur", "patterns"),
        ("indonesia", "facts"),
    ]


def test_a_tie_is_refused(temp_library) -> None:
    pattern_dir, _ = temp_library
    pattern_pack(pattern_dir, "alpha", "id", ["kopi"])
    pattern_pack(pattern_dir, "beta", "id", ["kopi"])

    assert library.suggest("kopi", library.packs_for("id"), use_embedding=False) == []


def test_the_language_gate_hides_other_languages(temp_library) -> None:
    pattern_dir, _ = temp_library
    pattern_pack(pattern_dir, "jawatimur", "id", ["cangkrukan"])

    assert library.packs_for("id") == {"jawatimur": "cangkrukan"}
    assert library.packs_for("en") == {}


def test_seed_style_falls_back_to_the_first_pack_of_each_kind(temp_library) -> None:
    pattern_dir, fact_dir = temp_library
    pattern_pack(pattern_dir, "bali", "id", ["megibung"])
    pattern_pack(pattern_dir, "jawatimur", "id", ["cangkrukan"])
    fact_pack(fact_dir, "indonesia", "id", ["surabaya"])

    style = library.seed_style("nothing in this text matches", "id")

    assert style.patterns == ("bali",)
    assert style.facts == ("indonesia",)
    assert (style.max_patterns, style.max_facts) == (2, 3)


def test_resolve_soul_keeps_other_keys_and_unmatched_kinds(temp_library, tmp_path) -> None:
    pattern_dir, _ = temp_library
    pattern_pack(pattern_dir, "jawatimur", "id", ["cangkrukan"])
    soul_path = tmp_path / "soul.yaml"
    soul_path.write_text(
        yaml.safe_dump(
            {
                "name": "Eidesoul",
                "language": "id",
                "persona": "tidak ada kata yang cocok",
                "role": "assistant",
                "style": {
                    "patterns": ["lama"],
                    "facts": [],
                    "registers": ["serious"],
                    "max_patterns": 4,
                    "max_facts": 0,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    text, candidates = library.resolve_soul(soul_path, use_embedding=False)

    assert yaml.safe_load(text)["name"] == "Eidesoul"
    assert yaml.safe_load(text)["style"]["patterns"] == ["lama"]  # nothing matched: kept
    assert yaml.safe_load(text)["style"]["registers"] == ["serious"]
    assert yaml.safe_load(text)["style"]["max_patterns"] == 4
    assert [(item.pack, item.kind, item.reason) for item in candidates] == [
        ("lama", "patterns", "kept: no pack matched")
    ]
