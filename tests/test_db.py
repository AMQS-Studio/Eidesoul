"""Database contract: idempotent schema, upsert, state, and LIKE search."""


import time
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url
from support import database
from support import test_dsn as store_dsn

from monoagens.engine.db import SCHEMA_VERSION, Database, redact_dsn
from monoagens.engine.schema import documents, messages, schema_meta, state
from monoagens.engine.stores.history import HistoryStore


def test_ensure_is_idempotent_on_the_migrated_schema(tmp_path) -> None:
    db = database()
    db.ensure()
    db.ensure()
    found = db.fetch_all(select(state))
    assert found == []
    control = db.fetch_all(select(documents))
    assert control == []


def test_upsert_insert_then_update(tmp_path) -> None:
    db = database()
    with db.connect() as conn:
        db.upsert(
            conn, documents, [{"name": "engine", "content": "a", "updated_at": 1.0}], ["name"]
        )
    with db.connect() as conn:
        db.upsert(
            conn, documents, [{"name": "engine", "content": "b", "updated_at": 2.0}], ["name"]
        )
    assert db.fetch_all(select(documents)) == [
        {"name": "engine", "content": "b", "updated_at": 2.0}
    ]


def test_upsert_partial_update_keeps_other_columns(tmp_path) -> None:
    db = database()
    with db.connect() as conn:
        db.upsert(conn, documents, [{"name": "soul", "content": "x", "updated_at": 1.0}], ["name"])
    with db.connect() as conn:
        db.upsert(
            conn,
            documents,
            [{"name": "soul", "content": "y", "updated_at": 1.0}],
            ["name"],
            update={"content": "y"},
        )
    row = db.fetch_one(select(documents).where(documents.c.name == "soul"))
    assert row["content"] == "y"
    assert row["updated_at"] == 1.0


def test_upsert_chunks_postgres_parameter_limit() -> None:
    db = database()
    rows = [
        {
            "id": i,
            "thread_id": "chat",
            "msg_id": f"m{i}",
            "ts": float(i),
            "sender_id": "",
            "sender_name": "",
            "sender_address": "",
            "text": "x",
            "outgoing": False,
            "author": "unknown",
            "visibility": 2,
            "data": {},
        }
        for i in range(5500)
    ]
    seen: list[int] = []

    class FakeConnection:
        dialect = SimpleNamespace(name="postgresql")

        def execute(self, statement):
            compiled = statement.compile(dialect=postgresql.dialect())
            seen.append(len(compiled.params))
            return SimpleNamespace(rowcount=500)

    try:
        total = db.upsert(FakeConnection(), messages, rows, ["id"])
    finally:
        db.dispose()

    assert total == 5500
    assert len(seen) == 11
    assert max(seen) == sum(1 for c in messages.columns if c.computed is None) * 500
    assert max(seen) <= 65535


def test_state_roundtrip(tmp_path) -> None:
    db = database()
    with db.connect() as conn:
        db.upsert(
            conn,
            state,
            [{"key": "style", "value": '{"seeded": true}', "updated_at": 3.0}],
            ["key"],
        )
    row = db.fetch_one(select(state).where(state.c.key == "style"))
    assert row["value"] == '{"seeded": true}'


def test_search_is_ilike_per_term(tmp_path) -> None:
    db = database()
    store = HistoryStore(db)
    from monoagens.engine.turn.context import Message

    def message(mid: str, text: str, when: datetime) -> Message:
        return Message(id=mid, sender_name="Budi", sender_id="1", when=when, text=text)

    store.add("chat", message("a", "Rekap RAPAT logistik", datetime(2026, 1, 1, 9)))
    store.add("chat", message("b", "jadwal ronda", datetime(2026, 1, 1, 10)))
    store.add("chat", message("c", "rapat panitia", datetime(2026, 1, 1, 11)))

    hits = store.search("chat", "rapat logistik")
    assert [hit.id for hit in hits] == ["a"]
    assert [hit.id for hit in store.search("chat", "RAPAT")] == ["c", "a"]
    assert store.message_count() == 3


def test_search_time_window_and_other_chat_isolation(tmp_path) -> None:
    db = database()
    store = HistoryStore(db)
    from monoagens.engine.turn.context import Message

    def message(mid: str, text: str, stamp: float) -> Message:
        return Message(
            id=mid,
            sender_name="B",
            sender_id="1",
            when=datetime.fromtimestamp(stamp),
            text=text,
        )

    now = time.time()
    store.add("a", message("1", "fakta lama", now - 100))
    store.add("a", message("2", "fakta baru", now))
    store.add("b", message("3", "fakta chat lain", now))
    assert [hit.id for hit in store.search("a", "fakta", after=now - 50)] == ["2"]
    assert [hit.id for hit in store.search("a", "fakta", before=now - 50)] == ["1"]
    assert {hit.id for hit in store.search("a", "")} == {"1", "2"}


def test_redact_dsn_hides_password() -> None:
    label = redact_dsn("postgresql+psycopg://monoagens:secret@db:5432/monoagens?sslmode=require")
    assert "secret" not in label
    assert label.startswith("postgresql://")
    assert "db:5432/monoagens" in label


def test_label_of_the_store_hides_the_password() -> None:
    db = database()
    try:
        assert db.label.startswith("postgresql://")
        assert make_url(store_dsn()).database in db.label
        assert "monoagens:monoagens" not in db.label
    finally:
        db.dispose()


def test_schema_version_is_recorded(tmp_path) -> None:
    db = database()
    db.ensure()
    db.ensure()
    version = db.fetch_one(select(schema_meta).where(schema_meta.c.key == "version"))
    assert version is not None
    assert version["value"] == str(SCHEMA_VERSION)


def test_main_pool_uses_the_configured_size_and_overflow() -> None:
    default = Database(store_dsn())
    try:
        assert default.engine.pool._max_overflow == 0
    finally:
        default.dispose()
    db = Database(store_dsn(), pool_size=1, max_overflow=2)
    try:
        assert db.engine.pool.size() == 1
        assert db.engine.pool._max_overflow == 2
        with db.engine.connect(), db.engine.connect(), db.engine.connect():
            assert db.engine.pool.overflow() == 2
    finally:
        db.dispose()


def test_control_pool_is_separate_and_survives_a_saturated_turn_pool() -> None:
    db = Database(store_dsn(), pool_size=1, max_overflow=0)
    try:
        assert db.control_engine is not db.engine
        assert db.control_engine.pool.size() == 2
        # The only turn-pool slot is busy; the heartbeat path still gets its own connection.
        with db.connect() as held, db.control() as control:
            assert control.execute(text("SELECT 1")).scalar() == 1
            assert (
                control.connection.dbapi_connection
                is not held.connection.dbapi_connection
            )
    finally:
        db.dispose()

