"""Admin database helpers: upgrade, check, backup, and restore.

Everything here runs outside the agent: it never touches the lease except to refuse a
destructive operation while a primary is still alive.
"""


import base64
import json
import logging
import os
import shutil
import subprocess
from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import Integer, func, select, text
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
    metadata,
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
from . import service

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


def _container_pg(tool: str, *, config_path=None) -> list[str] | None:
    """`podman/docker exec` into the compose stack's postgres container, when it is running."""
    try:
        current = service.load(config_path)
    except ConfigError:
        return None
    compose = service.compose_argv(current)
    if compose is None:
        return None
    listing = subprocess.run(
        [*compose, "ps", "-q", "postgres"],
        cwd=current.root,
        capture_output=True,
        text=True,
        check=False,
    )
    container = next(
        (line.strip() for line in listing.stdout.splitlines() if line.strip()), ""
    )
    if listing.returncode != 0 or not container:
        return None
    return [compose[0], "exec", "-i", container, tool]


def _pg_command(tool: str, *, config_path=None) -> tuple[list[str], bool]:
    """The host binary, or the exec prefix into the managed postgres container."""
    binary = shutil.which(tool)
    if binary:
        return [binary], False
    command = _container_pg(tool, config_path=config_path)
    if command is not None:
        return command, True
    raise ConfigError(
        f"{tool} was not found on PATH and no managed postgres container is available; "
        "install the postgres client tools or run this inside the compose stack"
    )


def dump_database(dsn: str, target: Path, *, config_path=None) -> None:
    """Custom-format dump from the host client, or streamed out of the postgres container."""
    command, container = _pg_command("pg_dump", config_path=config_path)
    argv = [*command, "--format=custom", *(["--file", str(target)] if not container else []), dsn]
    if container:
        with open(target, "wb") as handle:
            subprocess.run(argv, stdout=handle, check=True)
    else:
        subprocess.run(argv, check=True)


def restore_database(dsn: str, source: Path, *, config_path=None) -> None:
    """Restore from the host client, or stream the archive into the postgres container."""
    command, container = _pg_command("pg_restore", config_path=config_path)
    argv = [*command, "--clean", "--if-exists", "--no-owner", "--dbname", dsn]
    if container:
        with open(source, "rb") as handle:
            subprocess.run(argv, stdin=handle, check=True)
    else:
        subprocess.run([*argv, str(source)], check=True)


def backup(config_path, out: Path) -> Path:
    """Postgres `pg_dump --format=custom`; mode 600."""
    host, _paths = _host(config_path)
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError("database URL is empty; nothing to back up")
    target = Path(out).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    dump_database(_pg_dsn(url), target, config_path=config_path)
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
        restore_database(_pg_dsn(url), path, config_path=config_path)
        log.info("database restore %s <- %s", db.label, path)
    finally:
        db.dispose()


# ------------------------------------------------------------------ portable JSON


def pg_available(tool: str, *, config_path=None) -> bool:
    """True when the host binary or the managed postgres container can run `tool`."""
    try:
        _pg_command(tool, config_path=config_path)
    except ConfigError:
        return False
    return True


def _json_value(value: object) -> object:
    """One column value in a JSON-safe form that `_db_value` can reverse."""
    if isinstance(value, datetime):
        return {"$type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"$type": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"$type": "time", "value": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$type": "decimal", "value": str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$type": "bytes", "value": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, UUID):
        return str(value)
    tolist = getattr(value, "tolist", None)
    if callable(tolist) and not isinstance(value, (str, bytes)):
        return tolist()  # numpy arrays from pgvector
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _db_value(value: object) -> object:
    """Reverse `_json_value` for the handful of types Postgres returns as objects."""
    if isinstance(value, Mapping) and value.get("$type"):
        kind = str(value.get("$type"))
        raw = value.get("value")
        if kind == "datetime":
            return datetime.fromisoformat(str(raw))
        if kind == "date":
            return date.fromisoformat(str(raw))
        if kind == "time":
            return time.fromisoformat(str(raw))
        if kind == "decimal":
            return Decimal(str(raw))
        if kind == "bytes":
            return base64.b64decode(str(raw))
    return value


def _portable_rows(db: Database) -> dict:
    """Every non-computed column of every table, in dependency order."""
    body: dict = {"format": 1, "tables": {}}
    with db.connect() as conn:
        for table in metadata.sorted_tables:
            columns = [column for column in table.columns if column.computed is None]
            order = [column for column in table.primary_key.columns] or list(columns)
            rows = conn.execute(select(*columns).order_by(*order)).mappings().all()
            body["tables"][table.name] = [
                {key: _json_value(value) for key, value in row.items()} for row in rows
            ]
    return body


def dump_portable(url: str, target: Path) -> None:
    """Portable JSON dump for a host without `pg_dump`; the whole schema, row by row."""
    db = Database(url, pool_size=1, pool_timeout=2)
    try:
        body = _portable_rows(db)
    finally:
        db.dispose()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _align_sequences(db: Database) -> None:
    """Realign every integer primary-key sequence after explicit ids were inserted."""
    with db.connect() as conn:
        for table in metadata.sorted_tables:
            columns = list(table.primary_key.columns)
            if len(columns) != 1 or not isinstance(columns[0].type, Integer):
                continue
            column = columns[0]
            seq = conn.execute(
                text("SELECT pg_get_serial_sequence(:table, :column)"),
                {"table": table.name, "column": column.name},
            ).scalar()
            if not seq:
                continue
            # Table and column names come from the static schema, never from the archive.
            conn.execute(
                text(
                    f"SELECT setval(:seq, COALESCE((SELECT MAX({column.name}) "
                    f"FROM {table.name}), 1), (SELECT COUNT(*) FROM {table.name}) > 0)"
                ),
                {"seq": str(seq)},
            )


def restore_portable(url: str, source: Path) -> None:
    """Replace every table from a portable JSON dump, then realign the sequences."""
    try:
        body = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read portable backup {source}: {exc}") from exc
    if not isinstance(body, dict) or body.get("format") != 1:
        raise ConfigError(f"unsupported portable backup format in {source}")
    tables = body.get("tables")
    if not isinstance(tables, dict):
        raise ConfigError(f"portable backup has no tables: {source}")
    db = Database(url, pool_size=1, pool_timeout=2)
    try:
        with db.connect() as conn:
            for table in reversed(list(metadata.sorted_tables)):
                conn.execute(table.delete())
            for table in metadata.sorted_tables:
                rows = tables.get(table.name) or []
                if not isinstance(rows, list) or not rows:
                    continue
                allowed = {column.name for column in table.columns if column.computed is None}
                payload = [
                    {key: _db_value(value) for key, value in row.items() if key in allowed}
                    for row in rows
                    if isinstance(row, dict)
                ]
                if payload:
                    conn.execute(table.insert(), payload)
        _align_sequences(db)
    finally:
        db.dispose()
