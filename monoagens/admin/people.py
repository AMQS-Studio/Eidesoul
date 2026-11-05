"""Operator identity commands: list, link, and unlink."""


import logging
import os
import time

import yaml
from sqlalchemy import select

from ..engine.access.identity import IdentityStore, new_person_id
from ..engine.access.roles import PROTECTED, parse_role
from ..engine.db import Database
from ..engine.schema import config_changes, people, person_links
from ..engine.stores.documents import DocumentStore

log = logging.getLogger(__name__)

CLI_ADDRESS = "local"


def summary(db: Database) -> list[str]:
    """One line per identity: id, name, role, and every linked address (operator terminal)."""
    identity = IdentityStore(db)
    lines: list[str] = []
    for row in db.fetch_all(select(people).order_by(people.c.person_id)):
        person = str(row["person_id"])
        links = ", ".join(
            f"{item['gateway']}:{item['address']}" for item in identity.links(person)
        )
        lines.append(f"{person}  {row['name'] or '-'}  {row['role'] or '-'}  {links}".rstrip())
    return lines


def link(db: Database, person_id: str, gateway: str, address: str) -> str:
    """Link one address; a missing `people` row is created first so an operator can seed one."""
    person = str(person_id or "").strip()
    if not person:
        return "missing"
    identity = IdentityStore(db)
    status = identity.link(person, gateway, address, actor="admin")
    if status != "missing":
        return status
    with db.connect() as conn:
        db.upsert(conn, people, [{"person_id": person}], ["person_id"])
    return identity.link(person, gateway, address, actor="admin")


def unlink(db: Database, person_id: str, gateway: str) -> bool:
    return IdentityStore(db).unlink(person_id, gateway)


def _read_roles(paths) -> dict:
    if not paths.roles.is_file():
        raise ValueError(f"no roles found: {paths.roles} (run `monoagens init`)")
    try:
        raw = yaml.safe_load(paths.roles.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"roles.yaml is invalid: {exc}") from exc
    roles = raw.get("roles") if isinstance(raw, dict) else None
    if not isinstance(roles, dict):
        raise ValueError(f"roles.yaml must contain a 'roles' mapping: {paths.roles}")
    return roles


def _write_roles(paths, roles: dict) -> None:
    """ADR-0046 §4.1: the operator file is written first, then the cache follows."""
    path = paths.roles
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump({"roles": roles}, sort_keys=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def add(
    db: Database,
    paths,
    gateway: str,
    address: str,
    role: str,
    *,
    name: str = "",
) -> str:
    """ADR-0036 §4.6: create/link the first identity and append it to one role.

    ADR-0046 §4.1: roles.yaml holds the definitions and the membership list; the DB keeps the
    identity/link records and a derived `role:<name>` cache row.
    """
    from ..engine.memory.ledger import ActionLedger

    gateway = str(gateway or "").strip()
    address = str(address or "").strip()
    role_name = str(role or "").strip().lower()
    if not gateway or not address:
        raise ValueError("gateway and address are required")
    document = f"role:{role_name}"
    roles = _read_roles(paths)
    body = roles.get(role_name)
    if not isinstance(body, dict):
        raise ValueError(f"role '{role_name}' does not exist")
    parsed = parse_role(role_name, body, document)
    if not parsed.users and role_name != PROTECTED:
        raise ValueError(f"the first user must be '{PROTECTED}'")
    with db.connect() as conn:
        found = conn.execute(
            select(person_links.c.person_id).where(
                person_links.c.gateway == gateway,
                person_links.c.address == address,
            )
        ).scalar()
        person_id = str(found or "") or new_person_id()
        now = time.time()
        if found is None:
            conn.execute(
                people.insert().values(
                    person_id=person_id,
                    name=str(name or "").strip(),
                )
            )
            conn.execute(
                person_links.insert().values(
                    gateway=gateway,
                    address=address,
                    person_id=person_id,
                    created_at=now,
                    seen_at=now,
                    linked_by="people.add",
                )
            )
        elif name.strip():
            conn.execute(
                people.update()
                .where(people.c.person_id == person_id)
                .values(name=str(name).strip())
            )
        users = list(parsed.users)
        if person_id not in users:
            users.append(person_id)
        config_changes_row = {
            "ts": now,
            "actor": "people add",
            "file": "roles.yaml",
            "path": f"roles.{role_name}.users",
            "old": ", ".join(parsed.users),
            "new": ", ".join(users),
        }
    new_body = dict(body)
    new_body["users"] = users
    roles[role_name] = new_body
    _write_roles(paths, roles)
    with db.connect() as conn:
        conn.execute(config_changes.insert().values(config_changes_row))
    DocumentStore(db, paths, sync=True).put(
        document, yaml.safe_dump(new_body, sort_keys=False), actor="people add"
    )
    ActionLedger(db).log_action(
        "person", person_id, "config", f"people add {gateway}:{address} role={role_name}"
    )
    return person_id
