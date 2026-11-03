"""ADR-0016: the Alembic chain is the schema path, the primary is the only migrator."""

from __future__ import annotations

import time

import pytest
from sqlalchemy import inspect, select

from eidesoul.engine.db import SCHEMA_VERSION, Database, MigrationError
from eidesoul.engine.schema import engine_lease, schema_meta


def _tables(db: Database) -> list[str]:
    return inspect(db.engine).get_table_names()


def test_upgrade_creates_every_table_and_stamps_the_head(tmp_path) -> None:
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'upgrade.db'}")
    try:
        assert db.is_empty()
        assert db.upgrade() == SCHEMA_VERSION
        names = _tables(db)
        for expected in ("messages", "chats", "engine_lease", "schema_meta", "alembic_version"):
            assert expected in names
        version = db.scalar(select(schema_meta.c.value).where(schema_meta.c.key == "version"))
        assert str(version) == str(SCHEMA_VERSION)
        head = db.scalar(select(schema_meta.c.value).where(schema_meta.c.key == "version"))
        assert str(head) == str(SCHEMA_VERSION)
    finally:
        db.dispose()


def test_upgrade_is_idempotent(tmp_path) -> None:
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'twice.db'}")
    try:
        db.upgrade()
        before = set(_tables(db))
        db.upgrade()
        assert set(_tables(db)) == before
    finally:
        db.dispose()


def test_a_live_primary_refuses_to_migrate(tmp_path) -> None:
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'busy.db'}")
    try:
        db.create_all()
        with db.connect() as conn:
            db.upsert(
                conn,
                engine_lease,
                [
                    {
                        "id": 1,
                        "instance_id": "other",
                        "host": "elsewhere",
                        "pid": 1,
                        "mode": "primary",
                        "started_at": time.time(),
                        "heartbeat_at": time.time(),
                    }
                ],
                ["id"],
            )
        with pytest.raises(MigrationError, match="primary is alive"):
            db.upgrade()
    finally:
        db.dispose()


def test_a_stale_lease_does_not_block(tmp_path) -> None:
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'stale.db'}")
    try:
        db.create_all()
        with db.connect() as conn:
            db.upsert(
                conn,
                engine_lease,
                [
                    {
                        "id": 1,
                        "instance_id": "old",
                        "host": "elsewhere",
                        "pid": 1,
                        "mode": "primary",
                        "started_at": 0.0,
                        "heartbeat_at": 0.0,
                    }
                ],
                ["id"],
            )
        assert db.upgrade() == SCHEMA_VERSION
    finally:
        db.dispose()
