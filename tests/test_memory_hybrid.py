"""Native hybrid retrieval (ADR-0051): vector, lexical, RRF fusion, and strict scope.

Every row is read through `memories_readable`; the deterministic stub embedder keeps the test
offline. A vector that must not come from the text is written explicitly.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from support import build_config, database, stub_vector

from monoagens.engine.config import ConfigError
from monoagens.engine.memory import store as store_module
from monoagens.engine.memory.store import MemoryStore

DM_A = "628111@s.whatsapp.net"
DM_B = "628999@s.whatsapp.net"
GROUP_X = "111@g.us"
GROUP_Y = "222@g.us"


def store(tmp_path) -> MemoryStore:
    return MemoryStore(database(), build_config(tmp_path))


def add(memory: MemoryStore, body: str, **kwargs) -> str:
    return asyncio.run(memory.add(body, **kwargs))[0]


def literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{float(item):.6f}" for item in vector) + "]"


def set_embedding(memory: MemoryStore, memory_id: str, vector: list[float] | None) -> None:
    assert memory.db is not None
    with memory.db.connect() as conn:
        conn.execute(
            text("UPDATE memories SET embedding = CAST(:vec AS vector) WHERE id = :id"),
            {"vec": literal(vector) if vector is not None else None, "id": memory_id},
        )


def insert_row(memory: MemoryStore, body: str, *, person_id: str, thread_id: str, **columns) -> str:
    memory_id = str(uuid.uuid4())
    values = {
        "id": memory_id,
        "text": body,
        "scope": "person",
        "person_id": person_id,
        "thread_id": thread_id,
        "created_at": 1.0,
        "updated_at": 1.0,
        **columns,
    }
    names = ", ".join(values)
    params = ", ".join(f":{key}" for key in values)
    assert memory.db is not None
    with memory.db.connect() as conn:
        conn.execute(text(f"INSERT INTO memories ({names}) VALUES ({params})"), values)
    return memory_id


def search(memory: MemoryStore, query: str, **kwargs):
    return asyncio.run(memory.search(query, **kwargs))


def test_vector_only_match_is_found(tmp_path) -> None:
    memory = store(tmp_path)
    remembered = add(
        memory,
        "domisili tetap di kota bandung",
        scope="person",
        person_id="628111",
        thread_id=DM_A,
    )
    set_embedding(memory, remembered, stub_vector("alamat rumah tinggal"))

    hits = search(memory, "alamat rumah tinggal", person_id="628111", thread_id=DM_A)

    assert [hit.id for hit in hits] == [remembered]


def test_lexical_only_match_is_found_without_an_embedding(tmp_path) -> None:
    memory = store(tmp_path)
    remembered = add(
        memory,
        "suka kopi tubruk kental",
        scope="person",
        person_id="628111",
        thread_id=DM_A,
    )
    set_embedding(memory, remembered, None)

    hits = search(memory, "kopi", person_id="628111", thread_id=DM_A)

    assert [hit.id for hit in hits] == [remembered]
    assert hits[0].score > 0


def test_rrf_puts_a_hit_found_by_both_arms_first(tmp_path) -> None:
    memory = store(tmp_path)
    both = add(memory, "kopi arabika enak", scope="person", person_id="1", thread_id=DM_A)
    lexical = add(memory, "kopi arabika robusta", scope="person", person_id="1", thread_id=DM_A)
    vector = add(memory, "minuman pagi", scope="person", person_id="1", thread_id=DM_A)
    set_embedding(memory, both, stub_vector("kopi arabika"))
    set_embedding(memory, lexical, None)
    set_embedding(memory, vector, stub_vector("kopi arabika"))

    hits = search(memory, "kopi arabika", person_id="1", thread_id=DM_A)

    assert hits[0].id == both
    assert {hit.id for hit in hits} == {both, lexical, vector}


def test_person_memory_never_crosses_people_or_threads(tmp_path) -> None:
    memory = store(tmp_path)
    mine = add(memory, "rahasia liburan pulau", scope="person", person_id="A", thread_id=DM_A)
    theirs = add(memory, "rahasia liburan gunung", scope="person", person_id="B", thread_id=DM_B)
    group = add(memory, "tiket konser grup x", scope="thread", thread_id=GROUP_X)

    for_a = search(memory, "rahasia liburan", person_id="A", thread_id=DM_A)
    assert {hit.id for hit in for_a} == {mine}
    for_b = search(memory, "rahasia liburan", person_id="B", thread_id=DM_B)
    assert {hit.id for hit in for_b} == {theirs}

    elsewhere = search(memory, "tiket konser", person_id="A", thread_id=GROUP_Y)
    assert group not in {hit.id for hit in elsewhere}
    inside = search(memory, "tiket konser", person_id="A", thread_id=GROUP_X)
    assert group in {hit.id for hit in inside}


def test_group_captured_person_memory_stays_in_its_thread(tmp_path) -> None:
    memory = store(tmp_path)
    remembered = add(
        memory,
        "kode pos grup x",
        scope="person",
        person_id="A",
        thread_id=GROUP_X,
        audience="group",
    )

    outside = search(memory, "kode pos", person_id="A", thread_id=GROUP_Y)
    inside = search(memory, "kode pos", person_id="A", thread_id=GROUP_X)

    assert remembered not in {hit.id for hit in outside}
    assert remembered in {hit.id for hit in inside}


def test_unreadable_rows_never_appear(tmp_path) -> None:
    memory = store(tmp_path)
    pending = insert_row(memory, "kandidat sinyal rahasia", person_id="A", thread_id=DM_A)
    staged = insert_row(
        memory,
        "kandidat sinyal rahasia kedua",
        person_id="A",
        thread_id=DM_A,
        state="staged",
        level=2,
        confirmed=True,
    )
    vetoed = add(memory, "sinyal rahasia veto", scope="person", person_id="A", thread_id=DM_A)
    assert asyncio.run(memory.veto(vetoed, subject_id="A"))

    hits = search(memory, "sinyal rahasia", person_id="A", thread_id=DM_A)

    assert {hit.id for hit in hits}.isdisjoint({pending, staged, vetoed})


def test_level_cap_is_applied_in_the_query(tmp_path) -> None:
    memory = store(tmp_path)
    secret = add(
        memory, "kode pintu rumah", scope="person", person_id="A", thread_id=DM_A, level=4
    )

    assert search(memory, "kode pintu", person_id="A", thread_id=DM_A, max_level=2) == []
    assert secret in {
        hit.id
        for hit in search(memory, "kode pintu", person_id="A", thread_id=DM_A, max_level=4)
    }


def test_a_non_384_embedder_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store_module, "EMBED_DIM", 512)

    with pytest.raises(ConfigError, match="384"):
        MemoryStore(database(), build_config(tmp_path))
