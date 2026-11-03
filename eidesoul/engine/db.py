"""Single database (Postgres at runtime, SQLite in tests) through SQLAlchemy Core."""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, Table, create_engine, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import make_url

from .schema import engine_control, engine_lease, metadata, schema_meta

log = logging.getLogger(__name__)

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
ADVISORY_LOCK_KEY = 0x45494445  # "EIDE": one migrator at a time
MIGRATION_LOCK_TIMEOUT = 30.0
UPSERT_BATCH_ROWS = 500


def head_version() -> int:
    """The newest numeric revision head; empty/absent chain falls back to 1."""
    numbers: list[int] = []
    for path in (MIGRATIONS / "versions").glob("*.py"):
        head = path.name.split("_", 1)[0]
        if head.isdigit():
            numbers.append(int(head))
    return max(numbers, default=1)


SCHEMA_VERSION = head_version()


class MigrationError(RuntimeError):
    """A migration that must not run here (standby, missing tree, busy lock)."""


def redact_dsn(url: str) -> str:
    """DSN without the password; safe for logs, status, and audits."""
    try:
        parsed = make_url(str(url))
        parsed = parsed.set(password=None)
        parsed = parsed.set(drivername=parsed.get_backend_name())
        return parsed.render_as_string(hide_password=True)
    except Exception:
        return "database"


class Database:
    def __init__(
        self,
        url: str,
        *,
        pool_size: int = 5,
        pool_timeout: float = 30.0,
    ) -> None:
        self.url = str(url)
        if not self.url:
            raise ValueError("database URL is empty")
        self.dialect = make_url(self.url).get_backend_name()
        self.label = redact_dsn(self.url)
        options: dict[str, Any] = {"pool_pre_ping": True, "future": True}
        if self.dialect != "sqlite":
            options.update(pool_size=int(pool_size), pool_timeout=float(pool_timeout))
        else:
            options["connect_args"] = {"check_same_thread": False}
        self.engine: Engine = create_engine(self.url, **options)

    # ------------------------------------------------------------- schema

    def create_all(self) -> None:
        metadata.create_all(self.engine)
        with self.connect() as conn:
            self.upsert(
                conn,
                schema_meta,
                [{"key": "version", "value": str(SCHEMA_VERSION)}],
                ["key"],
                update={"value": str(SCHEMA_VERSION)},
            )
            # Seed the control row without overwriting an active freeze.
            self.upsert(
                conn,
                engine_control,
                [{"id": 1, "frozen": False, "frozen_by": "", "frozen_at": 0.0}],
                ["id"],
                update={"id": engine_control.c.id},
            )

    def ensure_vector(self) -> bool:
        """Enable pgvector best-effort; SQLite does not need it."""
        if self.dialect != "postgresql":
            return False
        try:
            with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            return True
        except Exception as exc:  # missing permission/pgvector: Mem0 is unavailable, not a crash
            log.warning("pgvector could not be enabled: %s", exc)
            return False

    def is_empty(self) -> bool:
        from sqlalchemy import inspect

        return not inspect(self.engine).get_table_names()

    def ensure(self) -> None:
        """Create the schema only for an empty database; migrations own every later change."""
        if self.is_empty():
            self.create_all()
        self.ensure_vector()

    def assert_migration_allowed(self, *, now: float | None = None) -> None:
        """Refuse when another instance is a live primary; a standby never migrates."""
        from sqlalchemy.exc import SQLAlchemyError

        from .lease import TTL

        try:
            row = self.fetch_one(select(engine_lease).where(engine_lease.c.id == 1))
        except SQLAlchemyError:  # empty database: nothing can hold the lease yet
            return
        if row is None:
            return
        moment = time.time() if now is None else float(now)
        if float(row.get("heartbeat_at") or 0.0) >= moment - float(TTL):
            raise MigrationError(
                "migration refused: a primary is alive "
                f"({row.get('host')} pid {row.get('pid')}); run the upgrade on the primary "
                "or stop that instance first"
            )

    def upgrade(self, revision: str = "head", *, timeout: float = MIGRATION_LOCK_TIMEOUT) -> int:
        """Run the Alembic chain to `revision` under the advisory lock (ADR-0016)."""
        if not (MIGRATIONS / "env.py").exists():
            raise MigrationError(f"migrations tree is missing at {MIGRATIONS}")
        self.assert_migration_allowed()
        from alembic import command
        from alembic.config import Config as AlembicConfig

        cfg = AlembicConfig()
        cfg.set_main_option("script_location", str(MIGRATIONS))
        cfg.set_main_option("sqlalchemy.url", self.url)
        with self.engine.connect() as conn:
            locked = False
            if self.dialect == "postgresql":
                deadline = time.time() + float(timeout)
                while True:
                    locked = bool(
                        conn.execute(
                            text("SELECT pg_try_advisory_lock(:key)"),
                            {"key": ADVISORY_LOCK_KEY},
                        ).scalar()
                    )
                    if locked or time.time() >= deadline:
                        break
                    time.sleep(0.25)
                conn.commit()
                if not locked:
                    raise MigrationError(
                        f"migration lock not acquired within {timeout:g}s; "
                        "another upgrade is still running"
                    )
            try:
                cfg.attributes["connection"] = conn
                command.upgrade(cfg, revision)
                self.upsert(
                    conn,
                    schema_meta,
                    [{"key": "version", "value": str(SCHEMA_VERSION)}],
                    ["key"],
                    update={"value": str(SCHEMA_VERSION)},
                )
                conn.commit()
            finally:
                if locked:
                    conn.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY}
                    )
                    conn.commit()
        return SCHEMA_VERSION

    # -------------------------------------------------------- connection

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        with self.engine.begin() as conn:
            yield conn

    def fetch_all(self, statement) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(statement).mappings().all()]

    def fetch_one(self, statement) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(statement).mappings().first()
            return dict(row) if row is not None else None

    def scalar(self, statement):
        with self.connect() as conn:
            return conn.execute(statement).scalar()

    def upsert(
        self,
        conn: Connection,
        table: Table,
        rows: Sequence[Mapping[str, Any]],
        index_elements: Sequence[str],
        update=None,
    ) -> int:
        if not rows:
            return 0
        items = list(rows)
        dialect = conn.dialect.name
        insert_fn = pg_insert if dialect == "postgresql" else sqlite_insert
        total = 0
        for start in range(0, len(items), UPSERT_BATCH_ROWS):
            chunk = items[start : start + UPSERT_BATCH_ROWS]
            statement = insert_fn(table).values(chunk)
            if update is None:
                conflict = {
                    column.name: statement.excluded[column.name]
                    for column in table.columns
                    if column.name not in index_elements
                }
            elif callable(update):
                conflict = update(statement)
            else:
                conflict = update
            statement = statement.on_conflict_do_update(
                index_elements=list(index_elements), set_=conflict
            )
            total += int(conn.execute(statement).rowcount or 0)
        return total

    def dispose(self) -> None:
        with contextlib.suppress(Exception):
            self.engine.dispose()
