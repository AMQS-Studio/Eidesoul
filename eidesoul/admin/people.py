"""Operator identity commands: list, link, and unlink."""


import logging

from sqlalchemy import select

from ..engine.access.identity import IdentityStore
from ..engine.db import Database
from ..engine.schema import people

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
