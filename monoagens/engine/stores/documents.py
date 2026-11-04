"""Engine/soul/role documents in the DB, mirrored into config.yaml, soul.yaml, roles.yaml.

The DB is the source of truth; files are seeds plus mirrors. The `soul` document holds the
whole soul.yaml body, the `engine` document holds the engine section of config.yaml, and
every role lives in roles.yaml, so one config folder holds the whole soul.
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


def _canonical(body: object) -> str:
    return yaml.safe_dump(body, sort_keys=True, allow_unicode=True)


def _yaml_dict(text: str, label: str) -> dict:
    try:
        body = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        log.warning("%s is not valid YAML: %s", label, exc)
        return {}
    return body if isinstance(body, dict) else {}


class DocumentStore:
    def __init__(self, db: Database, paths: Paths, *, sync: bool = True) -> None:
        self.db = db
        self.paths = paths
        self.enabled = sync

    # ---------------------------------------------------------- documents

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

    # ---------------------------------------------------------- seed/sync

    def _config(self) -> dict:
        path = self.paths.config
        if not path.is_file():
            return {}
        try:
            return _yaml_dict(path.read_text(encoding="utf-8"), "config.yaml")
        except OSError:
            log.warning("failed to read %s", path, exc_info=True)
            return {}

    def _soul_file(self) -> str | None:
        path = self.paths.soul_yaml
        if not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            log.warning("failed to read %s", path, exc_info=True)
            return None

    def _roles_file(self) -> dict:
        path = self.paths.roles
        if not path.is_file():
            return {}
        try:
            body = _yaml_dict(path.read_text(encoding="utf-8"), "roles.yaml")
        except OSError:
            log.warning("failed to read %s", path, exc_info=True)
            return {}
        roles = body.get("roles")
        return roles if isinstance(roles, dict) else {}

    def seed(self) -> int:
        """Fill missing documents from files; existing documents are left untouched."""
        if not self.enabled:
            return 0
        existing = set(self.names())
        saved = 0
        engine = self._config().get("engine")
        if "engine" not in existing and isinstance(engine, dict):
            self.put("engine", _dump(engine), actor="seed")
            saved += 1
        soul = self._soul_file()
        if "soul" not in existing and soul:
            self.put("soul", soul, actor="seed")
            saved += 1
        for name, body in self._roles_file().items():
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

    def _sync_one(
        self,
        conn,
        *,
        key: str,
        file_text: str | None,
        db_text: str | None,
        label: str,
        export: list[str],
    ) -> str | None:
        """Four-case table plus missing sides; DB wins every conflict (ADR-0042 §4.1)."""
        file_hash = _hash(file_text)
        db_hash = _hash(db_text)
        last = self._state(conn, key)
        action = ""
        if file_text is None and db_text is None:
            return None
        if file_text is None:
            action = "export"
        elif db_text is None:
            action = "import"
        elif last is None:
            action = "conflict-first"
        elif file_hash == last and db_hash == last:
            return last
        elif file_hash != last and db_hash == last:
            action = "import"
        elif db_hash != last and file_hash == last:
            action = "export"
        elif file_hash != db_hash:
            action = "conflict"
        else:
            action = "same"
        if action == "import":
            assert file_text is not None
            if key == SYNC_ENGINE:
                body = _yaml_dict(file_text, label)
                content = _dump(body)
            else:
                content = file_text
            self._put_document(conn, "engine" if key == SYNC_ENGINE else label, content)
            self._set_state(conn, key, _hash(content) or "")
            log.info("document sync import key=%s hash=%s", key, (_hash(content) or "")[:12])
            return _hash(content)
        if action in {"export", "conflict", "conflict-first", "same"}:
            if action in {"conflict", "conflict-first"}:
                log.warning(
                    "document sync conflict key=%s; database wins, mirror exported", key
                )
            if db_text is not None:
                self._set_state(conn, key, db_hash or "")
                export.append(label if key != SYNC_ENGINE else "engine")
            return db_hash
        return db_hash

    def sync(self) -> int:
        """Apply changed host files on start; hashes only, one writer, no resolver."""
        if not self.enabled:
            return 0
        config = self._config()
        engine = config.get("engine")
        file_engine = _canonical(engine) if isinstance(engine, dict) else None
        file_soul = self._soul_file()
        file_roles = self._roles_file()
        exported: list[str] = []
        changed = 0
        with self.db.engine.connect() as conn:
            acquired = conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": SYNC_LOCK_KEY}
            ).scalar()
            if not acquired:
                log.info("document sync skipped: another instance holds the lock")
                return 0
            try:
                db_engine = self._get_with(conn, "engine")
                db_soul = self._get_with(conn, "soul")
                if self._sync_one(
                    conn,
                    key=SYNC_ENGINE,
                    file_text=file_engine,
                    db_text=db_engine,
                    label="engine",
                    export=exported,
                ) is not None:
                    changed += 1
                if self._sync_one(
                    conn,
                    key=SYNC_SOUL,
                    file_text=file_soul,
                    db_text=db_soul,
                    label="soul",
                    export=exported,
                ) is not None:
                    changed += 1
                names = sorted(set(file_roles) | {
                    name.split(":", 1)[1]
                    for name in self._names_with(conn, ROLES_PREFIX)
                    if ":" in name
                })
                for name in names:
                    body = file_roles.get(name)
                    file_text = _canonical(body) if isinstance(body, dict) else None
                    db_text = self._get_with(conn, f"{ROLES_PREFIX}{name}")
                    if self._sync_one(
                        conn,
                        key=f"{SYNC_ROLE_PREFIX}{name}",
                        file_text=file_text,
                        db_text=db_text,
                        label=f"{ROLES_PREFIX}{name}",
                        export=exported,
                    ) is not None:
                        changed += 1
                conn.commit()
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SYNC_LOCK_KEY})
                conn.commit()
        if exported:
            self.export_to_files()
        return changed

    def export_to_files(self) -> int:
        """Write file mirrors from DB documents (`monoagens config export` and settings tool)."""
        if not self.enabled:
            return 0
        saved = 0
        raw = self._config()
        engine = self.get("engine")
        if engine is not None:
            raw["engine"] = _yaml_dict(engine, "document engine")
            _atomic_write(self.paths.config, _dump(raw))
            saved += 1
        soul = self.get("soul")
        if soul is not None:
            _atomic_write(self.paths.soul_yaml, soul)
            saved += 1
        roles = {
            name.split(":", 1)[1]: _yaml_dict(self.get(name) or "", name)
            for name in self.names(ROLES_PREFIX)
            if ":" in name
        }
        if roles:
            _atomic_write(self.paths.roles, _dump({"roles": roles}), mode=ROLE_MODE)
            saved += 1
        return saved

    def import_from_files(self) -> int:
        """Read host files and upsert documents (`monoagens config import`)."""
        if not self.enabled:
            return 0
        saved = 0
        engine = self._config().get("engine")
        if isinstance(engine, dict):
            self.put("engine", _dump(engine), actor="import")
            saved += 1
        soul = self._soul_file()
        if soul:
            self.put("soul", soul, actor="import")
            saved += 1
        for name, body in self._roles_file().items():
            self.put(f"{ROLES_PREFIX}{name}", _dump(body), actor="import")
            saved += 1
        if "persona" in set(self.names()):
            self.delete("persona")
        return saved
