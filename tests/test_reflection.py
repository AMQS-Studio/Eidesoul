"""ADR-0010 reflection contract: disputed rows hide, one T2 derivative, no chain, stale."""


import asyncio
import time

import pytest
from sqlalchemy import text as sql
from support import build_config, database

from monoagens.engine.memory import reflect
from monoagens.engine.memory.store import MemoryStore

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
    left = write(memory, "I like coffee")
    right = write(memory, "I do not like coffee")

    report = asyncio.run(
        reflect.run_pass(memory, scope=f"person:{PERSON}", llm=None, language="id")
    )

    assert report.disputes == 1
    states = {str(row["id"]): row["dispute_state"] for row in rows(memory)}
    assert states[left] == "open"
    assert states[right] == "open"
    assert asyncio.run(memory.search("kopi", person_id=PERSON, thread_id=THREAD)) == []
    assert memory.get(left) is None
    assert memory.get(right) is None


def test_duplicates_merge_into_one_t2_derivative_and_never_chain(tmp_path) -> None:
    memory = store(tmp_path)
    write(memory, "aku suka kopi tanpa gula")
    write(memory, "aku suka kopi tanpa gula")

    first = asyncio.run(reflect.run_pass(memory, scope=f"person:{PERSON}", llm=None, language="id"))
    assert first.writes == 1
    derived = [row for row in rows(memory) if row["via"] == "reflection"]
    assert len(derived) == 1
    assert int(derived[0]["trust"]) == 2
    assert derived[0]["refs"]
    assert derived[0]["state"] == "ok"

    second = asyncio.run(
        reflect.run_pass(memory, scope=f"person:{PERSON}", llm=None, language="id")
    )
    assert second.writes == 0
    assert len([row for row in rows(memory) if row["via"] == "reflection"]) == 1


def test_apply_reflection_refuses_empty_refs_and_a_chain(tmp_path) -> None:
    memory = store(tmp_path)
    source_id = write(memory, "I like coffee")
    source = memory.db.fetch_one(sql("SELECT * FROM memories WHERE id = :id"), {"id": source_id})

    with pytest.raises(ValueError, match="refs"):
        memory.apply_reflection(
            "I like coffee",
            refs=[],
            sources=[source],
            scope="person",
            person_id=PERSON,
            thread_id=THREAD,
        )

    derived = memory.apply_reflection(
        "I like coffee",
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
            "I like coffee",
            refs=[derived],
            sources=[derived_row],
            scope="person",
            person_id=PERSON,
            thread_id=THREAD,
        )
    with pytest.raises(ValueError, match="lower"):
        memory.apply_reflection(
            "I like coffee",
            refs=[source_id],
            sources=[source],
            scope="person",
            person_id=PERSON,
            thread_id=THREAD,
            level=1,
        )


def test_stale_dispute_weakens_the_inferred_side(tmp_path) -> None:
    memory = store(tmp_path)
    left = write(memory, "I like coffee")
    right = memory.write(
        "I do not like coffee",
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


def test_a_pass_reports_the_pairs_it_opened(tmp_path) -> None:
    """ADR-0010 §4.7: the pass hands its opened pairs to whoever asks the question."""
    memory = store(tmp_path)
    left = write(memory, "I like coffee")
    right = write(memory, "I do not like coffee")

    report = asyncio.run(
        reflect.run_pass(memory, scope=f"person:{PERSON}", llm=None, language="id")
    )

    assert report.disputes == 1
    assert len(report.opened) == 1
    pair = report.opened[0]
    assert set(pair["ids"]) == {left, right}
    assert {pair["first"], pair["second"]} == {"I like coffee", "I do not like coffee"}
    assert pair["subject_id"] == PERSON

    open_pairs = memory.open_disputes()
    assert len(open_pairs) == 1
    assert set(open_pairs[0]["ids"]) == {left, right}
    assert memory.disputes_asked_today(PERSON) == 0
    assert memory.mark_dispute_asked(pair["ids"]) == 2
    assert memory.disputes_asked_today(PERSON) == 1


def test_the_worker_asks_one_question_per_pass(tmp_path) -> None:
    from monoagens.engine.jobs.cognition import CognitionWorker, Job

    memory = store(tmp_path)
    write(memory, "I like coffee")
    write(memory, "I do not like coffee")
    asked: list[tuple[str, dict]] = []
    worker = CognitionWorker(
        database(),
        build_config(tmp_path),
        memory,
        ask_dispute=lambda subject, pair: asked.append((subject, pair)),
    )

    result = asyncio.run(
        worker._reflect(
            Job(
                id="j1",
                kind="reflect",
                scope=f"person:{PERSON}",
                payload_ref="",
                attempts=1,
                idempotency_key="reflect:test",
            )
        )
    )

    assert "disputes 1" in result
    assert len(asked) == 1
    assert asked[0][0] == PERSON
    assert memory.disputes_asked_today(PERSON) == 0  # asking is the agent's side effect


def _row(rid: str, text: str) -> dict:
    return {
        "id": rid,
        "text": text,
        "scope": "person",
        "person_id": PERSON,
        "thread_id": THREAD,
        "subject_id": PERSON,
        "kind": "fact",
        "state": "ok",
    }


def test_a_negation_pair_in_another_language_is_a_candidate() -> None:
    rows = [_row("a", "Pengguna suka tema gelap"), _row("b", "Pengguna tidak suka tema gelap")]

    pairs = reflect.candidates(
        rows,
        language="id",
        vectors={"a": [1.0, 0.0], "b": [0.8, 0.6]},
        axis=[0.2, -0.6],
    )

    assert [pair.rule for pair in pairs] == ["vector_opposite"]


def test_a_same_attribute_update_is_a_candidate_without_a_negation() -> None:
    rows = [
        _row("a", "My favorite color is blue"),
        _row("b", "My favorite color is green"),
    ]

    pairs = reflect.candidates(
        rows,
        language="en",
        vectors={"a": [1.0, 0.0], "b": [0.7, 0.7]},
        axis=[1.0, 0.0],
    )

    assert [pair.rule for pair in pairs] == ["similar_claim"]


def test_unrelated_or_identical_statements_are_not_candidates() -> None:
    unrelated = [_row("a", "I have a cat named Milo"), _row("b", "I work at a bank")]
    assert (
        reflect.candidates(
            unrelated,
            language="en",
            vectors={"a": [1.0, 0.0], "b": [0.0, 1.0]},
            axis=[1.0, 0.0],
        )
        == []
    )

    identical = [_row("c", "I like coffee"), _row("d", "I like coffee")]
    assert (
        reflect.candidates(
            identical,
            language="en",
            vectors={"c": [1.0, 0.0], "d": [1.0, 0.0]},
            axis=[1.0, 0.0],
        )
        == []
    )


def test_a_pass_opens_a_dispute_only_the_vector_space_finds(tmp_path, monkeypatch) -> None:
    from monoagens.engine import embed

    memory = store(tmp_path)
    left = write(memory, "Pengguna suka tema gelap")
    right = write(memory, "Pengguna tidak suka tema gelap")
    positive = {pair[0] for pair in reflect.POLARITY_ANCHORS}
    negative = {pair[1] for pair in reflect.POLARITY_ANCHORS}
    space = {
        "Pengguna suka tema gelap": [1.0, 0.0, 0.0],
        "Pengguna tidak suka tema gelap": [0.8, 0.6, 0.0],
    }

    def fake(texts, **kwargs):
        found = []
        for text in texts:
            if text in positive:
                found.append([1.0, 0.0, 0.0])
            elif text in negative:
                found.append([0.0, 1.0, 0.0])
            else:
                found.append(space[text])
        return found

    monkeypatch.setattr(embed, "encode", fake)

    report = asyncio.run(
        reflect.run_pass(memory, scope=f"person:{PERSON}", llm=None, language="id")
    )

    assert report.candidates == 1
    assert report.disputes == 1
    assert set(report.opened[0]["ids"]) == {left, right}
