"""Operator identity commands: list, link, unlink, add, and adopt.

`roles.yaml` is the only membership source: a person's roles are the blocks whose `users`
list holds their `per_…` id. Addresses enter the store in the gateway's own canonical shape,
so an operator who types `+62812…` and a gateway that reports `62812…` agree.
"""


import logging
import time
from pathlib import Path

import yaml
from sqlalchemy import select, update

from ..engine.access.identity import IdentityStore, is_person_id, new_person_id
from ..engine.access.roles import PROTECTED, parse_role
from ..engine.db import Database
from ..engine.memory.ledger import ActionLedger
from ..engine.schema import config_changes, people, person_links, thread_people
from ..engine.stores.documents import DocumentStore
from .gateway import normalize_address, update_document

log = logging.getLogger(__name__)

CLI_ADDRESS = "local"
ROLE_PREFIX = "role:"


def _read_roles(paths) -> dict:
    if paths is None:
        return {}
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


def _update_users(paths, updates: dict) -> None:
    """Write only the `users` lists; every other value and comment stays put."""
    path = Path(paths.roles)
    path.parent.mkdir(parents=True, exist_ok=True)
    update_document(path, updates)


def role_members(roles: dict) -> dict[str, list[str]]:
    """person id -> the role blocks whose `users` list names it."""
    found: dict[str, list[str]] = {}
    for role, body in roles.items():
        users = body.get("users") if isinstance(body, dict) else None
        for person in users or []:
            found.setdefault(str(person), []).append(str(role))
    return found


def summary(db: Database, paths=None) -> list[str]:
    """One line per identity: id, name, roles from roles.yaml, and every linked address."""
    identity = IdentityStore(db)
    try:
        roles = _read_roles(paths) if paths is not None else {}
    except ValueError:
        roles = {}
    members = role_members(roles)
    lines: list[str] = []
    for row in db.fetch_all(select(people).order_by(people.c.person_id)):
        person = str(row["person_id"])
        links = ", ".join(
            f"{item['gateway']}:{item['address']}" for item in identity.links(person)
        )
        role = ", ".join(members.get(person, [])) or "-"
        lines.append(f"{person}  {row['name'] or '-'}  {role}  {links}".rstrip())
    return lines


def _notify(paths, reason: str) -> None:
    """roles.yaml changed: ask the primary to reload (a broken sibling file only warns)."""
    from . import service

    service.notify(str(paths.config), reason=reason)


def link(db: Database, person_id: str, gateway: str, address: str) -> str:
    """Link one address; a missing `people` row is created first so an operator can seed one."""
    person = str(person_id or "").strip()
    if not person:
        return "missing"
    address = normalize_address(gateway, address)
    identity = IdentityStore(db)
    status = identity.link(person, gateway, address, actor="admin")
    if status != "missing":
        return status
    with db.connect() as conn:
        db.upsert(conn, people, [{"person_id": person}], ["person_id"])
    return identity.link(person, gateway, address, actor="admin")


def unlink(db: Database, person_id: str, gateway: str) -> bool:
    return IdentityStore(db).unlink(person_id, gateway)


def _administrator_users(roles: dict) -> list[str]:
    body = roles.get(PROTECTED)
    users = body.get("users") if isinstance(body, dict) else None
    return [str(item) for item in users or []]


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
    identity/link records and a derived `role:<name>` cache row. The first-user rule is a
    deployment rule: an administrator must exist before any other role gains a user.
    """
    gateway = str(gateway or "").strip()
    address = normalize_address(gateway, address)
    role_name = str(role or "").strip().lower()
    if not gateway or not address:
        raise ValueError("gateway and address are required")
    document = f"{ROLE_PREFIX}{role_name}"
    roles = _read_roles(paths)
    body = roles.get(role_name)
    if not isinstance(body, dict):
        raise ValueError(f"role '{role_name}' does not exist")
    parsed = parse_role(role_name, body, document)
    if not _administrator_users(roles) and role_name != PROTECTED:
        raise ValueError(
            f"the first user must be '{PROTECTED}'; add one before seeding role '{role_name}'"
        )
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
    _update_users(paths, {f"roles.{role_name}.users": users})
    with db.connect() as conn:
        conn.execute(config_changes.insert().values(config_changes_row))
    DocumentStore(db, paths, sync=True).put(
        document, yaml.safe_dump(new_body, sort_keys=False), actor="people add"
    )
    ActionLedger(db).log_action(
        "person", person_id, "config", f"people add {gateway}:{address} role={role_name}"
    )
    _notify(paths, f"people add {role_name}")
    return person_id


def adopt(db: Database, paths) -> list[tuple[str, str]]:
    """Move legacy raw actor ids onto `per_…` identities in the store and in roles.yaml."""
    roles = _read_roles(paths)
    legacy: list[str] = []
    for body in roles.values():
        users = body.get("users") if isinstance(body, dict) else None
        for person in users or []:
            if not is_person_id(str(person)) and str(person) not in legacy:
                legacy.append(str(person))
    moved: list[tuple[str, str]] = []
    changed_users: dict[str, list[str]] = {}
    for old in legacy:
        row = db.fetch_one(select(people).where(people.c.person_id == old))
        if row is None:
            continue
        fresh = new_person_id()
        values = {key: value for key, value in dict(row).items() if key != "person_id"}
        with db.connect() as conn:
            # A primary-key update would violate the children's foreign keys; insert the new
            # identity, move the links, then delete the legacy row in one transaction.
            conn.execute(people.insert().values(person_id=fresh, **values))
            conn.execute(
                update(person_links)
                .where(person_links.c.person_id == old)
                .values(person_id=fresh)
            )
            conn.execute(
                update(thread_people)
                .where(thread_people.c.person_id == old)
                .values(person_id=fresh)
            )
            conn.execute(people.delete().where(people.c.person_id == old))
        for role, body in roles.items():
            if not isinstance(body, dict):
                continue
            users = body.get("users")
            if isinstance(users, list) and old in users:
                changed_users[str(role)] = [
                    fresh if str(item) == old else item for item in users
                ]
        moved.append((old, fresh))
    if moved:
        _update_users(
            paths,
            {f"roles.{role}.users": users for role, users in changed_users.items()},
        )
        for old, fresh in moved:
            DocumentStore(db, paths, sync=True).put(
                f"{ROLE_PREFIX}adopted", f"{old} -> {fresh}", actor="people adopt"
            )
            ActionLedger(db).log_action("person", fresh, "config", f"people adopt {old}")
        _notify(paths, "people adopt")
    return moved