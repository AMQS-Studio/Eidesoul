"""Character library contract: opt-in packs, merging, register filters, soul-driven selection."""


import asyncio
import json
from dataclasses import replace

import pytest
from support import default_soul, inbound_message, make_agent

from eidesoul.engine.character import patterns as pattern_catalog
from eidesoul.engine.character.knowledge import load_facts, select_facts
from eidesoul.engine.character.patterns import load_lexicon, load_patterns, select_patterns
from eidesoul.engine.config import CharacterConfig
from eidesoul.engine.soul import Soul, SoulStyle

DM = "628111@s.whatsapp.net"

ROW = {
    "register": ["neutral"],
    "keys": [],
    "target": "any",
    "min_toxicity": 0,
    "max_toxicity": 4,
    "min_adult": 0,
    "say": [],
    "priority": 1,
    "always": True,
    "source": "curated",
}


def row(ident: str, content: str, **overrides) -> dict:
    return {**ROW, "id": ident, "content": content, **overrides}


def pack(tmp_path, monkeypatch, files: dict[str, dict]):
    """Point the catalog at a temporary library holding the given packs."""
    library = tmp_path / "library"
    library.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (library / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setattr(pattern_catalog, "LIBRARY", library)
    clear()
    return library


def clear() -> None:
    for name in ("load_pack", "load_patterns", "load_lexicon", "_load_vectors"):
        helper = getattr(pattern_catalog, name)
        if hasattr(helper, "cache_clear"):  # a test may monkeypatch one with a plain lambda
            helper.cache_clear()


@pytest.fixture(autouse=True)
def clean_cache():
    clear()
    yield
    clear()


def test_no_packs_means_no_data() -> None:
    assert load_patterns(()) == []
    assert load_lexicon(()) == {"serious": (), "laughter": (), "roast": (), "meme": ()}
    assert load_facts(()) == []
    assert (
        select_patterns(
            3,
            register="neutral",
            limits=(0, 0),
            messages=["halo"],
            language="id",
        )
        == []
    )
    assert select_facts(3, messages=["uin malang maba"]) == []


def test_missing_and_invalid_packs_are_empty() -> None:
    assert load_patterns(("does-not-exist",)) == []
    assert load_patterns(("Bad_Name",)) == []
    assert load_lexicon(("does-not-exist",))["serious"] == ()
    assert load_facts(("does-not-exist",)) == []
    assert load_facts(("Bad_Name",)) == []


def test_shipped_packs_are_selectable() -> None:
    assert load_patterns(("jawatimur",))
    assert load_lexicon(("jawatimur",))["meme"]
    assert load_facts(("indonesia",))


def test_selected_packs_merge_once(tmp_path, monkeypatch) -> None:
    pack(
        tmp_path,
        monkeypatch,
        {
            "alpha": {"version": 2, "model": pattern_catalog.MODEL, "patterns": [row("a", "A.")]},
            "beta": {
                "version": 2,
                "model": pattern_catalog.MODEL,
                "lexicon": {"serious": ["capek"]},
                "patterns": [row("b", "B.")],
            },
        },
    )

    merged = load_patterns(("alpha", "beta"))
    assert [item["id"] for item in merged] == ["a", "b"]
    assert load_lexicon(("alpha", "beta"))["serious"] == ("capek",)
    duplicated = load_patterns(("beta", "beta"))
    assert [item["id"] for item in duplicated] == ["b"]


def test_register_allow_list_filters_rows(tmp_path, monkeypatch) -> None:
    pack(
        tmp_path,
        monkeypatch,
        {
            "trial": {
                "version": 2,
                "model": pattern_catalog.MODEL,
                "patterns": [
                    row("plain", "Banter saja.", register=["banter"]),
                    row("mixed", "Banter dan soal.", register=["banter", "soal"]),
                ],
            }
        },
    )

    allowed = select_patterns(
        4,
        register="banter",
        limits=(0, 0),
        messages=["halo"],
        packs=("trial",),
        registers=("soal",),
     language="id")
    assert allowed == ["Banter dan soal."]
    unfiltered = select_patterns(
        4, register="banter", limits=(0, 0), messages=["halo"], packs=("trial",), language="id"
    )
    assert unfiltered == ["Banter dan soal.", "Banter saja."]


def test_soul_style_drives_agent_selection(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pattern_catalog, "_load_vectors", lambda name: {})

    quiet = make_agent(
        tmp_path,
        soul=Soul(
            language="id", locale="id-ID", timezone="UTC", name="Eidesoul", style=SoulStyle()
        ),
    )
    quiet.agent.config = replace(quiet.agent.config, character=CharacterConfig(enabled=True))
    asyncio.run(quiet.agent.handle(inbound_message("aku capek banget", thread=DM), quiet.gateway))
    assert "## Room patterns" not in quiet.llm.system
    assert "## Known facts" not in quiet.llm.system

    loud_dir = tmp_path / "loud"
    loud_dir.mkdir()
    loud = make_agent(
        loud_dir,
        soul=default_soul(style=SoulStyle(patterns=("jawatimur",), max_patterns=2)),
    )
    loud.agent.config = replace(loud.agent.config, character=CharacterConfig(enabled=True))
    asyncio.run(loud.agent.handle(inbound_message("aku capek banget", thread=DM), loud.gateway))
    assert "## Room patterns" in loud.llm.system
