"""Administrator notification: one alert goes to the last thread an administrator wrote in."""

import logging
from typing import TYPE_CHECKING

from .. import io

if TYPE_CHECKING:
    from ..access.roles import RoleRegistry
    from ..agent import Agent
    from ..db import Database

log = logging.getLogger(__name__)


def route(db: Database, roles: RoleRegistry) -> tuple[str, str, str] | None:
    """The newest thread an administrator person wrote in, with its owning gateway."""
    from sqlalchemy import select

    from ..schema import messages, threads

    people = tuple(roles.require_administrator().users)
    if not people:
        return None
    row = db.fetch_one(
        select(messages.c.thread_id, messages.c.sender_id)
        .where(messages.c.sender_id.in_(people), messages.c.outgoing.is_(False))
        .order_by(messages.c.ts.desc())
        .limit(1)
    )
    if row is None:
        return None
    thread = str(row["thread_id"])
    found = db.fetch_one(select(threads.c.gateway).where(threads.c.thread_id == thread))
    name = str((found or {}).get("gateway") or "")
    if not name:
        return None
    return thread, name, str(row["sender_id"])


async def notify_admin(agent: Agent, text: str) -> None:
    """Deliver one alert through the one door, with an explicit origin (ADR-0009)."""
    from ..inbound import Address

    found = await io.run(route, agent.db, agent.config.roles)
    if found is None:
        log.info("alert has no administrator route; log only")
        return
    thread, gateway_name, subject = found
    body = agent.body(gateway_name)
    if body is None:
        log.info("alert gateway %s is not attached; log only", gateway_name)
        return
    await agent.send_text(
        thread,
        text,
        gateway=body,
        subject=subject,
        origin=Address(body.name, thread),
        reason="alert",
    )
