"""ADR-0010 reflection contract: disputed rows hide, one T2 derivative, no chain, stale."""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import text as sql
from support import build_config, database

from eidesoul.engine.memory import reflect
from eidesoul.engine.memory.store import MemoryStore

THREAD = "628111@s.whatsapp.net"
PERSON = "628111"


def store(tmp_path) -> MemoryStore:
    return MemoryStore(database(), build_config(tmp_path))


def write(memory: MemoryStore, text: str, **kwargs) -> str:
    base = {
        "scope": "person",
        "person_id": PERSON,
        "thread_id": THREAD,
        "owner_id": PERSON,
        "subject_id": PERSON,
        "level": 2,
    }
    base.update(kwargs)
    return memory.write(text, **base)


def rows(memory: MemoryStore) -> list[dict]:
    return memory.db.fetch_all(sql("SELECT * FROM memories ORDER BY created_at"))


def test_opposite_polarity_opens_a_dispute_and_hides_both(tmp_path) -> None:
    memory = store(tmp_path)
    left = write(memory, "aku suka kopi")
    right = write(memory, "aku tidak suka kopi")

    report = asyncio.run(
        reflect.run_pass(memory, scope=f"person:{PERSON}", llm=None)
    )

    assert report.disputes == 1
    states = {str(row["id"]): row["dispute_state"] for row in rows(memory)}
    assert states[left] == "open" and states[right] == "open"
    assert asyncio.run(memory.search("kopi", person_id=PERSON, thread_id=THREAD)) == []
    assert memory.get(left) is None and memory.get(right) is None


def test_duplicates_merge_into_one_t2_derivative_and_never_chain(tmp_path) -> None:
    memory = store(tmp_path)
    write(memory, "aku suka kopi tanpa gula")
    write(memory, "aku suka kopi tanpa gula")

    first = asyncio.run(reflect.run_pass(memory, scope=f"person:{PERSON}", llm=None))
    assert first.writes == 1
    derived = [row for row in rows(memory) if row["via"] == "reflection"]
    assert len(derived) == 1
    assert int(derived[0]["trust"]) == 2
    assert derived[0]["refs"]
    assert derived[0]["state"] == "ok"

    second = asyncio.run(reflect.run_pass(memory, scope=f"person:{PERSON}", llm=None))
    assert second.writes == 0
    assert len([row for row in rows(memory) if row["via"] == "reflection"]) == 1


def test_apply_reflection_refuses_empty_refs_and_a_chain(tmp_path) -> None:
    memory = store(tmp_path)
    source_id = write(memory, "aku suka kopi")
    source = memory.db.fetch_one(sql("SELECT * FROM memories WHERE id = :id"), {"id": source_id})

    with pytest.raises(ValueError, match="refs"):
        memory.apply_reflection(
            "aku suka kopi",
            refs=[],
            sources=[source],
            scope="person",
            person_id=PERSON,
            thread_id=THREAD,
        )

    derived = memory.apply_reflection(
        "aku suka kopi",
        refs=[source_id],
        sources=[source],
        scope="person",
        person_id=PERSON,
        thread_id=THREAD,
        level=2,
    )
    derived_row = memory.db.fetch_one(sql("SELECT * FROM memories WHERE id = :id"), {"id": derived})
    with pytest.raises(ValueError, match="derive_depth"):
        memory.apply_reflection(
            "aku suka kopi",
            refs=[derived],
            sources=[derived_row],
            scope="person",
            person_id=PERSON,
            thread_id=THREAD,
        )
    with pytest.raises(ValueError, match="lower"):
        memory.apply_reflection(
            "aku suka kopi",
            refs=[source_id],
            sources=[source],
            scope="person",
            person_id=PERSON,
            thread_id=THREAD,
            level=1,
        )


def test_stale_dispute_weakens_the_inferred_side(tmp_path) -> None:
    memory = store(tmp_path)
    left = write(memory, "aku suka kopi")
    right = memory.write(
        "aku tidak suka kopi",
        scope="person",
        person_id=PERSON,
        thread_id=THREAD,
        owner_id=PERSON,
        subject_id=PERSON,
        level=2,
        via="reflection",
    )
    assert memory.mark_disputed(left, right, now=time.time() - 40 * 86400) is True

    assert memory.stale_disputes(ttl_days=30) >= 1

    states = {
        str(row["id"]): (row["dispute_state"], row["state"]) for row in rows(memory)
    }
    assert states[left] == ("stale", "ok")
    assert states[right][0] == "stale"
    assert states[right][1] == "staged"
