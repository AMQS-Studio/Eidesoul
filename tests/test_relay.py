"""ADR-0023 relay consent: no grant, no content; `none`, expiry, and revocation refuse.

The grant row is keyed `(task, from, to)` and holds the decision only. The consent question uses
the existing approval path, and only the content owner may decide or revoke it.
"""


import asyncio
import time

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from support import inbound_message, make_agent, role, specs
from test_whatsapp_gateway import FakeClient, make_gateway

from eidesoul.engine.access.relay import MODES, RelayRefused, RelayStore
from eidesoul.engine.inbound import Address
from eidesoul.engine.schema import people, person_links, relay_grants
from eidesoul.engine.tools import execute
from eidesoul.gateways.whatsapp import directory

TASK = "task-42"
FROM = "per_b"
TO = "per_a"
SOURCE = "628222222222@s.whatsapp.net"  # B's DM with the soul
TARGET = "628111111111@s.whatsapp.net"  # A's DM
ANSWER = "pekerjaan macet di lokasi, selesai minggu depan"


def build(tmp_path):
    gateway = make_gateway(tmp_path)
    gateway.client = FakeClient()
    directory._upsert_contact(gateway.db, TARGET, "A", "")
    with gateway.db.connect() as conn:
        gateway.db.upsert(
            conn,
            people,
            [{"person_id": TO, "name": "A"}, {"person_id": FROM, "name": "Budi"}],
            ["person_id"],
        )
        gateway.db.upsert(
            conn,
            person_links,
            [
                {"gateway": "whatsapp", "address": TARGET, "person_id": TO, "created_at": 1.0},
                {"gateway": "whatsapp", "address": SOURCE, "person_id": FROM, "created_at": 1.0},
            ],
            ["gateway", "address"],
        )
    administrator = role("administrator", tools=("*",), commands=("*",), memory_admin=True)
    env = make_agent(
        tmp_path,
        gateway=gateway,
        roles={"administrator": administrator},
        default_role="administrator",
    )
    ctx = env.agent.tool_context(SOURCE, FROM, (administrator,), gateway, person_id=FROM)
    ctx.reads.add(TARGET)
    return env, ctx


def send(env, ctx, *, relay=True, text=ANSWER, reason=""):
    args = {"chat": "A", "text": text}
    if relay:
        args.update({"relay_task": TASK, "relay_from": FROM})
    elif reason:
        args["reason"] = reason
    return asyncio.run(execute(specs(env), "send_to", args, ctx))


def ask(env, *, expires_at=None):
    return asyncio.run(
        RelayStore(env.agent.db).ask(
            env.agent,
            task_id=TASK,
            from_person=FROM,
            to_person=TO,
            scope_ref=SOURCE,
            thread_id=SOURCE,
            address=Address(env.gateway.name, SOURCE),
            to_label="A",
            topic="the obstacles",
            expires_at=expires_at,
        )
    )


def decide(env, approval, mode):
    return RelayStore(env.agent.db).decide(approval=approval, mode=mode, by=FROM)


def test_a_missing_grant_refuses_the_relay(tmp_path) -> None:
    env, ctx = build(tmp_path)

    result = send(env, ctx)

    assert result.startswith("error: relay refused")
    assert "no relay grant" in result
    assert env.gateway.client.calls == []


def test_none_is_stored_and_still_refuses_content(tmp_path) -> None:
    env, ctx = build(tmp_path)
    approval = ask(env)

    grant = RelayStore(env.agent.db).get(task_id=TASK, from_person=FROM, to_person=TO)
    assert grant is not None
    assert grant.mode == "none"
    assert grant.decided_via == ""
    assert approval.kind == "consent"

    result = send(env, ctx)

    assert "chose not to forward" in result
    assert len(env.gateway.client.calls) == 1  # only the question, never the answer


def test_no_answer_defaults_to_none(tmp_path) -> None:
    env, _ctx = build(tmp_path)
    ask(env)

    grant, reason = RelayStore(env.agent.db).check(
        task_id=TASK, from_person=FROM, to_person=TO
    )

    assert grant is None
    assert "chose not to forward" in reason


def test_expiry_refuses_the_next_relay(tmp_path) -> None:
    env, ctx = build(tmp_path)
    approval = ask(env, expires_at=time.time() - 1)
    decide(env, approval, "full")

    result = send(env, ctx)

    assert "has expired" in result
    assert len(env.gateway.client.calls) == 1


def test_revocation_stops_the_next_report(tmp_path) -> None:
    env, ctx = build(tmp_path)
    approval = ask(env)
    grant = decide(env, approval, "full")
    assert grant.mode == "full"

    assert RelayStore(env.agent.db).revoke(
        task_id=TASK, from_person=FROM, to_person=TO, by=FROM
    )

    result = send(env, ctx)
    assert "was revoked" in result
    assert len(env.gateway.client.calls) == 1


def test_a_valid_grant_allows_the_relay_without_content_in_the_ledger(tmp_path) -> None:
    env, ctx = build(tmp_path)
    approval = ask(env)
    decide(env, approval, "summary")

    result = send(env, ctx)

    assert result.startswith("ok: sent to A")
    assert [call[0] for call in env.gateway.client.calls[1:]] == ["message"]
    actions = env.agent.ledger.actions_context(tz="UTC")
    assert "relay" in actions
    assert ANSWER not in actions
    assert ANSWER not in str(ctx.sent)


def test_the_grant_row_carries_no_content(tmp_path) -> None:
    env, _ctx = build(tmp_path)
    approval = ask(env)
    decide(env, approval, "full")

    row = env.agent.db.fetch_one(select(relay_grants).where(relay_grants.c.task_id == TASK))

    assert row is not None
    assert set(relay_grants.c.keys()) == {
        "id",
        "task_id",
        "from_person",
        "to_person",
        "mode",
        "scope_ref",
        "created_at",
        "expires_at",
        "revoked_at",
        "decided_via",
    }
    assert ANSWER not in str(row)
    assert row["from_person"] == FROM
    assert row["to_person"] == TO


def test_decided_via_is_recorded(tmp_path) -> None:
    env, _ctx = build(tmp_path)
    approval = ask(env)

    grant = decide(env, approval, "anonymous")

    assert grant.decided_via == "consent"
    assert grant.mode == "anonymous"


def test_only_the_content_owner_can_decide_or_revoke(tmp_path) -> None:
    env, _ctx = build(tmp_path)
    approval = ask(env)

    with pytest.raises(RelayRefused, match="content owner"):
        RelayStore(env.agent.db).decide(approval=approval, mode="full", by=TO)
    with pytest.raises(RelayRefused, match="content owner"):
        RelayStore(env.agent.db).revoke(task_id=TASK, from_person=FROM, to_person=TO, by=TO)

    grant = RelayStore(env.agent.db).get(task_id=TASK, from_person=FROM, to_person=TO)
    assert grant.mode == "none"
    assert grant.decided_via == ""


def test_another_person_cannot_approve_the_question(tmp_path) -> None:
    env, _ctx = build(tmp_path)
    approval = ask(env)
    administrator = env.config.roles.get("administrator")

    result = asyncio.run(
        env.agent._command(
            "approve",
            f"{approval.id} full",
            SOURCE,
            TO,
            (administrator,),
            env.gateway,
            inbound_message("/approve", thread=SOURCE, person_id=TO, sender=TO),
        )
    )

    assert "another chat or sender" in result or "pengirim lain" in result
    grant = RelayStore(env.agent.db).get(task_id=TASK, from_person=FROM, to_person=TO)
    assert grant.mode == "none"


def test_approve_command_records_the_chosen_mode(tmp_path) -> None:
    env, _ctx = build(tmp_path)
    approval = ask(env)
    administrator = env.config.roles.get("administrator")

    result = asyncio.run(
        env.agent._command(
            "approve",
            f"{approval.id} summary",
            SOURCE,
            FROM,
            (administrator,),
            env.gateway,
            inbound_message("/approve", thread=SOURCE, person_id=FROM, sender=FROM),
        )
    )

    assert "summary" in result
    grant = RelayStore(env.agent.db).get(task_id=TASK, from_person=FROM, to_person=TO)
    assert grant.mode == "summary"
    assert grant.decided_via == "consent"
    assert env.agent.approvals.get(approval.id).status == "approved"


def test_approve_without_a_mode_keeps_the_question_pending(tmp_path) -> None:
    env, _ctx = build(tmp_path)
    approval = ask(env)
    administrator = env.config.roles.get("administrator")

    result = asyncio.run(
        env.agent._command(
            "approve",
            str(approval.id),
            SOURCE,
            FROM,
            (administrator,),
            env.gateway,
            inbound_message("/approve", thread=SOURCE, person_id=FROM, sender=FROM),
        )
    )

    assert "choose a relay mode" in result or "pilih mode relay" in result
    assert env.agent.approvals.get(approval.id).status == "pending"
    assert RelayStore(env.agent.db).get(task_id=TASK, from_person=FROM, to_person=TO).mode == "none"


def test_anonymous_hides_the_source_name(tmp_path) -> None:
    env, ctx = build(tmp_path)
    approval = ask(env)
    decide(env, approval, "anonymous")

    named = send(env, ctx, text=f"{ANSWER} — kata Budi")
    assert "must not name its source" in named
    assert len(env.gateway.client.calls) == 1

    unnamed = send(env, ctx, text=ANSWER)
    assert unnamed.startswith("ok: sent to A")
    assert len(env.gateway.client.calls) == 2


def test_a_plain_cross_send_needs_no_grant(tmp_path) -> None:
    env, ctx = build(tmp_path)
    # ADR-0007 §4.5: the requester must be provable in the target room; the grant is the
    # ADR-0023 concern this test is about.
    env.gateway.participants = lambda thread: {FROM, TO} if thread == TARGET else None

    result = send(env, ctx, relay=False, text="halo dari B", reason="menjawab A")

    assert result.startswith("ok: sent to A")
    assert [call[0] for call in env.gateway.client.calls] == ["message"]


def test_a_relay_needs_both_arguments(tmp_path) -> None:
    env, ctx = build(tmp_path)
    ctx.reads.add(TARGET)

    result = asyncio.run(
        execute(
            specs(env),
            "send_to",
            {"chat": "A", "text": ANSWER, "relay_task": TASK},
            ctx,
        )
    )

    assert "needs both relay_task and relay_from" in result


def test_an_unprovable_recipient_is_refused(tmp_path) -> None:
    env, ctx = build(tmp_path)
    with env.gateway.db.connect() as conn:
        conn.execute(
            person_links.delete().where(
                person_links.c.gateway == "whatsapp",
                person_links.c.address == TARGET,
            )
        )
    approval = ask(env)
    decide(env, approval, "full")

    result = send(env, ctx)

    assert "cannot prove the relay recipient" in result


def test_one_grant_per_task_and_pair(tmp_path) -> None:
    env, _ctx = build(tmp_path)
    ask(env)
    again = ask(env)
    # the row exists at ask time, so the second question reuses the pending approval
    assert again is not None

    with pytest.raises(IntegrityError), env.agent.db.connect() as conn:
        conn.execute(
            relay_grants.insert().values(
                task_id=TASK,
                from_person=FROM,
                to_person=TO,
                mode="full",
                scope_ref=SOURCE,
                created_at=time.time(),
            )
        )


def test_mode_check_constraint(tmp_path) -> None:
    env, _ctx = build(tmp_path)

    with pytest.raises(IntegrityError), env.agent.db.connect() as conn:
        conn.execute(
            relay_grants.insert().values(
                task_id="other",
                from_person=FROM,
                to_person=TO,
                mode="everything",
                scope_ref=SOURCE,
                created_at=time.time(),
            )
        )


def test_modes_are_the_four_the_adr_names() -> None:
    assert MODES == ("full", "summary", "anonymous", "none")
