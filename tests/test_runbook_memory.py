"""Runbook IV.1-IV.6: credential journal redaction, literal queries, use filtering,
cross-thread forgetting, privacy false positives, and thread-scoped person lookup."""

import asyncio
import time
import uuid

import pytest
from sqlalchemy import insert, text
from support import build_config, database

from monoagens.engine.jobs.cognition_capture import CognitionCapture
from monoagens.engine.memory import gate, queries
from monoagens.engine.memory.evidence import classify
from monoagens.engine.memory.store import MemoryStore
from monoagens.engine.schema import memory_events, people, thread_people
from monoagens.engine.soul import AffectConfig
from monoagens.engine.stores.affect import AffectStore

OWNER = "628111"
CHAT = "120363415422117951@g.us"
OTHER = "120363000000000000@g.us"


def store(tmp_path) -> MemoryStore:
    return MemoryStore(database(), build_config(tmp_path))


def write(memory: MemoryStore, body: str, *, thread_id: str = "t", **kwargs) -> str:
    return memory.write(
        body,
        scope="person",
        person_id=OWNER,
        thread_id=thread_id,
        audience="private",
        level=2,
        owner_id=OWNER,
        subject_id=OWNER,
        **kwargs,
    )


# ------------------------------------------------------------------ IV.1


def test_credential_redaction_also_blanks_the_journal(tmp_path) -> None:
    memory = store(tmp_path)
    memory_id = write(memory, "the candidate looked harmless")
    with memory.db.connect() as conn:
        conn.execute(
            insert(memory_events).values(
                id=uuid.uuid4(),
                memory_id=memory_id,
                content="api_key=abc123",
                content_hash="digest-only",
                created_at=time.time(),
            )
        )

    capture = object.__new__(CognitionCapture)
    capture.memory = memory

    assert capture._redact_credential(memory_id) is True
    assert memory.get(memory_id).text == gate.REDACTED_CREDENTIAL
    journal = memory.db.fetch_one(
        text("SELECT content, content_hash FROM memory_events WHERE memory_id = :id"),
        {"id": memory_id},
    )
    assert journal["content"] == gate.REDACTED_CREDENTIAL
    assert journal["content_hash"] == "digest-only"


# ------------------------------------------------------------------ IV.2


def test_search_query_with_nul_and_like_metacharacters_is_literal(tmp_path) -> None:
    memory = store(tmp_path)
    literal = write(memory, "cat _done")
    write(memory, "cat adone")

    with memory.db.connect() as conn:
        found = queries.match_rows(conn, "person", "%_done\x00", OWNER, "t", 10)

    assert [str(row["id"]) for row in found] == [literal]
    assert queries._like_term("50%") == "%50\\%%"


# ------------------------------------------------------------------ IV.3


def test_record_uses_ignores_malformed_and_purged_ids(tmp_path) -> None:
    memory = store(tmp_path)
    memory_id = write(memory, "suka kopi")

    touched = memory.record_uses(
        "turn-1",
        [memory_id, "not-a-uuid", str(uuid.uuid4()), None],
        delivered=True,
    )

    assert touched == 1
    row = memory.db.fetch_one(
        text("SELECT use_count FROM memories WHERE id = :id"), {"id": memory_id}
    )
    assert int(row["use_count"]) == 1


# ------------------------------------------------------------------ IV.4


def test_forget_intent_reaches_cross_thread_person_rows_and_three_letter_nouns(
    tmp_path,
) -> None:
    memory = store(tmp_path)
    memory_id = write(memory, "aku beli tas baru", thread_id=OTHER)

    removed = asyncio.run(
        memory.forget_by_intent("lupakan tas saya", person_id=OWNER, thread_id="current")
    )

    assert removed == [memory_id]
    assert memory.get(memory_id) is None


def test_forget_intent_keeps_a_one_character_cjk_topic(tmp_path, monkeypatch) -> None:
    """P0-4: `猫` is one token; the old three-character floor erased the request topic."""
    from monoagens.engine import embed

    memory = store(tmp_path)
    vectors = {
        "猫の餌はリビング": [1.0] + [0.0] * 383,
        "今日は晴れです": [0.0, 1.0] + [0.0] * 382,
        "猫を忘れて": [1.0] + [0.0] * 383,
    }
    monkeypatch.setattr(
        embed, "encode", lambda texts, **kwargs: [vectors.get(texts[0], [0.0] * 384)]
    )
    target = write(memory, "猫の餌はリビング")
    write(memory, "今日は晴れです")

    removed = asyncio.run(
        memory.forget_by_intent("猫を忘れて", person_id=OWNER, thread_id="current")
    )

    assert removed == [target]
    assert memory.get(target) is None


def test_forget_tokens_keep_short_cjk_words() -> None:
    from monoagens.engine.memory.records import _tokens

    assert _tokens("猫") == {"猫"}
    assert _tokens("給料はいくら") == {"給料はいくら"}
    assert _tokens("ok") == set()


# ------------------------------------------------------------------ IV.5

BENIGN = (
    ("Maaf ya, tadi aku yang salah", "id"),
    ("Saya tidak suka durian", "id"),
    ("I use a password manager", "en"),
    ("kill the background process", "en"),
    ("hit the gym", "en"),
    ("panic attack", "en"),
    ("work-life balance", "en"),
)

GENUINE = (
    ("I stole from the store", "en", 4),
    ("I do not like him", "en", 3),
    ("I was threatened", "en", 4),
    ("my password is hunter2", "en", 5),
    ("the secret token is here", "en", 5),
    ("I do not like her", "en", 3),
    ("I want to kill him", "en", 4),
    ("he hit me", "en", 4),
    ("my bank account is here", "en", 3),
)


@pytest.mark.parametrize(("body", "language"), BENIGN)
def test_benign_phrases_are_not_private(body: str, language: str) -> None:
    _evidence, decision = classify(body, language)

    assert decision.level < 3
    assert decision.action == "allow"


@pytest.mark.parametrize(("body", "language", "level"), GENUINE)
def test_genuine_phrases_keep_their_level(body: str, language: str, level: int) -> None:
    _evidence, decision = classify(body, language)

    assert decision.level == level


# ------------------------------------------------------------------ IV.6


def test_person_by_name_only_resolves_thread_participants(tmp_path) -> None:
    affect = AffectStore(database(), AffectConfig(enabled=True))
    with affect.db.connect() as conn:
        conn.execute(
            insert(people),
            [
                {"person_id": "628111", "name": "Fahri"},
                {"person_id": "628222", "name": "Sari"},
            ],
        )
        conn.execute(
            insert(thread_people),
            [
                {"thread_id": CHAT, "person_id": "628111", "last_seen": 1.0},
                {"thread_id": OTHER, "person_id": "628222", "last_seen": 1.0},
            ],
        )

    assert affect.person_by_name(CHAT, "Fahri") == "628111"
    assert affect.person_by_name(CHAT, "Sari") is None
    assert affect.person_by_name(OTHER, "Sari") == "628222"
    assert affect.person_by_name("", "Fahri") is None
