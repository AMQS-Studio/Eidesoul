"""Knowledge layer contract: multi-source facts, picked semantically, separate from patterns."""


import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fakta

from eidesoul.engine.character import knowledge
from eidesoul.engine.turn.prompts import character_block


def test_shipped_facts_pass_schema_and_vectors() -> None:
    body = fakta.load()
    assert fakta.validate(body) == []
    assert fakta.vectors_deficits(body, fakta.VECTORS) == []
    assert len(fakta.rows(body)) >= 40


def test_fact_text_must_start_with_capital() -> None:
    body = fakta.load()
    body["facts"][0]["text"] = "huruf kecil di awal"
    assert "uppercase letter" in "\n".join(fakta.validate(body))


def test_select_facts_finds_uin_mahad() -> None:
    got = knowledge.select_facts(
        3, messages=["uin malang maba wajib mahad berapa lama?"], packs=("indonesia",)
    )
    assert got
    assert any("mondok" in line.lower() for line in got)


def test_select_facts_zero_count_returns_empty() -> None:
    assert (
        knowledge.select_facts(0, messages=["uin malang maba mahad"], packs=("indonesia",)) == []
    )


def test_select_facts_ignores_unrelated_message() -> None:
    assert knowledge.select_facts(3, messages=["wkwk"], packs=("indonesia",)) == []


def test_missing_file_is_empty_not_crash(tmp_path: Path) -> None:
    assert knowledge.load_facts(str(tmp_path / "hilang.json")) == []
    assert knowledge.load_facts(("hilang",)) == []
    assert knowledge.select_facts(3, messages=["uin malang"], packs=("hilang",)) == []


def test_character_block_keeps_facts_separate_from_patterns() -> None:
    block = character_block((0, 0), patterns=["Pola satu."], facts=["Fakta satu."], language="id"
    )
    assert "## Room patterns" in block
    assert "Pola satu." in block
    assert "## Known facts" in block
    assert "Fakta satu." in block
    assert block.index("## Room patterns") < block.index("## Known facts")
