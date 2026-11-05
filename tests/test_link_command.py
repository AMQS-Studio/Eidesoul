"""Command contract: /link and /unlink move one person between gateways."""


import asyncio
import re
import time
from dataclasses import replace

from support import FakeGateway, inbound_message, make_agent, role

from monoagens.engine.commands.link import link
from monoagens.engine.commands.unlink import unlink
from monoagens.engine.commands.whoami import whoami
from monoagens.engine.config import GatewaySettings
from monoagens.engine.soul import StyleConfig
from monoagens.engine.stores.style import StyleStore

SENDER = "6285111242960"
CODE = re.compile(r"/link ([A-Z0-9]{6})")


def build(tmp_path):
    whatsapp = FakeGateway(name="whatsapp")
    signal = FakeGateway(name="signal")
    env = make_agent(tmp_path, gateway=whatsapp, gateways=(whatsapp, signal))
    env.agent.config = replace(
        env.config,
        gateways={
            "whatsapp": GatewaySettings(name="whatsapp", enabled=True),
            "signal": GatewaySettings(name="signal", enabled=True),
            "telegram": GatewaySettings(name="telegram", enabled=False),
        },
    )
    env.agent.style = StyleStore(env.db, StyleConfig(enabled=True), language="id")
    return env, whatsapp, signal


def context(env, gateway, args: str, *, person: str, sender: str = SENDER):
    inbound = inbound_message(f"/x {args}".strip(), sender=sender, person_id=person)
    return env.agent.command_context(
        inbound.thread, sender, (role("member"),), gateway, args, inbound
    )


def test_status_card_masks_addresses_and_hides_disabled_gateways(tmp_path) -> None:
    env, whatsapp, _signal = build(tmp_path)
    person = env.agent.identity.resolve("whatsapp", SENDER)

    text = asyncio.run(link(context(env, whatsapp, "", person=person)))

    assert "🔗 Identitas:" in text
    assert "⛔ signal   belum tertaut" in text
    assert "✅ whatsapp   628••••2960" in text
    assert SENDER not in text
    # the disabled gateway never appears in the status list (the usage example is static text)
    status = text.split("Mau menautkan")[0]
    assert "telegram" not in status


def test_unknown_gateway_lists_the_enabled_ones(tmp_path) -> None:
    env, whatsapp, _signal = build(tmp_path)
    person = env.agent.identity.resolve("whatsapp", SENDER)

    text = asyncio.run(link(context(env, whatsapp, "signalapp", person=person)))

    assert "signalapp" in text
    assert "signal" in text
    assert "telegram" not in text


def test_no_gateway_means_no_link(tmp_path) -> None:
    env, _whatsapp, _signal = build(tmp_path)
    person = env.agent.identity.resolve("whatsapp", SENDER)

    text = asyncio.run(link(context(env, None, "", person=person)))

    assert text.startswith("error:")


def test_finishing_a_code_moves_the_identity_and_its_style_bucket(tmp_path) -> None:
    env, whatsapp, signal = build(tmp_path)
    identity = env.agent.identity
    person = identity.resolve("whatsapp", SENDER)
    provisional = identity.resolve("signal", "local")
    env.agent.style.learn("iyaa aku udah cek, aman kok", person_id=provisional)

    started = asyncio.run(link(context(env, whatsapp, "signal", person=person)))
    found = CODE.search(started)
    assert found is not None, started
    assert identity.links(person) == identity.links(person)  # nothing linked yet
    assert [item["gateway"] for item in identity.links(person)] == ["whatsapp"]

    done = asyncio.run(
        link(context(env, signal, found.group(1), person=provisional, sender="local"))
    )

    assert "✅ Tertaut." in done
    assert identity.person(provisional) is None
    assert [item["gateway"] for item in identity.links(person)] == ["signal", "whatsapp"]
    assert person in env.agent.style.people_ids()
    assert provisional not in env.agent.style.people_ids()


def test_unlink_asks_first_then_removes_the_link(tmp_path) -> None:
    env, whatsapp, _signal = build(tmp_path)
    identity = env.agent.identity
    person = identity.resolve("whatsapp", SENDER)
    identity.link(person, "signal", "local")

    ask = asyncio.run(unlink(context(env, whatsapp, "signal", person=person)))
    assert "confirm" in ask
    assert [item["gateway"] for item in identity.links(person)] == ["signal", "whatsapp"]

    done = asyncio.run(unlink(context(env, whatsapp, "signal confirm", person=person)))
    assert "✅" in done
    assert [item["gateway"] for item in identity.links(person)] == ["whatsapp"]

    missing = asyncio.run(unlink(context(env, whatsapp, "signal confirm", person=person)))
    assert "tidak tertaut" in missing


def test_whoami_shows_the_person_id(tmp_path) -> None:
    env, whatsapp, _signal = build(tmp_path)
    person = env.agent.identity.resolve("whatsapp", SENDER)

    text = asyncio.run(whoami(context(env, whatsapp, "", person=person)))

    assert "🪪 Identitas:" in text
    assert person in text
    assert "🎭 Peran:" in text


def test_a_six_letter_gateway_name_starts_a_code_instead_of_redeeming_it(tmp_path) -> None:
    env, whatsapp, _signal = build(tmp_path)
    person = env.agent.identity.resolve("whatsapp", SENDER)

    text = asyncio.run(link(context(env, whatsapp, "signal", person=person)))

    assert "Kode untuk signal" in text
    assert CODE.search(text) is not None
    assert env.agent.identity.code_target(CODE.search(text).group(1)) == person


def test_six_failed_codes_are_rate_limited(tmp_path) -> None:
    env, whatsapp, _signal = build(tmp_path)
    person = env.agent.identity.resolve("whatsapp", SENDER)
    codes = ("ZZZZZ2", "ZZZZZ3", "ZZZZZ4", "ZZZZZ5", "ZZZZZ6", "ZZZZZ7")

    replies = [
        asyncio.run(link(context(env, whatsapp, code, person=person))) for code in codes
    ]

    assert all("tidak dikenal" in reply for reply in replies[:5])
    assert "Terlalu banyak" in replies[5]


def test_unlink_revokes_pending_approvals_and_relay_grants(tmp_path) -> None:
    from monoagens.engine.access.relay import RelayStore

    env, whatsapp, _signal = build(tmp_path)
    identity = env.agent.identity
    person = identity.resolve("whatsapp", SENDER)
    identity.link(person, "signal", "local")
    approval = asyncio.run(
        env.agent.approvals.create(
            kind="send",
            thread_id="local",
            sender=person,
            gateway="signal",
            summary="waiting",
        )
    )
    relay = RelayStore(env.db)
    relay._upsert(
        task_id="task-1",
        from_person=person,
        to_person="per_abcdefghij",
        scope_ref="local",
        expires_at=None,
        now=time.time(),
        mode="full",
        decided_via="consent",
    )

    text = asyncio.run(unlink(context(env, whatsapp, "signal confirm", person=person)))

    assert "✅" in text
    assert env.agent.approvals.get(approval.id).status == "revoked"
    assert relay.get(task_id="task-1", from_person=person, to_person="per_abcdefghij").revoked_at

