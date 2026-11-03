"""Store-backed test harness: the real Postgres+pgvector store, migrated once (ADR-0029).

Session: read `EIDESOL_TEST_DATABASE_URL`, enable pgvector, run the production migration path.
Test: truncate every table (`RESTART IDENTITY CASCADE`) so no test sees another's rows.
Embedder: a deterministic 384-dimension stub, so no test loads or downloads a model.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from support import StubEncoder, test_dsn

from eidesoul.engine import embed
from eidesoul.engine.db import SCHEMA_VERSION, Database

# mem0 reads this at import time; a test run never phones home (ADR-0029 §4.3).
os.environ["MEM0_TELEMETRY"] = "false"

ENV = "EIDESOL_TEST_DATABASE_URL"
PODMAN = (
    "podman run -d --name eidesoul-test-pg -p 5433:5432 "
    "-e POSTGRES_DB=eidesoul_test -e POSTGRES_USER=eidesoul -e POSTGRES_PASSWORD=eidesoul "
    "docker.io/pgvector/pgvector:pg17"
)
DSN = "postgresql+psycopg://eidesoul:eidesoul@127.0.0.1:5433/eidesoul_test"

# Schema bookkeeping belongs to the migrated fixture, not to a test's data.
KEEP = {"alembic_version", "schema_meta"}


def _require_dsn() -> str:
    try:
        url = test_dsn().strip()
    except KeyError:
        url = ""
    if not url:
        pytest.exit(
            f"{ENV} is not set: the suite runs on Postgres+pgvector (ADR-0029).\n"
            f"Start a throwaway store:\n  {PODMAN}\n"
            f"then export {ENV}='{DSN}'",
            returncode=1,
        )
    return url


def _tables(db: Database) -> list[str]:
    rows = db.fetch_all(
        text(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = current_schema() ORDER BY tablename"
        )
    )
    return [str(row["tablename"]) for row in rows if row["tablename"] not in KEEP]


@pytest.fixture(scope="session")
def test_store() -> Iterator[Database]:
    url = _require_dsn()
    db = Database(url)
    try:
        with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            # The store is a throwaway (ADR-0029 §4.1): start from an empty schema so a
            # rewritten pre-release baseline cannot leave yesterday's tables behind.
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        db.upgrade()
        from eidesoul.gateways import ensure_schemas

        ensure_schemas(db)
        version = db.scalar(text("SELECT version_num FROM alembic_version"))
        assert version is not None and int(version) == SCHEMA_VERSION
        stamped = db.scalar(text("SELECT value FROM schema_meta WHERE key = 'version'"))
        assert str(stamped) == str(SCHEMA_VERSION)
        yield db
    finally:
        db.dispose()


@pytest.fixture(autouse=True)
def clean_store(test_store: Database) -> Iterator[None]:
    """Truncate before every test body; the migration fixture survives."""
    tables = ", ".join(f'"{name}"' for name in _tables(test_store))
    with test_store.connect() as conn:
        if tables:
            conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
        conn.execute(
            text(
                "INSERT INTO engine_control (id, frozen, frozen_by, frozen_at, shutting_down) "
                "VALUES (1, false, '', 0.0, false) "
                "ON CONFLICT (id) DO UPDATE "
                "SET frozen = false, frozen_by = '', frozen_at = 0.0, shutting_down = false"
            )
        )
    yield


@pytest.fixture(autouse=True)
def stub_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """No model download, no network: the suite embeds with the deterministic stub."""
    monkeypatch.setattr(embed, "encoder", lambda model=embed.MODEL: StubEncoder())
