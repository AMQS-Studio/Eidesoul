"""Operator files are the source of truth; DB documents are a derived cache (ADR-0002 §4.1).

`config.yaml` (its `engine` section), `soul.yaml`, and `roles.yaml` are read, diffable, and
recoverable. The cache is keyed by content hash, refreshed from the files on start and by
`/reload`, and rebuilt from the files whenever it is missing or stale. A cache row never wins a
conflict and never becomes a fallback for an invalid file.
"""


import contextlib
import hashlib
import logging
import os
import time
from pathlib import Path

import yaml
from sqlalchemy import select, text

from ..config import Paths
from ..db import Database
from ..schema import documents, state

log = logging.getLogger(__name__)

ROLES_PREFIX = "role:"
ROLE_MODE = 0o600  # roles.yaml holds phone numbers
SYNC_LOCK_KEY = 0x45494446  # "EIDF": one file sync at a time
SYNC_ENGINE = "sync:engine"
SYNC_SOUL = "sync:soul"
SYNC_ROLE_PREFIX = "sync:role:"

_MISSING = object()


def _dump(body: object) -> str:
    return yaml.safe_dump(body, sort_keys=False, allow_unicode=True)


def _atomic_write(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    if mode is not None:
        with contextlib.suppress(OSError):
            tmp.chmod(mode)
    os.replace(tmp, path)


def _hash(value: str | None) -> str | None:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest() if value is not None else None


def _read_yaml(path: Path, label: str) -> object:
    """The file body, `_MISSING` when absent, or None when it is not a YAML mapping."""
    if not path.is_file():
        return _MISSING
    try:
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        log.warning("%s is not valid YAML; the cache is left untouched: %s", label, exc)
        return None
    if body is None:
        return {}
    if not isinstance(body, dict):
        log.warning("%s must be a mapping; the cache is left untouched", label)
        return None
    return body


class DocumentStore:
    def __init__(self, db: Database, paths: Paths, *, sync: bool = True) -> None:
        self.db = db
        self.paths = paths
        self.enabled = sync

    # ---------------------------------------------------------- cache

    def get(self, name: str) -> str | None:
        row = self.db.fetch_one(select(documents).where(documents.c.name == name))
        return None if row is None else str(row["content"])

    def names(self, prefix: str = "") -> list[str]:
        statement = select(documents.c.name).order_by(documents.c.name)
        if prefix:
            statement = statement.where(documents.c.name.startswith(prefix))
        return [str(row["name"]) for row in self.db.fetch_all(statement)]

    def put(self, name: str, content: str, actor: str = "") -> None:
        row = {"name": name, "content": str(content), "updated_at": time.time()}
        with self.db.connect() as conn:
            self.db.upsert(conn, documents, [row], ["name"])

    def delete(self, name: str) -> None:
        with self.db.connect() as conn:
            conn.execute(documents.delete().where(documents.c.name == name))

    # ---------------------------------------------------------- files

    def _config(self) -> object:
        return _read_yaml(self.paths.config, "config.yaml")

    def _soul_file(self) -> object:
        path = self.paths.soul_yaml
        if not path.is_file():
            return _MISSING
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            log.warning("failed to read %s", path, exc_info=True)
            return None

    def _roles_file(self) -> object:
        body = _read_yaml(self.paths.roles, "roles.yaml")
        if not isinstance(body, dict):
            return body
        roles = body.get("roles")
        if not isinstance(roles, dict):
            log.warning("roles.yaml must contain a 'roles' mapping; the cache is left untouched")
            return None
        return roles

    # ---------------------------------------------------------- seed/sync

    def seed(self) -> int:
        """Fill missing cache rows from files; an existing cache row is replaced by `sync`."""
        if not self.enabled:
            return 0
        existing = set(self.names())
        saved = 0
        config = self._config()
        engine = config.get("engine") if isinstance(config, dict) else None
        if "engine" not in existing and isinstance(engine, dict):
            self.put("engine", _dump(engine), actor="seed")
            saved += 1
        soul = self._soul_file()
        if "soul" not in existing and isinstance(soul, str):
            self.put("soul", soul, actor="seed")
            saved += 1
        roles = self._roles_file()
        if isinstance(roles, dict):
            for name, body in roles.items():
                document = f"{ROLES_PREFIX}{name}"
                if document in existing:
                    continue
                self.put(document, _dump(body), actor="seed")
                saved += 1
        if saved:
            log.info("documents seeded from files: %d", saved)
        return saved

    def _get_with(self, conn, name: str) -> str | None:
        row = conn.execute(select(documents.c.content).where(documents.c.name == name)).first()
        return None if row is None else str(row[0])

    def _names_with(self, conn, prefix: str = "") -> list[str]:
        statement = select(documents.c.name).order_by(documents.c.name)
        if prefix:
            statement = statement.where(documents.c.name.startswith(prefix))
        return [str(row[0]) for row in conn.execute(statement)]

    def _state(self, conn, key: str) -> str | None:
        row = conn.execute(select(state.c.value).where(state.c.key == key)).first()
        return None if row is None else str(row[0])

    def _set_state(self, conn, key: str, value: str) -> None:
        self.db.upsert(
            conn,
            state,
            [{"key": key, "value": value, "updated_at": time.time()}],
            ["key"],
            update={"value": value, "updated_at": time.time()},
        )

    def _put_document(self, conn, name: str, content: str) -> None:
        self.db.upsert(
            conn,
            documents,
            [{"name": name, "content": str(content), "updated_at": time.time()}],
            ["name"],
        )

    def _cache(self, conn, name: str, key: str, content: str) -> int:
        """Cache one file value; return 1 only when the stored row actually changed."""
        digest = _hash(content)
        current = self._get_with(conn, name)
        if current == content and self._state(conn, key) == digest:
            return 0
        self._put_document(conn, name, content)
        self._set_state(conn, key, digest or "")
        log.info("document cache refreshed name=%s hash=%s", name, (digest or "")[:12])
        return 1

    def _restore(self, conn, name: str, key: str, path: Path, mode: int | None = None) -> int:
        """A missing file is rebuilt from the cache, never the other way around."""
        content = self._get_with(conn, name)
        if content is None:
            return 0
        _atomic_write(path, content, mode)
        self._set_state(conn, key, _hash(content) or "")
        log.warning("file rebuilt from the cache: %s", path)
        return 1

    def sync(self) -> int:
        """Refresh the cache from the operator files; files always win (ADR-0002 §4.1)."""
        if not self.enabled:
            return 0
        with self.db.engine.connect() as conn:
            acquired = conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": SYNC_LOCK_KEY}
            ).scalar()
            if not acquired:
                log.info("document sync skipped: another instance holds the lock")
                return 0
            try:
                changed = self._refresh(conn)
                conn.commit()
                return changed
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SYNC_LOCK_KEY})
                conn.commit()

    def _refresh(self, conn) -> int:
        changed = 0
        config = self._config()
        if isinstance(config, dict):
            engine = config.get("engine")
            if isinstance(engine, dict):
                changed += self._cache(conn, "engine", SYNC_ENGINE, _dump(engine))
            else:
                log.warning("config.yaml has no 'engine' mapping; the cache is left untouched")
        elif config is _MISSING:
            log.warning("config.yaml is missing; the cache is left untouched")

        soul = self._soul_file()
        if isinstance(soul, str):
            changed += self._cache(conn, "soul", SYNC_SOUL, soul)
        elif soul is _MISSING:
            changed += self._restore(conn, "soul", SYNC_SOUL, self.paths.soul_yaml)

        roles = self._roles_file()
        if isinstance(roles, dict):
            for name, body in roles.items():
                changed += self._cache(
                    conn, f"{ROLES_PREFIX}{name}", f"{SYNC_ROLE_PREFIX}{name}", _dump(body)
                )
            existing = {
                name.split(":", 1)[1]
                for name in self._names_with(conn, ROLES_PREFIX)
                if ":" in name
            }
            for stale in sorted(existing - set(roles)):
                document = f"{ROLES_PREFIX}{stale}"
                conn.execute(documents.delete().where(documents.c.name == document))
                changed += 1
                log.info("stale role cache row removed name=%s", document)
        elif roles is _MISSING:
            changed += self._restore_roles(conn)
        return changed

    def _restore_roles(self, conn) -> int:
        rows = {
            name.split(":", 1)[1]: yaml.safe_load(self._get_with(conn, name) or "") or {}
            for name in self._names_with(conn, ROLES_PREFIX)
            if ":" in name
        }
        if not rows:
            return 0
        _atomic_write(self.paths.roles, _dump({"roles": rows}), mode=ROLE_MODE)
        log.warning("roles.yaml rebuilt from the cache")
        return 1

    def import_from_files(self) -> int:
        """Read host files and refresh every cache row (`monoagens config import`)."""
        if not self.enabled:
            return 0
        with self.db.engine.connect() as conn:
            changed = self._refresh(conn)
            conn.commit()
        if "persona" in set(self.names()):
            self.delete("persona")
            changed += 1
        return changed
