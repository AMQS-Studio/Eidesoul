"""Canonical memory contract: one write port, the readable view, states, retention, integrity."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import time
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from support import FakeMem0, build_config, database

from eidesoul.engine.context import Message
from eidesoul.engine.memory import MemoryHit
from eidesoul.engine.memory.integrity import TRUST_INFERENCE, TRUST_OTHER, TRUST_SELF
from eidesoul.engine.memory.mem0 import Mem0Backend
from eidesoul.engine.memory.store import MemoryStore
from eidesoul.engine.stores.history import HistoryStore


class FakeIndex:
    available = True
    backend = "fake"

    def __init__(self) -> None:
        self.added: list[dict] = []
        self.deleted: list[str] = []
        self.purged: list[tuple[str, str]] = []

    async def add(self, text: str, **kwargs) -> list[str]:
        self.added.append({"text": text, **kwargs})
        return [f"ext-{len(self.added)}"]

    async def delete(self, external_id: str) -> bool:
        self.deleted.append(external_id)
        return True

    async def purge_library(self, external_id: str = "", text: str = "") -> int:
        self.purged.append((external_id, text))
        return 0

    async def close(self) -> None:
        return None


class FakeExtractor:
    available = True

    def __init__(self, facts: list[MemoryHit] | None = None, error: Exception | None = None):
        self.facts = facts or []
        self.error = error
        self.calls: list[dict] = []

    async def capture(self, messages, **kwargs) -> list[MemoryHit]:
        self.calls.append({"messages": messages, **kwargs})
        if self.error is not None:
            raise self.error
        return list(self.facts)


def store(tmp_path, *, index=None, extractor=None, policy=None, quota=None) -> MemoryStore:
    return MemoryStore(
        database(),
        build_config(tmp_path),
        index=index,
        extractor=extractor,
        policy=policy,
        quota=quota,
    )


def archive(mid: str, text: str, sender: str = "628111") -> Message:
    return Message(
        id=mid,
        sender_name="Budi",
        sender_id=sender,
        when=datetime.now(),
        text=text,
        sender_address=f"{sender}@s.whatsapp.net",
    )


def count(memory: MemoryStore, table: str, where: str = "") -> int:
    clause = f" WHERE {where}" if where else ""
    return int(memory.db.scalar(text(f"SELECT count(*) FROM {table}{clause}")) or 0)


def row(memory: MemoryStore, memory_id: str) -> dict:
    found = memory.db.fetch_one(
        text("SELECT * FROM memories WHERE id = :id"), {"id": memory_id}
    )
    if found is None:
        return None
    return {
        key: str(value) if isinstance(value, uuid.UUID) else value
        for key, value in found.items()
    }


def readable_count(memory: MemoryStore) -> int:
    return int(memory.db.scalar(text("SELECT count(*) FROM memories_readable")) or 0)


# ---------------------------------------------------------------- never stored


def test_level_five_and_credentials_are_never_stored(tmp_path) -> None:
    index = FakeIndex()
    extractor = FakeExtractor()
    memory = store(tmp_path, index=index, extractor=extractor)

    with pytest.raises(ValueError, match="level 5"):
        memory.write("fakta", scope="global", level=5, owner_id="a", subject_id="a")
    with pytest.raises(ValueError, match="secret"):
        memory.write("api_key=abc123", scope="global", level=2, owner_id="a", subject_id="a")
    assert asyncio.run(
        memory.capture(
            [{"role": "user", "content": "password hunter2"}],
            person_id="1",
            thread_id="t",
            audience="private",
            level=2,
        )
    ) == []

    assert count(memory, "memories") == 0
    assert count(memory, "memory_events") == 0
    assert index.added == []
    assert extractor.calls == []


# ---------------------------------------------------------------- states / view


def test_classifier_failure_drops_the_candidate_without_a_level(tmp_path) -> None:
    memory = store(tmp_path)
    history = HistoryStore(memory.db, memories=memory)
    assert history.intake("t", archive("m1", "aku suka kopi"), "628111") is True

    assert asyncio.run(memory.drop_candidate("t", ("m1",), reason="labeling failed")) == 1

    found = memory.db.fetch_one(text("SELECT * FROM memories"))
    assert found["state"] == "failed" and found["level"] is None
    assert found["label_attempts"] == 1
    assert readable_count(memory) == 0
    assert memory.get(str(found["id"])) is None


def test_pending_is_quarantined_until_the_store_labels_it(tmp_path) -> None:
    memory = store(tmp_path)
    history = HistoryStore(memory.db, memories=memory)
    history.intake("t", archive("m1", "aku suka kopi"), "628111")
    pending = memory.db.fetch_one(text("SELECT * FROM memories"))

    assert pending["state"] == "pending" and pending["level"] is None
    assert pending["trust"] == 3
    assert readable_count(memory) == 0
    assert memory.get(str(pending["id"])) is None
    assert asyncio.run(memory.list(scope="person", person_id="628111")) == []


def test_six_states_and_only_legal_edges(tmp_path) -> None:
    memory = store(tmp_path)
    history = HistoryStore(memory.db, memories=memory)

    history.intake("t", archive("m1", "aku suka kopi"), "628111")
    pending = str(memory.db.fetch_one(text("SELECT id FROM memories"))["id"])
    asyncio.run(
        memory.capture(
            [{"role": "user", "content": "aku suka kopi"}],
            person_id="628111",
            thread_id="t",
            audience="private",
            level=2,
            refs=("m1",),
        )
    )
    assert row(memory, pending)["state"] == "ok"
    assert readable_count(memory) == 1
    with pytest.raises(ValueError, match="illegal"):
        asyncio.run(memory.promote(pending))  # ok -> ok is not an edge

    history.intake("t", archive("m2", "aku suka teh"), "628111")
    failed = str(memory.db.fetch_one(text("SELECT id FROM memories WHERE state='pending'"))["id"])
    asyncio.run(memory.drop_candidate("t", ("m2",)))
    assert row(memory, failed)["state"] == "failed"
    assert row(memory, failed)["level"] is None

    history.intake("t", archive("m3", "halo saja"), "628111")
    worthless = str(
        memory.db.fetch_one(text("SELECT id FROM memories WHERE state='pending'"))["id"]
    )
    asyncio.run(memory.discard_candidate("t", ("m3",), state="not_worth"))
    assert row(memory, worthless)["state"] == "not_worth"

    staged = memory.write(
        "fakta cadangan", scope="person", person_id="628111", thread_id="t",
        level=2, owner_id="628111", subject_id="628111", state="staged",
    )
    assert row(memory, staged)["state"] == "staged"
    assert memory.get(staged) is None
    assert asyncio.run(memory.promote(staged)) is True
    assert memory.get(staged) is not None

    replacement = memory.write(
        "aku suka kopi tubruk",
        scope="person",
        person_id="628111",
        thread_id="t",
        kind=str(row(memory, pending)["kind"]),
        level=2,
        owner_id="628111",
        subject_id="628111",
        supersedes=pending,
    )
    old = row(memory, pending)
    assert old["state"] == "superseded" and old["superseded_by"] == replacement
    assert old["level"] == 2 and old["superseded_by"] is not None
    assert memory.get(pending) is None

    with pytest.raises(ValueError, match="illegal"):
        memory.transition(pending, "ok", level=2)


def test_check_constraints_reject_guessed_levels(tmp_path) -> None:
    memory = store(tmp_path)
    history = HistoryStore(memory.db, memories=memory)
    history.intake("t", archive("m1", "aku suka kopi"), "628111")
    pending = str(memory.db.fetch_one(text("SELECT id FROM memories"))["id"])

    with pytest.raises(IntegrityError), memory.db.connect() as conn:
        conn.execute(
            text("UPDATE memories SET state='ok', level=NULL WHERE id=:id"),
            {"id": pending},
        )
    with pytest.raises(IntegrityError), memory.db.connect() as conn:
        conn.execute(
            text("UPDATE memories SET state='pending', level=2 WHERE id=:id"),
            {"id": pending},
        )


# ---------------------------------------------------------------- retention


def test_expired_row_is_purged_and_pinned_row_survives(tmp_path) -> None:
    index = FakeIndex()
    memory = store(tmp_path, index=index)
    expired = asyncio.run(
        memory.add(
            "fakta lama",
            scope="person",
            person_id="1",
            thread_id="t",
            level=2,
            owner_id="1",
            subject_id="1",
        )
    )[0]
    pinned = memory.write(
        "fakta penting", scope="person", person_id="1", thread_id="t", level=2,
        owner_id="1", subject_id="1", pinned=True,
    )
    stale = time.time() - 10 * 86400
    with memory.db.connect() as conn:
        conn.execute(
            text("UPDATE memories SET expires_at=:when WHERE id IN (:a, :b)"),
            {"when": stale, "a": expired, "b": pinned},
        )

    report = asyncio.run(memory.purge(grace_days=0))

    assert report.selected == 1 and report.purged == 1
    assert row(memory, expired) is None
    assert row(memory, pinned) is not None and row(memory, pinned)["pinned"] is True
    tomb = memory.db.fetch_one(
        text("SELECT * FROM memory_tombstones WHERE id=:id"), {"id": expired}
    )
    assert tomb["reason"] == "expired"
    assert tomb["content_hash"] == hashlib.sha256(b"fakta lama").hexdigest()
    assert index.deleted  # the derived index entry was removed first


def test_purge_dry_run_changes_nothing(tmp_path) -> None:
    memory = store(tmp_path)
    memory_id = memory.write(
        "fakta lama", scope="person", person_id="1", thread_id="t", level=2,
        owner_id="1", subject_id="1",
    )
    with memory.db.connect() as conn:
        conn.execute(
            text("UPDATE memories SET expires_at=:when WHERE id=:id"),
            {"when": time.time() - 10 * 86400, "id": memory_id},
        )

    report = asyncio.run(memory.purge(dry_run=True, grace_days=0))

    assert report.selected == 1 and report.purged == 0 and report.dry_run is True
    assert row(memory, memory_id) is not None
    assert count(memory, "memory_tombstones") == 0


def test_use_renews_once_per_delivered_turn_and_aggregates(tmp_path) -> None:
    memory = store(tmp_path)
    memory_id = memory.write(
        "suka kopi", scope="person", person_id="1", thread_id="t", level=2,
        owner_id="1", subject_id="1",
    )
    before = row(memory, memory_id)["expires_at"]

    assert memory.record_uses("turn-1", [memory_id], delivered=True) == 1
    first = row(memory, memory_id)
    assert first["use_count"] == 1 and first["distinct_use_days"] == 1
    assert first["last_used_at"] > 0 and first["expires_at"] > before
    assert memory.record_uses("turn-2", [memory_id], delivered=True) == 1
    second = row(memory, memory_id)
    assert second["use_count"] == 2
    assert second["expires_at"] == first["expires_at"]  # min_interval_hours (12h)
    assert memory.record_uses("turn-2", [memory_id], delivered=True) == 0
    assert memory.record_uses("turn-9", [memory_id], delivered=False) == 0

    yesterday = (datetime.utcnow().date() - timedelta(days=1)).isoformat()
    with memory.db.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO memory_uses (memory_id, day, turn_id) "
                "VALUES (:id, :day, 'old')"
            ),
            {"id": memory_id, "day": yesterday},
        )
    assert memory.record_uses("turn-3", [memory_id], delivered=True) == 1
    assert row(memory, memory_id)["distinct_use_days"] == 2


def test_l4_is_not_renewed_without_pin_or_confirmation(tmp_path) -> None:
    memory = store(tmp_path)
    memory_id = memory.write(
        "rahasia", scope="person", person_id="1", thread_id="t", level=4,
        owner_id="1", subject_id="1", confirmed=False,
    )
    before = row(memory, memory_id)["expires_at"]

    memory.record_uses("turn-1", [memory_id], delivered=True)

    found = row(memory, memory_id)
    assert found["use_count"] == 1
    assert found["expires_at"] == before


def test_purge_removes_the_mem0_library_copy(tmp_path) -> None:
    config = build_config(tmp_path)
    backend = Mem0Backend(config=config, db=None)
    backend._memory = FakeMem0()
    memory = MemoryStore(database(), config, index=backend, extractor=backend)
    memory_id = asyncio.run(
        memory.add("kopi hitam", scope="global", level=0, owner_id="a", subject_id="a")
    )[0]
    mapping = memory.db.fetch_one(
        text("SELECT external_id FROM memory_index WHERE memory_id=:id"), {"id": memory_id}
    )
    external = mapping["external_id"]

    history_db = backend.history_path
    with sqlite3.connect(history_db) as conn:
        conn.execute(
            "CREATE TABLE history (id TEXT PRIMARY KEY, memory_id TEXT, old_memory TEXT, "
            "new_memory TEXT, event TEXT, created_at DATETIME, updated_at DATETIME, "
            "is_deleted INTEGER, actor_id TEXT, role TEXT)"
        )
        conn.execute(
            "CREATE TABLE messages (id TEXT PRIMARY KEY, session_scope TEXT, role TEXT, "
            "content TEXT, name TEXT, created_at DATETIME)"
        )
        conn.execute(
            "INSERT INTO history (id, memory_id, old_memory, new_memory) "
            "VALUES ('h1', :id, 'kopi hitam', 'kopi hitam')",
            {"id": external},
        )
        conn.execute(
            "INSERT INTO messages (id, content) VALUES ('msg1', 'kopi hitam enak')"
        )
        conn.commit()

    with memory.db.connect() as conn:
        conn.execute(
            text("UPDATE memories SET expires_at=:when WHERE id=:id"),
            {"when": time.time() - 10 * 86400, "id": memory_id},
        )
    report = asyncio.run(memory.purge(grace_days=0))

    assert report.purged == 1
    with sqlite3.connect(history_db) as conn:
        assert conn.execute("SELECT count(*) FROM history").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 0


# ---------------------------------------------------------------- integrity


def test_trust_is_deterministic_from_provenance(tmp_path) -> None:
    memory = store(tmp_path)
    own = memory.write(
        "aku suka kopi", scope="person", person_id="1", thread_id="t", level=2,
        owner_id="1", subject_id="1",
    )
    other = memory.write(
        "budi suka teh", scope="person", person_id="1", thread_id="t", level=2,
        owner_id="1", subject_id="2",
    )
    inference = memory.write(
        "ringkasan", scope="thread", thread_id="t", level=2,
        owner_id="1", subject_id="1", via="reflection", source="reflection",
    )
    assert row(memory, own)["trust"] == TRUST_SELF
    assert row(memory, other)["trust"] == TRUST_OTHER
    assert row(memory, inference)["trust"] == TRUST_INFERENCE


def test_supersede_is_reciprocal_and_needs_proof(tmp_path) -> None:
    memory = store(tmp_path)
    old = memory.write(
        "proyek jalan", scope="person", person_id="1", thread_id="t", level=2,
        owner_id="1", subject_id="1", kind="commitment",
    )
    new = memory.write(
        "proyek selesai",
        scope="person",
        person_id="1",
        thread_id="t",
        level=2,
        owner_id="1",
        subject_id="1",
        kind="commitment",
        supersedes=old,
        change="status_change",
        task_status="done",
    )
    before = row(memory, old)
    assert row(memory, new)["supersedes"] == old
    assert before["state"] == "superseded" and before["superseded_by"] == new
    assert before["level"] == 2  # superseded keeps its level and refs
    assert before["expires_at"] > time.time() + 29 * 86400

    with pytest.raises(ValueError, match="stated_by"):
        memory.write(
            "proyek pindah", scope="person", person_id="1", thread_id="t", level=2,
            owner_id="2", subject_id="1", kind="commitment", supersedes=new,
        )


def test_rule_quarantine_and_promotion_requirements(tmp_path) -> None:
    memory = store(tmp_path)
    from_user = memory.write(
        "always be nice", scope="person", person_id="1", thread_id="t", level=2,
        owner_id="1", subject_id="1", kind="rule",
    )
    from_soul = memory.write(
        "always answer in Indonesian", scope="global", level=2, owner_id="soul",
        subject_id="soul", kind="rule", via="soul_doc", source="soul_doc",
        corroboration_days=2,
    )
    from_reflection = memory.write(
        "always ask first", scope="thread", thread_id="t", level=2, owner_id="1",
        subject_id="1", kind="rule", via="reflection", source="reflection",
    )
    assert row(memory, from_user)["kind"] == "fact"
    assert row(memory, from_soul)["kind"] == "rule"
    assert row(memory, from_reflection)["kind"] == "fact"


def test_overflow_goes_to_staged_and_promotes(tmp_path) -> None:
    from eidesoul.engine.memory.integrity import WriteQuota

    memory = store(tmp_path, quota=WriteQuota(per_person_hour=1))
    first = memory.write(
        "satu", scope="person", person_id="1", thread_id="t", level=2,
        owner_id="1", subject_id="1",
    )
    second = memory.write(
        "dua", scope="person", person_id="1", thread_id="t", level=2,
        owner_id="1", subject_id="1",
    )
    assert row(memory, first)["state"] == "ok"
    assert row(memory, second)["state"] == "staged"
    assert memory.get(second) is None
    assert asyncio.run(memory.promote(second)) is True
    assert memory.get(second) is not None


def test_t3_quarantine_and_tombstone_invisibility(tmp_path) -> None:
    index = FakeIndex()
    memory = store(tmp_path, index=index)
    history = HistoryStore(memory.db, memories=memory)
    history.intake("t", archive("m1", "aku suka kopi"), "628111")
    pending = str(memory.db.fetch_one(text("SELECT id FROM memories"))["id"])
    assert row(memory, pending)["trust"] == 3
    assert memory.get(pending) is None
    assert asyncio.run(memory.search("kopi", person_id="628111", thread_id="t")) == []

    live = memory.write(
        "aku suka kopi", scope="person", person_id="628111", thread_id="t", level=2,
        owner_id="628111", subject_id="628111",
    )
    assert memory.get(live) is not None
    assert asyncio.run(memory.forget(live, requester_id="628111", thread_id="t")) is True

    assert memory.get(live) is None
    assert asyncio.run(memory.search("kopi", person_id="628111", thread_id="t")) == []
    assert asyncio.run(memory.list(scope="person", person_id="628111")) == []
    assert asyncio.run(
        memory.context(person_id="628111", thread_id="t", query="kopi")
    ).text == ""
    tomb = memory.db.fetch_one(
        text("SELECT * FROM memory_tombstones WHERE id=:id"), {"id": live}
    )
    assert tomb["reason"] == "forgot" and tomb["content_hash"] == hashlib.sha256(
        b"aku suka kopi"
    ).hexdigest()
    assert count(memory, "memory_events") == 1  # the untouched pending candidate's journal


# ---------------------------------------------------------------- privacy / rekey


def test_set_privacy_and_veto_are_canonical(tmp_path) -> None:
    memory = store(tmp_path)
    memory_id = memory.write(
        "fakta", scope="person", person_id="1", thread_id="t", level=2,
        owner_id="1", subject_id="1",
    )
    assert asyncio.run(memory.set_privacy(memory_id, level=4, confirmed=True)) is True
    found = row(memory, memory_id)
    assert found["level"] == 4 and found["confirmed"] is True
    with pytest.raises(ValueError, match="level 5"):
        asyncio.run(memory.set_privacy(memory_id, level=5))

    assert asyncio.run(memory.veto(memory_id, subject_id="2")) is False
    assert asyncio.run(memory.veto(memory_id, subject_id="1")) is True
    assert memory.get(memory_id) is None
    assert asyncio.run(memory.veto(memory_id, subject_id="1", active=False)) is True
    assert memory.get(memory_id) is not None


def test_rekey_updates_provenance_and_keeps_veto(tmp_path) -> None:
    memory = store(tmp_path)
    memory_id = memory.write(
        "fakta", scope="person", person_id="old", thread_id="t", level=3,
        owner_id="old", subject_id="old",
    )
    asyncio.run(memory.veto(memory_id, subject_id="old"))

    assert asyncio.run(memory.rekey("old", "new")) == 1

    found = row(memory, memory_id)
    assert found["person_id"] == "new"
    assert found["owner_id"] == "new" and found["subject_id"] == "new"
    assert found["vetoed"] is True
    assert found["level"] == 3 and found["confirmed"] is True
