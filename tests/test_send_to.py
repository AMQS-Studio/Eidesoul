"""send_to contract: the target must be explicit; a single contact is never a fallback."""


import asyncio

from support import make_agent, role, specs
from test_whatsapp_gateway import make_gateway

from eidesoul.engine.tools import execute
from eidesoul.engine.turn.context import Message
from eidesoul.gateways.whatsapp import directory


def test_unknown_target_never_falls_back_to_the_only_contact(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    directory._upsert_contact(gateway.db, "628999999999@s.whatsapp.net", "Budi", "")
    env = make_agent(
        tmp_path,
        gateway=gateway,
        roles={"owner": role("owner", tools=("send_to",), commands=("*",))},
        default_role="owner",
    )
    ctx = env.agent.tool_context("111@g.us", "628111", (env.config.roles.get("owner"),), gateway)

    result = asyncio.run(
        execute(specs(env), "send_to", {"chat": "tidak-ada@g.us", "text": "x"}, ctx)
    )

    assert result.startswith("error:")
    assert "is not in the archive" in result
    assert gateway.client is None  # no transport was used


def test_send_to_listing_without_chat(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    directory._upsert_contact(gateway.db, "628999999999@s.whatsapp.net", "Budi", "")
    env = make_agent(
        tmp_path,
        gateway=gateway,
        roles={"owner": role("owner", tools=("send_to",), commands=("*",))},
        default_role="owner",
    )
    ctx = env.agent.tool_context("111@g.us", "628111", (env.config.roles.get("owner"),), gateway)

    result = asyncio.run(execute(specs(env), "send_to", {}, ctx))

    assert "Contacts in the archive" in result
    assert "Budi" in result


TARGET = "628999999999@s.whatsapp.net"


class FailingClient:
    async def send_message(self, jid, payload):
        raise RuntimeError("koneksi putus")


def cross_env(tmp_path, role_tools=("send_to", "chat_history", "*")):

    gateway = make_gateway(tmp_path)
    directory._upsert_contact(gateway.db, TARGET, "Budi", "")
    # ADR-0007 §4.5: the requester must be provable in the target room before a cross send.
    gateway.participants = lambda thread: {"628111"} if thread == TARGET else None
    env = make_agent(
        tmp_path,
        gateway=gateway,
        roles={"owner": role("owner", tools=role_tools, commands=("*",))},
        default_role="owner",
    )
    env.history.add(
        TARGET,
        Message(
            id="b1",
            sender_name="Budi",
            sender_id="628999999999",
            when=__import__("datetime").datetime(2026, 1, 1, 10),
            text="halo dari budi",
            author="manual_owner",
            visibility=3,
        ),
    )
    ctx = env.agent.tool_context(
        "111@g.us", "628111", (env.config.roles.get("owner"),), gateway
    )
    return env, ctx


def test_cross_chat_send_refused_before_reading_the_target(tmp_path) -> None:
    env, ctx = cross_env(tmp_path)

    result = asyncio.run(
        execute(specs(env), "send_to", {"chat": "budi", "text": "halo"}, ctx)
    )

    assert "read the archive of Budi" in result
    assert "(628999999999@s.whatsapp.net) first" in result
    assert "chat_history" in result
    assert env.gateway.client is None


def test_cross_chat_send_works_after_reading_the_target(tmp_path) -> None:
    from test_whatsapp_gateway import FakeClient

    env, ctx = cross_env(tmp_path)
    env.gateway.client = FakeClient()

    read = asyncio.run(execute(specs(env), "chat_history", {"chat": "budi"}, ctx))
    result = asyncio.run(
        execute(
            specs(env),
            "send_to",
            {"chat": "budi", "text": "halo", "reason": "jawab budi"},
            ctx,
        )
    )

    assert "halo dari budi" in read
    assert result.startswith("ok: sent to Budi")
    assert [call[0] for call in env.gateway.client.calls] == ["message"]
    assert "cross_send" in env.agent.ledger.actions_context()


def test_failed_cross_chat_send_is_logged_as_failure(tmp_path) -> None:
    env, ctx = cross_env(tmp_path)
    env.gateway.client = FailingClient()
    ctx.reads.add(TARGET)

    result = asyncio.run(
        execute(
            specs(env),
            "send_to",
            {"chat": "budi", "text": "halo", "reason": "jawab budi"},
            ctx,
        )
    )

    assert result.startswith("error: sending to Budi (628999999999@s.whatsapp.net) failed:")
    assert "koneksi putus" in result
    actions = env.agent.ledger.actions_context()
    assert "failed — error: failed to send text: RuntimeError: koneksi putus" in actions
    assert ctx.sent == []
