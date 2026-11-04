"""Admin database helpers: upgrade, check, backup, and restore.

Everything here runs outside the agent: it never touches the lease except to refuse a
destructive operation while a primary is still alive.
"""


import logging
import os
import subprocess
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from ..engine.config import ConfigError, HostConfig, Paths, load_host, resolve_database_url
from ..engine.db import Database
from ..engine.schema import (
    actions,
    approvals,
    config_changes,
    documents,
    drives,
    engine_lease,
    messages,
    people,
    recipes,
    schema_meta,
    skill_files,
    skills,
    state,
    thread_people,
    threads,
    topics,
)
from ..engine.stores.documents import DocumentStore

log = logging.getLogger(__name__)

LEASE_TTL = 30.0

COUNT_TABLES = (
    ("documents", documents),
    ("messages", messages),
    ("threads", threads),
    ("people", people),
    ("topics", topics),
    ("drives", drives),
    ("thread_people", thread_people),
    ("skills", skills),
    ("skill_files", skill_files),
    ("recipes", recipes),
    ("actions", actions),
    ("approvals", approvals),
    ("config_changes", config_changes),
    ("state", state),
)


# ------------------------------------------------------------------ helpers


def _host(config_path) -> tuple[HostConfig, Paths]:
    return load_host(config_path)


def _database(host: HostConfig) -> Database:
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError(
            "database URL is empty; set database.url in config.yaml or the env var "
            f"{host.database.url_env or 'MONOAGENS_DATABASE_URL'}"
        )
    return Database(
        url,
        pool_size=host.database.pool_size,
        pool_timeout=host.database.pool_timeout,
    )


def _count(db: Database, table) -> int:
    return int(db.scalar(select(func.count()).select_from(table)) or 0)


def _lease_row(db: Database) -> dict | None:
    return db.fetch_one(select(engine_lease).where(engine_lease.c.id == 1))


def lease_active(db: Database, *, ttl: float = LEASE_TTL) -> dict | None:
    """The lease row while it is alive; freshness comes from the database clock (ADR-0012 §4.4)."""
    row = db.fetch_one(
        text(
            "SELECT instance_id, host, pid, generation, heartbeat_at, "
            "EXTRACT(EPOCH FROM clock_timestamp()) AS now FROM engine_lease WHERE id = 1"
        )
    )
    if row is None:
        return None
    if float(row.get("heartbeat_at") or 0.0) < float(row["now"]) - float(ttl):
        return None
    return row


def _pg_dsn(url: str) -> str:
    """DSN for pg_dump/pg_restore: drop the SQLAlchemy driver, keep the password."""
    parsed = make_url(str(url))
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)


# ------------------------------------------------------------------ upgrade


def reset_sequences(db: Database) -> list[str]:
    """Align Postgres sequences with ids inserted explicitly."""
    done: list[str] = []
    for table in (messages, actions, approvals, config_changes):
        with db.connect() as conn:
            conn.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence(:table, 'id'), "
                    f"GREATEST(COALESCE((SELECT MAX(id) FROM {table.name}), 0), 1), "
                    f"COALESCE((SELECT MAX(id) FROM {table.name}), 0) > 0)"
                ),
                {"table": table.name},
            )
        done.append(table.name)
    return done


def upgrade(config_path=None) -> dict:
    """Run the Alembic chain, ensure gateway tables, then seed documents from files."""
    host, paths = _host(config_path)
    db = _database(host)
    try:
        db.upgrade(timeout=host.database.migrations.advisory_lock_timeout_seconds)
        db.ensure_vector()
        from ..gateways import ensure_schemas

        gateways = ensure_schemas(db)
        docs = DocumentStore(db, paths, sync=host.sync_documents)
        seeded = docs.seed()
        docs.sync()
        reset_sequences(db)
        found = db.fetch_one(select(schema_meta.c.value).where(schema_meta.c.key == "version"))
        log.info(
            "db upgrade: %s (schema %s, seeded documents %d)",
            db.label,
            (found or {}).get("value"),
            seeded,
        )
        return {
            "database": db.label,
            "dialect": make_url(db.url).get_backend_name(),
            "schema_version": str(found["value"]) if found is not None else None,
            "documents": docs.names(),
            "seeded": seeded,
            "gateways": gateways,
        }
    finally:
        db.dispose()


# -------------------------------------------------------------------- check


def check(config_path=None) -> dict:
    """Read-only summary: schema, row counts, lease, approvals, config changes."""
    host, _paths = _host(config_path)
    db = _database(host)
    try:
        try:
            found = db.fetch_one(
                select(schema_meta.c.value).where(schema_meta.c.key == "version")
            )
            counts = {name: _count(db, table) for name, table in COUNT_TABLES}
            pending_approvals = int(
                db.scalar(
                    select(func.count())
                    .select_from(approvals)
                    .where(approvals.c.status == "pending")
                )
                or 0
            )
            lease = _lease_row(db)
            last_change = db.fetch_one(
                select(config_changes).order_by(config_changes.c.id.desc()).limit(1)
            )
        except SQLAlchemyError as exc:
            raise ConfigError(
                f"database is not ready ({db.label}): {exc}; run `monoagens db upgrade`"
            ) from exc
        return {
            "database": db.label,
            "dialect": make_url(db.url).get_backend_name(),
            "schema_version": str(found["value"]) if found is not None else None,
            "counts": counts,
            "approvals_pending": pending_approvals,
            "lease": lease,
            "lease_active": lease_active(db) is not None,
            "last_config_change": last_change,
        }
    finally:
        db.dispose()


def format_check(data: dict) -> str:
    """Plain-text lines for the `monoagens db check` CLI."""
    lease = data.get("lease") or {}
    if lease and lease.get("host"):
        holder = (
            f"{lease.get('host')} pid {lease.get('pid')} "
            f"({'active' if data.get('lease_active') else 'stale'})"
        )
    else:
        holder = "-"
    last = data.get("last_config_change") or {}
    if last:
        last_text = f"{last.get('file')}:{last.get('path')} by {last.get('actor') or '?'}"
    else:
        last_text = "-"
    counts = data.get("counts") or {}
    lines = [
        "Database",
        f"• URL: {data.get('database')}",
        f"• Dialect: {data.get('dialect')}",
        f"• Schema version: {data.get('schema_version') or '-'}",
        f"• Lease: {holder}",
        f"• Approvals pending: {data.get('approvals_pending', 0)}",
        f"• Last config change: {last_text}",
        "Rows",
    ]
    for name in (
        "documents",
        "messages",
        "threads",
        "people",
        "topics",
        "skills",
        "recipes",
        "actions",
        "approvals",
        "config_changes",
    ):
        lines.append(f"• {name}: {counts.get(name, 0)}")
    return "\n".join(lines)


# ------------------------------------------------------------ backup/restore


def backup(config_path, out: Path) -> Path:
    """Postgres `pg_dump --format=custom`; mode 600."""
    host, _paths = _host(config_path)
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError("database URL is empty; nothing to back up")
    target = Path(out).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["pg_dump", "--format=custom", "--file", str(target), _pg_dsn(url)],
        check=True,
    )
    try:
        os.chmod(target, 0o600)
    except OSError:
        log.warning("could not chmod 600 %s", target)
    log.info("database backup -> %s", target)
    return target


def restore(config_path, source: Path, *, yes: bool = False, force: bool = False) -> None:
    """Restore a backup; refuses without `yes` and refuses while the lease is alive."""
    if not yes:
        raise ConfigError("restore overwrites the database; run it with --yes")
    host, _paths = _host(config_path)
    db = _database(host)
    try:
        active = lease_active(db)
        if active is not None and not force:
            raise ConfigError(
                "lease still held by "
                f"{active.get('host')} pid {active.get('pid')}; stop it first or use --force"
            )
        url = db.url
        path = Path(source).expanduser()
        if not path.is_file():
            raise ConfigError(f"backup file not found: {path}")
        subprocess.run(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--dbname",
                _pg_dsn(url),
                str(path),
            ],
            check=True,
        )
        log.info("database restore %s <- %s", db.label, path)
    finally:
        db.dispose()
