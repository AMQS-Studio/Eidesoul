"""Store-backed test harness: the real Postgres+pgvector store, migrated once (ADR-0029).

Session: read `MONOAGENS_TEST_DATABASE_URL`, enable pgvector, run the production migration path.
Test: truncate only the tables that hold rows (`RESTART IDENTITY CASCADE`) so no test sees
another's rows; one server-side pass keeps the truncation atomic for every connection.
Embedder: a deterministic 384-dimension stub, so no test loads or downloads a model.
"""


import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from support import StubEncoder, test_dsn

from monoagens.engine import embed
from monoagens.engine.db import SCHEMA_VERSION, Database

# The plugin tree at the repo root is importable by tests (`plugins.google...`).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ENV = "MONOAGENS_TEST_DATABASE_URL"
PODMAN = (
    "podman run -d --name monoagens-test-pg -p 5433:5432 "
    "-e POSTGRES_DB=monoagens_test -e POSTGRES_USER=monoagens -e POSTGRES_PASSWORD=monoagens "
    "docker.io/pgvector/pgvector:pg17"
)
DSN = "postgresql+psycopg://monoagens:monoagens@127.0.0.1:5433/monoagens_test"

# Schema bookkeeping and the control row belong to the migrated fixture, not to a test's data.
KEEP = {"alembic_version", "schema_meta", "engine_control"}


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


def _quote(name: str) -> str:
    """A table name as a SQL string literal, for the DO block's name array."""
    return "'" + name.replace("'", "''") + "'"


def _clean_sql(tables: tuple[str, ...]) -> str:
    """One DO block: truncate only the tables that hold rows.

    The check and the TRUNCATE run in the caller's transaction, so concurrent connections
    keep seeing either all of the previous test's rows or none of them.
    """
    names = ", ".join(_quote(name) for name in tables)
    return f"""
DO $clean$
DECLARE
    name text;
    dirty text := '';
    has_rows boolean;
BEGIN
    FOREACH name IN ARRAY ARRAY[{names}]::text[]
    LOOP
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM ' || quote_ident(name) || ' LIMIT 1)'
            INTO has_rows;
        IF has_rows THEN
            dirty := dirty || quote_ident(name) || ', ';
        END IF;
    END LOOP;
    IF dirty <> '' THEN
        EXECUTE 'TRUNCATE TABLE ' || rtrim(dirty, ', ')
            || ' RESTART IDENTITY CASCADE';
    END IF;
END
$clean$;
"""


@pytest.fixture(scope="session")
def store_tables(test_store: Database) -> tuple[str, ...]:
    """The migrated public tables, read once: no test adds or drops a table."""
    return tuple(_tables(test_store))


@pytest.fixture(scope="session")
def clean_sql(store_tables: tuple[str, ...]) -> str:
    """The DO block, built once from the session's table list."""
    return _clean_sql(store_tables)


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
        from monoagens.gateways import ensure_schemas

        ensure_schemas(db)
        # Plugin-owned tables appear on first use, not at migration time. Create them now so
        # the session's cached table list includes them and clean_store can truncate them.
        try:
            from plugins.google.google_workspace.tables import ensure as ensure_google
        except ImportError:  # the plugin extra is not installed
            pass
        else:
            ensure_google(db)
        version = db.scalar(text("SELECT version_num FROM alembic_version"))
        assert version is not None
        assert int(version) == SCHEMA_VERSION
        stamped = db.scalar(text("SELECT value FROM schema_meta WHERE key = 'version'"))
        assert str(stamped) == str(SCHEMA_VERSION)
        yield db
    finally:
        db.dispose()


@pytest.fixture(autouse=True)
def isolate_gateway_registry() -> Iterator[None]:
    """A gateway registered by one test must not leak into another test's discovery."""
    from monoagens.gateways.base import registered_gateways, restore_gateways

    snapshot = registered_gateways()
    yield
    restore_gateways(snapshot)


@pytest.fixture(autouse=True)
def clean_store(test_store: Database, clean_sql: str) -> None:
    """Truncate only the tables holding rows; the migration fixture survives."""
    with test_store.connect() as conn:
        conn.execute(text(clean_sql))
        conn.execute(
            text(
                "INSERT INTO engine_control (id, frozen, frozen_by, frozen_at, shutting_down) "
                "VALUES (1, false, '', 0.0, false) "
                "ON CONFLICT (id) DO UPDATE "
                "SET frozen = false, frozen_by = '', frozen_at = 0.0, shutting_down = false"
            )
        )
    return


@pytest.fixture(autouse=True)
def stub_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """No model download, no network: the suite embeds with the deterministic stub."""
    monkeypatch.setattr(embed, "encoder", lambda model=embed.MODEL: StubEncoder())
