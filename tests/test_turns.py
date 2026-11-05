"""Turn contract: percept hints persist and a resumed turn keeps its person (ADR-0001/0003)."""


import asyncio

from sqlalchemy import select
from support import inbound_message, make_agent

from monoagens.engine.schema import people, person_links
from monoagens.engine.stores.turns import turn_identity

DM = "628111@s.whatsapp.net"


def turn(env, message_id: str, thread: str = DM):
    return env.agent.turns.get(turn_identity(env.gateway.name, thread, message_id))
HINTS = {"dm": True, "mention": True, "reply": True, "app": "wa"}


def test_intake_persists_the_percept_hints(tmp_path) -> None:
    env = make_agent(tmp_path)
    inbound = inbound_message(
        "halo", thread=DM, message_id="hints-1", mention=True, reply=True, app="wa"
    )

    assert (
        env.history.intake(
            DM,
            inbound.payload,
            inbound.actor,
            language="id",
            gateway=env.gateway.name,
            hints=dict(inbound.hints),
        )
        is True
    )

    stored = turn(env, "hints-1")
    assert stored["hints"] == HINTS


def test_intake_defaults_hints_to_an_empty_object(tmp_path) -> None:
    env = make_agent(tmp_path)
    inbound = inbound_message("halo", thread=DM, message_id="hints-2")

    env.history.intake(
        DM,
        inbound.payload,
        inbound.actor,
        language="id",
        gateway=env.gateway.name,
    )

    assert turn(env, "hints-2")["hints"] == {}


def test_resume_keeps_the_person_and_restores_hints(tmp_path) -> None:
    """ADR-0001 §4.1/ADR-0003 §4.1: recovery rebuilds the Percept from the turn row."""
    env = make_agent(tmp_path)
    person = env.agent.identity.resolve(env.gateway.name, "628111")
    assert person.startswith("per_")
    inbound = inbound_message(
        "halo",
        thread=DM,
        person_id=person,
        message_id="resume-hints",
        mention=True,
        reply=True,
        app="wa",
    )
    env.history.intake(
        DM,
        inbound.payload,
        person,
        language="id",
        gateway=env.gateway.name,
        hints=dict(inbound.hints),
    )
    # The instance crashed after deciding: the lease is gone, the step is kept.
    env.agent.turns.checkpoint(turn_identity(env.gateway.name, DM, "resume-hints"), "decided")

    seen = []
    original = env.agent.handle

    async def spy(inbound, gateway, *, claimed=None):
        seen.append(inbound)
        return await original(inbound, gateway, claimed=claimed)

    env.agent.handle = spy

    async def run() -> int:
        resumed = await env.agent.recover()
        await asyncio.gather(*tuple(env.agent.tasks))
        return resumed

    assert asyncio.run(run()) == 1
    assert len(seen) == 1
    assert seen[0].actor == person
    assert dict(seen[0].hints) == HINTS

    stored_turn = turn(env, "resume-hints")
    assert stored_turn["status"] == "delivered"
    assert stored_turn["person_id"] == person
    assert stored_turn["hints"] == HINTS
    assert stored_turn["message_id"] == "resume-hints"

    with env.db.connect() as conn:
        people_rows = conn.execute(select(people.c.person_id)).scalars().all()
        links = conn.execute(
            select(person_links.c.gateway, person_links.c.address, person_links.c.person_id)
        ).all()
    assert people_rows == [person]
    assert [(row.gateway, row.address, row.person_id) for row in links] == [
        ("fake", "628111", person)
    ]
