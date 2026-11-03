"""Command contract: /link and /unlink move one person between gateways."""


import asyncio
import re
from dataclasses import replace

from support import FakeGateway, inbound_message, make_agent, role

from eidesoul.engine.commands.link import link
from eidesoul.engine.commands.unlink import unlink
from eidesoul.engine.commands.whoami import whoami
from eidesoul.engine.config import GatewaySettings, StyleConfig
from eidesoul.engine.stores.style import StyleStore

SENDER = "6285111242960"
CODE = re.compile(r"/link ([A-Z0-9]{6})")


def build(tmp_path):
    whatsapp = FakeGateway(name="whatsapp")
    cli = FakeGateway(name="cli")
    env = make_agent(tmp_path, gateway=whatsapp, gateways=(whatsapp, cli))
    env.agent.config = replace(
        env.config,
        gateways={
            "whatsapp": GatewaySettings(name="whatsapp", enabled=True),
            "cli": GatewaySettings(name="cli", enabled=True),
            "telegram": GatewaySettings(name="telegram", enabled=False),
        },
    )
    env.agent.style = StyleStore(env.db, StyleConfig(enabled=True))
    return env, whatsapp, cli


def context(env, gateway, args: str, *, person: str, sender: str = SENDER):
    inbound = inbound_message(f"/x {args}".strip(), sender=sender, person_id=person)
    return env.agent.command_context(
        inbound.thread, sender, (role("member"),), gateway, args, inbound
    )


def test_status_card_masks_addresses_and_hides_disabled_gateways(tmp_path) -> None:
    env, whatsapp, _cli = build(tmp_path)
    person = env.agent.identity.resolve("whatsapp", SENDER)

    text = asyncio.run(link(context(env, whatsapp, "", person=person)))

    assert "🔗 Identitas:" in text
    assert "⛔ cli   belum tertaut" in text
    assert "✅ whatsapp   628••••2960" in text
    assert SENDER not in text
    # the disabled gateway never appears in the status list (the usage example is static text)
    status = text.split("Mau menautkan")[0]
    assert "telegram" not in status


def test_unknown_gateway_lists_the_enabled_ones(tmp_path) -> None:
    env, whatsapp, _cli = build(tmp_path)
    person = env.agent.identity.resolve("whatsapp", SENDER)

    text = asyncio.run(link(context(env, whatsapp, "signalapp", person=person)))

    assert "signalapp" in text
    assert "cli" in text and "telegram" not in text


def test_no_gateway_means_no_link(tmp_path) -> None:
    env, _whatsapp, _cli = build(tmp_path)
    person = env.agent.identity.resolve("whatsapp", SENDER)

    text = asyncio.run(link(context(env, None, "", person=person)))

    assert text.startswith("error:")


def test_finishing_a_code_moves_the_identity_and_its_style_bucket(tmp_path) -> None:
    env, whatsapp, cli = build(tmp_path)
    identity = env.agent.identity
    person = identity.resolve("whatsapp", SENDER)
    provisional = identity.resolve("cli", "local")
    env.agent.style.learn("iyaa aku udah cek, aman kok", person_id=provisional)

    started = asyncio.run(link(context(env, whatsapp, "cli", person=person)))
    found = CODE.search(started)
    assert found is not None, started
    assert identity.links(person) == identity.links(person)  # nothing linked yet
    assert [item["gateway"] for item in identity.links(person)] == ["whatsapp"]

    done = asyncio.run(
        link(context(env, cli, found.group(1), person=provisional, sender="local"))
    )

    assert "✅ Tertaut." in done
    assert identity.person(provisional) is None
    assert [item["gateway"] for item in identity.links(person)] == ["cli", "whatsapp"]
    assert person in env.agent.style.people_ids()
    assert provisional not in env.agent.style.people_ids()


def test_unlink_asks_first_then_removes_the_link(tmp_path) -> None:
    env, whatsapp, _cli = build(tmp_path)
    identity = env.agent.identity
    person = identity.resolve("whatsapp", SENDER)
    identity.link(person, "cli", "local")

    ask = asyncio.run(unlink(context(env, whatsapp, "cli", person=person)))
    assert "confirm" in ask
    assert [item["gateway"] for item in identity.links(person)] == ["cli", "whatsapp"]

    done = asyncio.run(unlink(context(env, whatsapp, "cli confirm", person=person)))
    assert "✅" in done
    assert [item["gateway"] for item in identity.links(person)] == ["whatsapp"]

    missing = asyncio.run(unlink(context(env, whatsapp, "cli confirm", person=person)))
    assert "tidak tertaut" in missing


def test_whoami_shows_the_person_id(tmp_path) -> None:
    env, whatsapp, _cli = build(tmp_path)
    person = env.agent.identity.resolve("whatsapp", SENDER)

    text = asyncio.run(whoami(context(env, whatsapp, "", person=person)))

    assert "🪪 Identitas:" in text and person in text
    assert "🎭 Peran:" in text
