"""ADR-0016: the Alembic chain is the schema path, the primary is the only migrator."""

from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from support import test_dsn as _dsn

from eidesoul.engine.db import SCHEMA_VERSION, Database, MigrationError


def _admin_engine(test_url: str):
    parsed = make_url(test_url)
    admin = parsed.set(database="postgres")
    return create_engine(admin, isolation_level="AUTOCOMMIT")


@pytest.fixture()
def fresh_database() -> str:
    """A throwaway database for the migration tests; dropped after (ADR-0029 §4.2)."""
    base = _dsn()
    name = f"eidesoul_migration_{uuid.uuid4().hex[:12]}"
    with _admin_engine(base).connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    yield make_url(base).set(database=name).render_as_string(hide_password=False)
    with _admin_engine(base).connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def test_upgrade_creates_every_table_and_stamps_the_head(fresh_database) -> None:
    db = Database(fresh_database)
    try:
        assert db.is_empty()
        assert db.upgrade() == SCHEMA_VERSION
        names = inspect(db.engine).get_table_names()
        for expected in ("messages", "threads", "engine_lease", "schema_meta", "alembic_version"):
            assert expected in names
        with db.connect() as conn:
            version = conn.execute(
                text("SELECT value FROM schema_meta WHERE key = 'version'")
            ).scalar()
            head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert str(version) == str(SCHEMA_VERSION)
        assert str(head) == f"{SCHEMA_VERSION:04d}"
    finally:
        db.dispose()


def test_upgrade_is_idempotent(fresh_database) -> None:
    db = Database(fresh_database)
    try:
        db.upgrade()
        before = set(inspect(db.engine).get_table_names())
        db.upgrade()
        assert set(inspect(db.engine).get_table_names()) == before
    finally:
        db.dispose()


def test_a_live_primary_refuses_to_migrate(fresh_database) -> None:
    db = Database(fresh_database)
    try:
        db.upgrade()
        with db.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO engine_lease (id, instance_id, host, pid, mode, generation, "
                    "started_at, heartbeat_at) "
                    "VALUES (1, 'other', 'elsewhere', 1, 'primary', 1, :now, :now) "
                    "ON CONFLICT (id) DO UPDATE SET heartbeat_at = EXCLUDED.heartbeat_at"
                ),
                {"now": time.time()},
            )
        with pytest.raises(MigrationError, match="primary is alive"):
            db.upgrade()
    finally:
        db.dispose()


def test_a_stale_lease_does_not_block(fresh_database) -> None:
    db = Database(fresh_database)
    try:
        db.upgrade()
        with db.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO engine_lease (id, instance_id, host, pid, mode, generation, "
                    "started_at, heartbeat_at) "
                    "VALUES (1, 'old', 'elsewhere', 1, 'primary', 1, 0, 0) "
                    "ON CONFLICT (id) DO UPDATE SET heartbeat_at = 0"
                )
            )
        assert db.upgrade() == SCHEMA_VERSION
    finally:
        db.dispose()
