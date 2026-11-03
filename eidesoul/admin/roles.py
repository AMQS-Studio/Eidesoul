"""Role-document migration and the protected-role write guard (ADR-0022 §4.2, §4.4).

`eidesoul config import` is the only legal write path for `roles.yaml` and the role documents.
This module normalizes the legacy role name `owner` to `administrator` in one idempotent pass,
writes a backup before touching the file, refuses a write that would delete or rename the
protected role, and records the migration in `config_changes` as `role_rename`.
"""


import contextlib
import os
import time
from pathlib import Path

import yaml
from sqlalchemy import select, update

from ..engine.access.roles import (
    LEGACY,
    MIGRATE,
    PROTECTED,
    RoleError,
    assert_protected_roles,
    parse_role,
)
from ..engine.config import Paths
from ..engine.db import Database
from ..engine.schema import approvals, config_changes
from ..engine.stores.documents import DocumentStore

MIGRATION = "role_rename"


def _dump(body: object) -> str:
    return yaml.safe_dump(body, sort_keys=False, allow_unicode=True)


def _atomic(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    if mode is not None:
        with contextlib.suppress(OSError):
            tmp.chmod(mode)
    os.replace(tmp, path)


def _backup(path: Path) -> Path | None:
    if not path.is_file():
        return None
    target = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    target.write_bytes(path.read_bytes())
    with contextlib.suppress(OSError):
        target.chmod(0o600)
    return target


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RoleError(f"roles.yaml is not readable YAML: {exc}") from exc
    if body is None:
        return {}
    roles = body.get("roles") if isinstance(body, dict) else None
    if roles is None:
        return {}
    if not isinstance(roles, dict):
        raise RoleError("roles.yaml must contain a 'roles' mapping")
    return roles


def _renamed(body: object) -> dict:
    """The same role body under the new identifier; the `name` field follows the key."""
    renamed = dict(body) if isinstance(body, dict) else {"name": PROTECTED}
    declared = str(renamed.get("name") or "").strip()
    if not declared or declared == LEGACY:
        renamed["name"] = PROTECTED
    return renamed


def _audit(db: Database, actor: str) -> None:
    with db.connect() as conn:
        conn.execute(
            config_changes.insert().values(
                ts=time.time(),
                actor=str(actor or ""),
                file="roles.yaml",
                path=MIGRATION,
                old=LEGACY,
                new=PROTECTED,
            )
        )


def _migrate_approval_roles(db: Database) -> int:
    """ADR-0022 §4.4: an old `approvals.roles` list that still names the role is migrated."""
    changed = 0
    rows = db.fetch_all(select(approvals).where(approvals.c.status == "pending"))
    for row in rows:
        payload = row["payload"] if isinstance(row["payload"], dict) else {}
        roles = payload.get("roles")
        if not isinstance(roles, list) or LEGACY not in [str(item) for item in roles]:
            continue
        migrated = [PROTECTED if str(item) == LEGACY else str(item) for item in roles]
        with db.connect() as conn:
            conn.execute(
                update(approvals)
                .where(approvals.c.id == row["id"])
                .values(payload={**payload, "roles": migrated})
            )
        changed += 1
    return changed


def _normalize(roles: dict) -> tuple[dict, bool]:
    """Apply the `owner` → `administrator` rename to the roles.yaml mapping, in memory."""
    before = set(roles)
    if LEGACY in roles and PROTECTED in roles:
        raise RoleError(
            f"roles.yaml names both '{LEGACY}' and '{PROTECTED}'; choose one and run {MIGRATE}"
        )
    if LEGACY in roles:
        roles = {
            (PROTECTED if name == LEGACY else name): (
                _renamed(body) if name == LEGACY else body
            )
            for name, body in roles.items()
        }
    assert_protected_roles(before, set(roles))
    if PROTECTED not in roles:
        raise RoleError(
            f"roles.yaml: the protected role '{PROTECTED}' is missing; it cannot be deleted or "
            f"renamed — restore it in roles.yaml and run {MIGRATE}"
        )
    for name, body in roles.items():
        parse_role(str(name), body, f"roles.yaml:{name}")
    return roles, LEGACY in before


def _document_names(docs: DocumentStore) -> set[str]:
    if not docs.sync:
        return set()
    names = docs.names("role:")
    found = {name.split(":", 1)[1] for name in names if ":" in name}
    if LEGACY in found and PROTECTED in found:
        raise RoleError(
            f"role documents '{LEGACY}' and '{PROTECTED}' both exist; choose one and run {MIGRATE}"
        )
    return found


def _rename_documents(docs: DocumentStore, names: set[str], actor: str) -> int:
    if not docs.sync or LEGACY not in names:
        return 0
    content = docs.get(f"role:{LEGACY}") or ""
    try:
        body = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise RoleError(f"role:{LEGACY} is not valid YAML: {exc}") from exc
    if body is not None and not isinstance(body, dict):
        raise RoleError(f"role:{LEGACY} must be a mapping")
    docs.put(f"role:{PROTECTED}", _dump(_renamed(body)), actor=actor)
    docs.delete(f"role:{LEGACY}")
    return 1


def migrate(
    docs: DocumentStore,
    paths: Paths,
    *,
    actor: str = "config import",
) -> dict:
    """Run the one-shot `owner` → `administrator` migration; idempotent and audited.

    Raises `RoleError` (one clear sentence) when the write would delete or rename the protected
    role, when both names exist (no automatic merge), or when the resulting document is invalid.
    """
    path = Path(paths.roles)
    roles, renamed = _normalize(_read(path))
    doc_names = _document_names(docs)
    backup = None
    if renamed:
        backup = _backup(path)
        _atomic(path, _dump({"roles": roles}), mode=0o600)
    documents = _rename_documents(docs, doc_names, actor)
    migrated_approvals = _migrate_approval_roles(docs.db)
    if renamed or documents or migrated_approvals:
        _audit(docs.db, actor)
    return {
        "renamed": renamed,
        "backup": str(backup) if backup is not None else "",
        "documents": documents,
        "approvals": migrated_approvals,
    }
