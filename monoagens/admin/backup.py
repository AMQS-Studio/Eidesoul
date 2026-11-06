"""Portable snapshots: `pg_dump` plus the four host files in one `tar.gz`.

A restore validates the archive before touching anything, writes the host files atomically,
and only replaces the database when the operator passes `--yes`. Every name inside the
archive is a plain file name: a crafted member can never escape the config directory.
"""


import io
import json
import os
import tarfile
import tempfile
import time
from pathlib import Path

from sqlalchemy.engine import make_url

from ..engine.config import ConfigError, Paths, load_host, resolve_database_url
from .database import dump_database, restore_database

HOST_FILES = ("config.yaml", "roles.yaml", "soul.yaml", ".env")
REQUIRED_FILES = ("config.yaml", "roles.yaml", "soul.yaml")
PRIVATE_FILES = (".env", "roles.yaml")
DUMP_NAME = "database.pgdump"
MANIFEST = "manifest.json"
BACKUP_DIR = "backups"


def pg_dsn(url: str) -> str:
    """The libpq DSN: drop the SQLAlchemy driver, keep the password."""
    parsed = make_url(str(url))
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)


def pg_dump(dsn: str, target: Path) -> None:
    """Host `pg_dump`, or the managed postgres container when the client is not installed."""
    dump_database(dsn, target)


def pg_restore(dsn: str, source: Path) -> None:
    """Host `pg_restore`, or the managed postgres container when the client is not installed."""
    restore_database(dsn, source)


def default_path(paths: Paths) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return paths.workspace.parent / BACKUP_DIR / f"monoagens-{stamp}.tar.gz"


def _database_url(config_path: str | None) -> str:
    host, _paths = load_host(config_path)
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError("database URL is empty; nothing to back up")
    return url


def _manifest(url: str, present: list[str]) -> dict:
    return {
        "format": 1,
        "created_at": time.time(),
        "database": make_url(url).render_as_string(hide_password=True),
        "files": present,
        "dump": DUMP_NAME,
    }


def _private_mode(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """`.env` and roles.yaml carry secrets: the archive states their private mode."""
    if info.name in PRIVATE_FILES:
        info.mode = 0o600
    return info


def create(
    config_path: str | None = None,
    out: str | Path | None = None,
    *,
    dump=pg_dump,
) -> Path:
    """Write one archive; the database dump and the host files share one portable bundle."""
    paths = load_host(config_path)[1]
    url = _database_url(config_path)
    target = Path(out).expanduser() if out else default_path(paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    present = [name for name in HOST_FILES if (paths.config.parent / name).is_file()]
    with tempfile.TemporaryDirectory(prefix="monoagens-backup-") as scratch:
        dump_file = Path(scratch) / DUMP_NAME
        dump(pg_dsn(url), dump_file)
        with tarfile.open(target, "w:gz") as archive:
            archive.add(dump_file, arcname=DUMP_NAME, recursive=False)
            for name in present:
                source = paths.config.parent / name
                archive.add(
                    source,
                    arcname=name,
                    recursive=False,
                    filter=_private_mode,
                )
            body = json.dumps(_manifest(url, present), indent=2, sort_keys=True)
            info = tarfile.TarInfo(MANIFEST)
            payload = body.encode("utf-8")
            info.size = len(payload)
            info.mode = 0o600
            info.mtime = time.time()
            archive.addfile(info, io.BytesIO(payload))
    os.chmod(target, 0o600)
    print(f"backup: {target}", flush=True)
    print(f"files: {', '.join(present) or '-'}; dump: {DUMP_NAME}", flush=True)
    return target


def inspect(source: str | Path) -> dict:
    """Read and validate the manifest without extracting anything."""
    archive_path = Path(source).expanduser()
    if not archive_path.is_file():
        raise ConfigError(f"backup file not found: {archive_path}")
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            names = archive.getnames()
            for name in names:
                if name != Path(name).name or name in {"", ".", ".."}:
                    raise ConfigError(f"refusing a member outside the archive root: {name}")
            if MANIFEST not in names:
                raise ConfigError(f"not a monoagens backup: {archive_path} has no {MANIFEST}")
            member = archive.extractfile(MANIFEST)
            body = json.loads(member.read().decode("utf-8")) if member else None
            if not isinstance(body, dict):
                raise ConfigError(f"backup manifest is not a mapping: {archive_path}")
            if body.get("format") != 1:
                raise ConfigError(f"unsupported backup format: {body.get('format')!r}")
            if DUMP_NAME not in names:
                raise ConfigError(f"backup has no database dump ({DUMP_NAME})")
            declared = body.get("files")
            wanted = [str(item) for item in declared] if isinstance(declared, list) else []
            wanted = [name for name in wanted if name in HOST_FILES]
            missing = [name for name in REQUIRED_FILES if name not in wanted or name not in names]
    except (tarfile.TarError, json.JSONDecodeError, OSError) as exc:
        raise ConfigError(f"cannot read backup {archive_path}: {exc}") from exc
    if missing:
        raise ConfigError(f"backup is missing host file(s): {', '.join(missing)}")
    body["path"] = str(archive_path)
    body["files"] = [name for name in HOST_FILES if name in wanted]
    return body


def _extract_file(archive: tarfile.TarFile, name: str, target: Path) -> None:
    member = archive.extractfile(name)
    if member is None:
        raise ConfigError(f"backup member is not a file: {name}")
    data = member.read()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.restore")
    temp.write_bytes(data)
    os.chmod(temp, 0o600 if name in PRIVATE_FILES else 0o644)
    os.replace(temp, target)


def restore(
    source: str | Path,
    config_path: str | None = None,
    *,
    yes: bool = False,
    force: bool = False,
    restore_db=pg_restore,
) -> list[str]:
    """Validate the archive, extract the host files, then optionally replace the database."""
    manifest = inspect(source)
    _host, paths = load_host(config_path)
    archive_path = Path(manifest["path"])
    extracted: list[str] = []
    with tarfile.open(archive_path, "r:gz") as archive, tempfile.TemporaryDirectory(
        prefix="monoagens-restore-"
    ) as scratch:
        for name in manifest["files"]:
            _extract_file(archive, name, paths.config.parent / name)
            extracted.append(name)
        dump_file = Path(scratch) / DUMP_NAME
        member = archive.extractfile(DUMP_NAME)
        if member is None:
            raise ConfigError(f"backup has no database dump ({DUMP_NAME})")
        dump_file.write_bytes(member.read())
        if not yes:
            print("database: not restored (pass --yes)", flush=True)
            print(f"restore: extracted {', '.join(extracted)}", flush=True)
            return extracted
        url = _database_url(config_path)
        _refuse_live_lease(url, force=force)
        restore_db(pg_dsn(url), dump_file)
    print(f"restore: {', '.join(extracted)} and the database", flush=True)
    return extracted


def _refuse_live_lease(url: str, *, force: bool) -> None:
    from ..engine.db import Database
    from .database import lease_active

    db = Database(url, pool_size=1, pool_timeout=2)
    try:
        active = lease_active(db)
    finally:
        db.dispose()
    if active is not None and not force:
        raise ConfigError(
            "lease still held by "
            f"{active.get('host')} pid {active.get('pid')}; stop it first or use --force"
        )
