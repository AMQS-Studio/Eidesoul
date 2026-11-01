"""Operator identity commands: list, link, unlink, and adopt legacy addresses."""

from __future__ import annotations

import logging
import re

import yaml
from sqlalchemy import select

from ..engine.db import Database
from ..engine.identity import IdentityStore, is_person_id
from ..engine.memory import build_memory
from ..engine.memory.rekey import rekey_memory
from ..engine.schema import chat_people, google_device, google_identity, people
from ..engine.stores.documents import ROLES_PREFIX, DocumentStore
from ..engine.stores.style import StyleStore

log = logging.getLogger(__name__)

DIGITS = re.compile(r"\d{6,}")
CLI_ADDRESS = "local"
LEGACY_GATEWAY = "legacy"


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


def _address(value: str) -> tuple[str, str]:
    """Legacy ids are WhatsApp digits, the CLI user, or an unknown address on a pseudo-gateway."""
    if DIGITS.fullmatch(value):
        return ("whatsapp", value)
    if value == CLI_ADDRESS:
        return ("cli", value)
    return (LEGACY_GATEWAY, value)


def _yaml_dict(text: str) -> dict:
    try:
        body = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        log.warning("adopt: a role document is not valid YAML")
        return {}
    return body if isinstance(body, dict) else {}


def _role_users(documents: DocumentStore | None) -> set[str]:
    if documents is None:
        return set()
    found: set[str] = set()
    for name in documents.names(ROLES_PREFIX):
        users = _yaml_dict(documents.get(name) or "").get("users")
        if isinstance(users, list):
            found.update(str(item) for item in users)
    return found


def _legacy_ids(
    db: Database, documents: DocumentStore | None, style: StyleStore
) -> list[str]:
    found: set[str] = set(_role_users(documents))
    found.update(str(item) for item in style.people_ids())
    for table in (people, chat_people, google_identity, google_device):
        for row in db.fetch_all(select(table.c.person_id)):
            found.add(str(row["person_id"]))
    return sorted(item for item in found if item and not is_person_id(item))


def _rewrite_roles(documents: DocumentStore, mapping: dict[str, str]) -> int:
    """Replace legacy users with their `per_…` id in every role document."""
    changed = 0
    for name in documents.names(ROLES_PREFIX):
        body = _yaml_dict(documents.get(name) or "")
        users = body.get("users")
        if not isinstance(users, list):
            continue
        items = [str(item) for item in users]
        replaced = [mapping.get(item, item) for item in items]
        if replaced == items:
            continue
        body["users"] = replaced
        documents.put(name, yaml.safe_dump(body, sort_keys=False), actor="adopt")
        changed += 1
    return changed


async def adopt(
    db: Database,
    config=None,
    documents: DocumentStore | None = None,
    *,
    memory=None,
) -> list[tuple[str, str, str, str]]:
    """Move every legacy address onto a `per_…` id; idempotent, so a partial run can repeat.

    Returns `(old_id, new_id, gateway, address)` rows for the addresses that were migrated.
    """
    identity = IdentityStore(db)
    style = StyleStore(db, config.style) if config is not None else StyleStore(db)
    if documents is None and config is not None:
        documents = DocumentStore(db, config.paths)
    if memory is None and config is not None:
        memory = await build_memory(config, db=db)
    mapping: dict[str, str] = {}
    rows: list[tuple[str, str, str, str]] = []
    for old in _legacy_ids(db, documents, style):
        gateway, address = _address(old)
        new = identity.resolve(gateway, address)
        identity.merge(old, new)
        style.merge_people(old, new)
        mapping[old] = new
        rows.append((old, new, gateway, address))
    if documents is not None:
        _rewrite_roles(documents, mapping)
        documents.export_to_files()
    if memory is not None and getattr(memory, "available", False):
        for old, new in mapping.items():
            await rekey_memory(memory, old, new, db=db)
    return rows
