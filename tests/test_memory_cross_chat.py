"""Cross-chat memory contract through canonical reads: person follows, group secrets stay."""

from __future__ import annotations

import asyncio

from support import build_config, database

from eidesoul.engine.memory.store import MemoryStore

DM = "628111@s.whatsapp.net"
GROUP_A = "111@g.us"
GROUP_B = "222@g.us"


def store(tmp_path) -> MemoryStore:
    return MemoryStore(database(), build_config(tmp_path))


def seed(memory: MemoryStore) -> None:
    asyncio.run(
        memory.add("owner suka kopi tanpa gula", scope="person", person_id="628111", thread_id=DM)
    )
    asyncio.run(memory.add("panitia punya grup logistik kopi", scope="global", thread_id=DM))
    asyncio.run(memory.add("rahasia grup A: tiket konser", scope="thread", thread_id=GROUP_A))
    asyncio.run(
        memory.add(
            "orang lain suka teh",
            scope="person",
            person_id="628999",
            thread_id="628999@s.whatsapp.net",
        )
    )


def add(memory: MemoryStore, text: str, **kwargs) -> str:
    return asyncio.run(memory.add(text, **kwargs))[0]


def test_person_memory_follows_owner_across_threads(tmp_path) -> None:
    memory = store(tmp_path)
    seed(memory)

    hits = asyncio.run(memory.search("kopi", person_id="628111", thread_id=GROUP_B))
    texts = {hit.text for hit in hits}
    assert "owner suka kopi tanpa gula" in texts
    assert "panitia punya grup logistik kopi" in texts
    assert "rahasia grup A: tiket konser" not in texts
    assert "orang lain suka teh" not in texts


def test_chat_memory_stays_in_its_chat(tmp_path) -> None:
    memory = store(tmp_path)
    seed(memory)

    inside = asyncio.run(memory.search("tiket", person_id="628111", thread_id=GROUP_A))
    assert "rahasia grup A: tiket konser" in {hit.text for hit in inside}
    outside = asyncio.run(memory.search("tiket", person_id="628111", thread_id=GROUP_B))
    assert "rahasia grup A: tiket konser" not in {hit.text for hit in outside}


def test_stranger_only_gets_global(tmp_path) -> None:
    memory = store(tmp_path)
    seed(memory)

    hits = asyncio.run(memory.search("kopi", person_id="777", thread_id=GROUP_B))
    assert {hit.scope for hit in hits} <= {"global"}


def test_context_renders_scopes_trust_and_dates(tmp_path) -> None:
    memory = store(tmp_path)
    seed(memory)

    block = asyncio.run(
        memory.context(
            person_id="628111", thread_id=GROUP_B, query="kopi", max_items=10, max_chars=2000
        )
    )

    assert block.text.startswith("Long-term memory (cross-chat):")
    assert "owner suka kopi tanpa gula" in block.text
    assert "[person/" in block.text and "[global/" in block.text
    assert "L2 T0" in block.text and "#" in block.text
    assert "orang lain suka teh" not in block.text
    assert "tiket konser" not in block.text


def test_context_respects_char_budget(tmp_path) -> None:
    memory = store(tmp_path)
    asyncio.run(memory.add("x" * 200, scope="global", thread_id=DM))

    block = asyncio.run(
        memory.context(person_id="628111", thread_id=GROUP_B, query="x", max_items=10, max_chars=60)
    )
    assert block.text == ""


def test_group_captured_person_memory_stays_in_that_group(tmp_path) -> None:
    memory = store(tmp_path)
    add(
        memory,
        "rahasia grup: kode pos",
        scope="person",
        person_id="628111",
        thread_id=GROUP_A,
        audience="group",
    )

    inside = asyncio.run(memory.search("kode", person_id="628111", thread_id=GROUP_A))
    outside = asyncio.run(memory.search("kode", person_id="628111", thread_id=GROUP_B))

    assert any("kode pos" in hit.text for hit in inside)
    assert not any("kode pos" in hit.text for hit in outside)


def test_veto_hides_a_row_from_every_read_path(tmp_path) -> None:
    memory = store(tmp_path)
    memory_id = add(
        memory, "fakta sensitif", scope="person", person_id="628111", thread_id=DM
    )

    assert asyncio.run(memory.search("sensitif", person_id="628111", thread_id=DM))
    assert asyncio.run(memory.veto(memory_id, subject_id="628111")) is True

    assert memory.get(memory_id) is None
    assert asyncio.run(memory.search("sensitif", person_id="628111", thread_id=DM)) == []
    assert asyncio.run(memory.list(scope="person", person_id="628111")) == []
    assert asyncio.run(
        memory.context(person_id="628111", thread_id=DM, query="sensitif")
    ).text == ""
