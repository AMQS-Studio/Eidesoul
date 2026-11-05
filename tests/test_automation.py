"""Automation: one declarative row, one run per occurrence, one scheduler (ADR-0014)."""


import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from support import inbound_message, make_agent

from monoagens.engine.access.relay import RelayStore
from monoagens.engine.access.roles import Role
from monoagens.engine.jobs.automation_manifest import describe, validate_manifest
from monoagens.engine.jobs.automation_store import AutomationStore
from monoagens.engine.jobs.automation_worker import AutomationWorker
from monoagens.engine.jobs.lease import Lease
from monoagens.engine.jobs.schedule import ManifestError, next_after, occurrence_key
from monoagens.engine.schema import automation_runs, automations, outbound_messages

OWNER = "628111"
TZ = UTC


def at(hour: int, minute: int = 0, *, day: int = 1) -> float:
    return datetime(2026, 1, day, hour, minute, tzinfo=TZ).timestamp()


def manifest(**overrides) -> dict:
    body = {
        "owner": OWNER,
        "name": "rekap",
        "event": {"kind": "schedule", "at": ["10:00"], "tz": "UTC"},
        "steps": [{"kind": "compose"}, {"kind": "send", "to": "owner_dm"}],
        "policy": {
            "budget_per_day": 4,
            "quiet_hours": "22:00-07:00",
            "quiet": "skip",
            "on_error": "notify_owner",
        },
    }
    body.update(overrides)
    return body


def build(tmp_path, *, tools=("*",), automations=None):
    env = make_agent(
        tmp_path,
        roles={
            "member": Role(
                "member", users=(OWNER,), tools=tools, commands=("*",)
            )
        },
        default_role="member",
    )
    if automations is not None:
        env.agent.config = replace(env.agent.config, automations=automations)
    env.agent.mode = "primary"
    env.store = AutomationStore(env.db, env.agent.config.automations)
    seed_dm(env)
    return env


def seed_dm(env, thread: str = OWNER) -> str:
    """ADR-0032 §4.1: a send needs a route, so the owner has a DM thread with the fake body."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from monoagens.engine.schema import people, thread_people, threads

    with env.db.connect() as conn:
        conn.execute(
            pg_insert(people)
            .values(person_id=thread, name="Budi")
            .on_conflict_do_nothing(index_elements=["person_id"])
        )
        conn.execute(
            pg_insert(threads)
            .values(thread_id=thread, name="", reset_ts=0.0, kind="dm", gateway=env.gateway.name)
            .on_conflict_do_nothing(index_elements=["thread_id"])
        )
        conn.execute(
            pg_insert(thread_people)
            .values(thread_id=thread, person_id=thread)
            .on_conflict_do_nothing()
        )
    return thread


def approved(env):
    approval = asyncio.run(
        env.agent.approvals.create(
            kind="automation",
            thread_id=OWNER,
            sender=OWNER,
            tool="",
            args={"manifest": {}},
            payload={"manifest": {}},
            summary="s",
        )
    )
    env.agent.approvals.mark(approval.id, "approved", OWNER)
    return env.agent.approvals.get(approval.id)


def create(env, body: dict, *, now: float) -> int:
    return env.store.create(body, approval=approved(env), agent=env.agent, now=now)


def runs(env) -> list[dict]:
    return env.db.fetch_all(select(automation_runs).order_by(automation_runs.c.id))


def test_occurrence_key_is_deterministic() -> None:
    assert occurrence_key(1, 1000.9) == occurrence_key(1, 1000.1)
    assert occurrence_key(1, 1000) != occurrence_key(1, 1001)
    assert occurrence_key(1, 1000) != occurrence_key(2, 1000)


def test_manifest_requires_owner_and_a_schedule(tmp_path) -> None:
    env = build(tmp_path)
    with pytest.raises(ManifestError):
        create(env, manifest(owner=""), now=at(9, 50))
    with pytest.raises(ManifestError):
        create(
            env,
            manifest(event={"kind": "schedule", "tz": "UTC"}),
            now=at(9, 50),
        )


def test_a_row_is_never_stored_without_approval(tmp_path) -> None:
    env = build(tmp_path)
    with pytest.raises(ManifestError, match="approved approval"):
        env.store.create(manifest(), approval=None, agent=env.agent, now=at(9, 50))
    pending = asyncio.run(
        env.agent.approvals.create(
            kind="automation", thread_id=OWNER, sender=OWNER, summary="s"
        )
    )
    with pytest.raises(ManifestError, match="approved approval"):
        env.store.create(manifest(), approval=pending, agent=env.agent, now=at(9, 50))


def test_authority_is_checked_at_creation(tmp_path) -> None:
    env = build(tmp_path, tools=("read",))
    with pytest.raises(ManifestError, match="'send' tool"):
        create(env, manifest(), now=at(9, 50))


def test_sandbox_program_must_stay_in_the_workspace(tmp_path) -> None:
    env = build(tmp_path)
    body = manifest(steps=[{"kind": "sandbox", "program": "../escape.py"}])
    with pytest.raises(ManifestError, match="inside the workspace"):
        create(env, body, now=at(9, 50))


def test_budget_below_the_occurrence_count_is_rejected_while_authoring(tmp_path) -> None:
    env = build(tmp_path)
    body = manifest(event={"kind": "schedule", "at": ["10:00", "11:00"], "tz": "UTC"})
    body["policy"]["budget_per_day"] = 1
    with pytest.raises(ManifestError, match="exceed budget_per_day"):
        create(env, body, now=at(9, 50))


def test_one_occurrence_is_one_run_and_a_duplicate_tick_never_doubles(tmp_path) -> None:
    env = build(tmp_path)
    create(env, manifest(), now=at(9, 50))
    worker = AutomationWorker(env.agent, store=env.store)

    first = asyncio.run(worker.run_once(now=at(10, 1)))
    second = asyncio.run(worker.run_once(now=at(10, 1)))

    found = runs(env)
    assert len(found) == 1
    assert found[0]["status"] == "succeeded"
    assert first.get("succeeded") == 1
    assert second.get("materialized", 0) == 0
    assert [item[0] for item in env.gateway.sent] == [OWNER]
    assert occurrence_key(1, at(10)) == occurrence_key(1, at(10))
    delivered = env.db.fetch_all(select(outbound_messages))
    assert len(delivered) == 1
    assert delivered[0]["scope_key"] == f"automation:{found[0]['id']}:0"
    assert delivered[0]["status"] == "sent"


def test_a_second_store_instance_does_not_double_the_occurrence(tmp_path) -> None:
    env = build(tmp_path)
    create(env, manifest(), now=at(9, 50))
    other = AutomationStore(env.db, env.agent.config.automations)
    assert env.store.materialize(at(10, 1))["materialized"] == 1
    assert other.materialize(at(10, 1))["materialized"] == 0
    assert len(runs(env)) == 1


def test_quiet_hours_skip_records_skipped_and_sends_nothing(tmp_path) -> None:
    env = build(tmp_path)
    create(
        env,
        manifest(event={"kind": "schedule", "at": ["23:00"], "tz": "UTC"}),
        now=at(22, 55),
    )
    worker = AutomationWorker(env.agent, store=env.store)
    asyncio.run(worker.run_once(now=at(23, 1)))

    found = runs(env)
    assert found[0]["status"] == "skipped"
    assert "quiet" in found[0]["detail"]
    assert env.gateway.sent == []


def test_quiet_hours_defer_runs_once_at_the_window_end(tmp_path) -> None:
    env = build(tmp_path)
    body = manifest(event={"kind": "schedule", "at": ["23:00"], "tz": "UTC"})
    body["policy"]["quiet"] = "defer"
    create(env, body, now=at(22, 55))
    worker = AutomationWorker(env.agent, store=env.store)

    asyncio.run(worker.run_once(now=at(23, 1)))
    deferred = runs(env)[0]
    assert deferred["status"] == "queued"
    assert deferred["deferred"] is True
    assert deferred["scheduled_at"] == at(7, 0, day=2)
    assert env.gateway.sent == []

    asyncio.run(worker.run_once(now=at(7, 1, day=2)))
    assert runs(env)[0]["status"] == "succeeded"
    assert len(env.gateway.sent) == 1


def test_daily_budget_counts_runs_and_skips_the_extra(tmp_path) -> None:
    env = build(tmp_path)
    body = manifest()
    body["policy"]["budget_per_day"] = 1
    automation_id = create(env, body, now=at(9, 50))
    row = env.store.get(automation_id)
    env.store._insert_run(row, at(10, 1), "queued", "", at(9, 55))
    worker = AutomationWorker(env.agent, store=env.store)

    asyncio.run(worker.run_once(now=at(10, 5)))

    found = sorted(runs(env), key=lambda item: item["scheduled_at"])
    assert found[0]["status"] == "succeeded"
    assert found[1]["status"] == "skipped"
    assert "budget" in found[1]["detail"]


def test_global_kill_switch_stops_the_worker(tmp_path) -> None:
    env = build(tmp_path)
    create(env, manifest(), now=at(9, 50))
    config = replace(env.agent.config.automations, kill_switch=True)
    env.agent.config = replace(env.agent.config, automations=config)
    worker = AutomationWorker(env.agent, store=env.store)

    report = asyncio.run(worker.run_once(now=at(10, 1)))

    assert report == {"reaped": 0, "materialized": 0, "missed": 0, "queued": 0, "skipped": 0}
    assert runs(env) == []
    assert env.gateway.sent == []


def test_stopped_automation_cancels_a_queued_run(tmp_path) -> None:
    env = build(tmp_path)
    automation_id = create(env, manifest(), now=at(9, 50))
    row = env.store.get(automation_id)
    env.store._insert_run(row, at(10), "queued", "", at(9, 55))
    env.store.set_state(automation_id, "stopped", reason="user", actor=OWNER, now=at(9, 56))
    worker = AutomationWorker(env.agent, store=env.store)

    asyncio.run(worker.run_once(now=at(10, 1)))

    assert runs(env)[0]["status"] == "cancelled"
    assert env.gateway.sent == []


class BrokenLLM:
    async def complete(self, *args, **kwargs):
        return ""


def test_a_failing_step_retries_then_dead_letters(tmp_path) -> None:
    env = build(tmp_path)
    retry = replace(
        env.agent.config.automations.retry,
        max_attempts=2,
        backoff_seconds=0.0,
        jitter=False,
    )
    env.agent.config = replace(
        env.agent.config,
        automations=replace(env.agent.config.automations, retry=retry),
    )
    env.store = AutomationStore(env.db, env.agent.config.automations)
    create(
        env,
        manifest(steps=[{"kind": "compose"}]),
        now=at(9, 50),
    )
    env.agent.llm = BrokenLLM()
    worker = AutomationWorker(env.agent, store=env.store)

    asyncio.run(worker.run_once(now=at(10, 1)))
    assert runs(env)[0]["status"] == "queued"
    assert runs(env)[0]["attempts"] == 1

    asyncio.run(worker.run_once(now=at(10, 1)))
    found = runs(env)[0]
    assert found["status"] == "failed"
    assert found["attempts"] == 2
    assert "attempts exhausted" in found["detail"]


def test_relay_asks_and_refuses_without_a_grant(tmp_path) -> None:
    env = build(tmp_path)
    create(
        env,
        manifest(steps=[{"kind": "relay", "task_id": "task-1", "to_person": "629999"}]),
        now=at(9, 50),
    )
    worker = AutomationWorker(env.agent, store=env.store)

    asyncio.run(worker.run_once(now=at(10, 1)))

    found = runs(env)[0]
    assert found["status"] == "skipped"
    assert "consent asked" in found["detail"]
    grant, reason = RelayStore(env.db).check(
        task_id="task-1", from_person=OWNER, to_person="629999"
    )
    # The refused row is a real decision (`mode='none'`), so its reason is the honest one.
    assert grant is None
    assert reason
    assert env.agent.approvals.count_pending() == 1
    assert [item[0] for item in env.gateway.sent] == [OWNER]


def test_relay_sends_when_a_grant_exists(tmp_path) -> None:
    env = build(tmp_path)
    automation_id = create(
        env,
        manifest(
            steps=[
                {
                    "kind": "relay",
                    "task_id": "task-1",
                    "to_person": "629999",
                    "text": "laporan proyek",
                }
            ]
        ),
        now=at(9, 50),
    )
    seed_dm(env, "629999")
    worker = AutomationWorker(env.agent, store=env.store)
    asyncio.run(worker.run_once(now=at(10, 1)))
    approval = env.agent.approvals.latest(OWNER, OWNER)
    RelayStore(env.db).decide(approval=approval, mode="full", by=OWNER)

    row = env.store.get(automation_id)
    env.store._insert_run(row, at(11), "queued", "", at(10, 5))
    asyncio.run(worker.run_once(now=at(11, 1)))

    targets = [item[0] for item in env.gateway.sent]
    assert "629999" in targets
    delivered = next(item for item in env.gateway.sent if item[0] == "629999")
    assert "automatic" in delivered[1].lower() or "(pesan otomatis)" in delivered[1]


def test_catch_up_window_missed_is_recorded_not_sent(tmp_path) -> None:
    env = build(tmp_path)
    create(env, manifest(), now=at(8, 0))
    worker = AutomationWorker(env.agent, store=env.store)

    asyncio.run(worker.run_once(now=at(10, 30)))

    found = runs(env)
    assert len(found) == 1
    assert found[0]["status"] == "missed"
    assert "catch-up" in found[0]["detail"]
    assert found[0]["apology_sent_at"] is not None
    # The only outbound message is the owner apology, never the stale task itself.
    assert [item[0] for item in env.gateway.sent] == [OWNER]


def test_count_exhausts_into_expired(tmp_path) -> None:
    env = build(tmp_path)
    create(
        env,
        manifest(event={"kind": "schedule", "at": ["10:00"], "tz": "UTC", "count": 1}),
        now=at(9, 50),
    )
    worker = AutomationWorker(env.agent, store=env.store)

    asyncio.run(worker.run_once(now=at(10, 1)))

    assert env.store.get(runs(env)[0]["automation_id"])["state"] == "expired"


def test_state_transitions_are_logged_and_terminal_states_stay_closed(tmp_path) -> None:
    env = build(tmp_path)
    automation_id = create(env, manifest(), now=at(9, 50))
    env.store.set_state(automation_id, "paused", reason="user", actor=OWNER, now=at(9, 51))
    env.store.set_state(automation_id, "active", reason="resume", actor=OWNER, now=at(9, 52))
    env.store.set_state(automation_id, "stopped", reason="user", actor=OWNER, now=at(9, 53))

    log = env.store.transitions(automation_id)
    assert [(row["from_state"], row["to_state"]) for row in log] == [
        ("draft", "active"),
        ("active", "paused"),
        ("paused", "active"),
        ("active", "stopped"),
    ]
    with pytest.raises(ManifestError, match="terminal"):
        env.store.set_state(automation_id, "active", now=at(9, 54))


def test_clean_shutdown_marks_interrupted_and_boot_resumes_it(tmp_path) -> None:
    env = build(tmp_path)
    automation_id = create(env, manifest(), now=at(9, 50))
    row = env.store.get(automation_id)
    env.store._insert_run(row, at(10), "queued", "", at(9, 55))
    claimed = env.store.claim(at(10, 1), worker="w", limit=1)
    assert claimed
    assert runs(env)[0]["status"] == "claimed"

    assert env.store.interrupt(now=at(10, 2)) == 1
    assert runs(env)[0]["status"] == "interrupted"
    assert env.store.resume_interrupted(now=at(10, 3)) == 1
    assert runs(env)[0]["status"] == "queued"


def test_describe_names_event_steps_and_policy(tmp_path) -> None:
    env = build(tmp_path)

    described = describe(validate_manifest(manifest(), env.agent.config.automations))
    assert "at 10:00" in described
    assert "compose -> send" in described
    assert "budget 4/day" in described
    assert "owner 628111" in described


def test_next_after_handles_time_lists_and_day_filters() -> None:
    listed = {"kind": "schedule", "tz": "UTC", "at": ["10:00", "12:00"]}
    later = next_after(listed, at(10, 30), anchor=at(9))
    assert datetime.fromtimestamp(later, TZ).hour == 12

    weekly = {"kind": "schedule", "tz": "UTC", "at": ["10:00"], "days": "mon,wed"}
    found = next_after(weekly, at(10, 30), anchor=at(9))
    # 2026-01-01 is a Thursday, so the next allowed day is Monday.
    assert datetime.fromtimestamp(found, TZ).weekday() == 0
    assert datetime.fromtimestamp(found, TZ).day == 5


def test_stats_counts_real_rows(tmp_path) -> None:
    env = build(tmp_path)
    create(env, manifest(), now=at(9, 50))
    worker = AutomationWorker(env.agent, store=env.store)
    asyncio.run(worker.run_once(now=at(10, 1)))

    stats = env.store.stats(at(10, 2))
    assert stats["active"] == 1
    assert stats["runs_total"] == 1
    assert stats["runs.succeeded"] == 1
    assert stats["missed"] == 0


def test_a_row_survives_a_new_store_instance(tmp_path) -> None:
    env = build(tmp_path)
    automation_id = create(env, manifest(), now=at(9, 50))
    reloaded = AutomationStore(env.db, env.agent.config.automations).get(automation_id)
    assert reloaded is not None
    assert reloaded["state"] == "active"
    assert reloaded["event"]["at"] == ["10:00"]
    assert float(reloaded["next_occurrence"]) == at(10)


def test_automation_tables_follow_the_migration(tmp_path) -> None:
    env = build(tmp_path)
    create(env, manifest(), now=at(9, 50))
    rows = env.db.fetch_all(select(automations))
    assert len(rows) == 1
    assert rows[0]["owner"] == OWNER
    assert rows[0]["owner_roles_at_creation"] == ["member"]
    assert time.time() > 0


def message_manifest(**overrides) -> dict:
    body = manifest(
        name="reaksi",
        event={"kind": "message", "tz": "UTC"},
        steps=[{"kind": "send", "to": "origin"}],
        **{},
    )
    body.update(overrides)
    return body


def test_a_message_event_queues_one_run_per_matching_message(tmp_path) -> None:
    """ADR-0014 §4.3: the intake sink writes the run, and its payload carries the message."""
    env = build(tmp_path)
    env.history.event_sink = env.store
    thread_id = "628111@s.whatsapp.net"
    create(env, message_manifest(), now=time.time())

    first = inbound_message("halo", thread=thread_id, message_id="msg-1")
    duplicate = inbound_message("halo", thread=thread_id, message_id="msg-1")
    assert env.history.intake(
        thread_id, first.payload, first.actor, gateway="fake",
    language="id",
    ) is True
    assert env.history.intake(
        thread_id, duplicate.payload, duplicate.actor, gateway="fake",
    language="id",
    ) is False

    rows = runs(env)
    assert len(rows) == 1
    assert rows[0]["status"] == "queued"
    assert rows[0]["payload"]["message_id"] == "msg-1"
    assert rows[0]["payload"]["thread"] == thread_id
    assert rows[0]["payload"]["person_id"] == first.actor


def test_a_message_event_only_matches_its_room_and_sender(tmp_path) -> None:
    env = build(tmp_path)
    env.history.event_sink = env.store
    create(
        env,
        message_manifest(
            event={"kind": "message", "tz": "UTC", "thread": ["room-a"], "sender": "628222"}
        ),
        now=time.time(),
    )

    other_room = inbound_message("halo", thread="room-b", sender="628222", person_id="628222")
    env.history.intake("room-b", other_room.payload, other_room.actor, language="id")
    other_sender = inbound_message("halo", thread="room-a", sender="628999", person_id="628999")
    env.history.intake("room-a", other_sender.payload, other_sender.actor, language="id")
    assert runs(env) == []

    matching = inbound_message("halo", thread="room-a", sender="628222", person_id="628222")
    env.history.intake("room-a", matching.payload, matching.actor, language="id")
    assert len(runs(env)) == 1


def test_a_message_run_reads_its_text_and_sender(tmp_path) -> None:
    env = build(tmp_path)
    env.history.event_sink = env.store
    thread_id = "628111@s.whatsapp.net"
    create(
        env,
        message_manifest(
            condition={"all": [{"kind": "keyword", "value": "halo"}]},
            steps=[{"kind": "compose"}, {"kind": "send", "to": "origin"}],
        ),
        now=time.time(),
    )
    message = inbound_message("halo semuanya", thread=thread_id, message_id="msg-9")
    env.history.intake(thread_id, message.payload, message.actor, gateway="fake", language="id")

    worker = AutomationWorker(env.agent, store=env.store)
    facts: dict = {}
    assert asyncio.run(worker._message_facts(runs(env)[0], facts)) is True
    assert facts["text"] == "halo semuanya"
    assert facts["sender"] == message.actor
    assert facts["thread"] == thread_id


def test_the_durable_route_survives_a_restart(tmp_path) -> None:
    """ADR-0014 §4.6: the first body owns the thread; delivery follows that route."""
    from support import FakeGateway

    other = FakeGateway(name="other")
    env = build(tmp_path)
    env.agent.gateways = (env.gateway, other)
    thread_id = "628111@s.whatsapp.net"
    message = inbound_message("halo", thread=thread_id, message_id="msg-route")
    env.history.intake(thread_id, message.payload, message.actor, gateway="other", language="id")

    assert env.history.gateway_of(thread_id) == "other"
    env.agent._turn_gateways.clear()  # a fresh boot keeps no in-memory map
    assert env.agent.address_of(thread_id).gateway == "other"
    assert env.agent.body_of(thread_id) is other


def claim_row(env, run_id: int, *, status: str, claimed_by: str, generation: int) -> None:
    with env.db.connect() as conn:
        conn.execute(
            text(
                "UPDATE automation_runs SET status = :status, claimed_by = :claimed_by, "
                "generation = :generation, lease_until = 0 WHERE id = :id"
            ),
            {
                "status": status,
                "claimed_by": claimed_by,
                "generation": generation,
                "id": int(run_id),
            },
        )


def status_of(env, run_id: int) -> str:
    found = env.db.fetch_one(
        text("SELECT status FROM automation_runs WHERE id = :id"), {"id": int(run_id)}
    )
    return str(found["status"])


def test_reap_touches_only_claimed_or_running_and_only_its_own_generation(tmp_path) -> None:
    """ADR-0012 §4.3: a reaper never releases a queued/succeeded row nor another generation's."""
    env = build(tmp_path)
    automation_id = create(env, manifest(), now=at(9, 50))
    row = env.store.get(automation_id)
    env.store._insert_run(row, at(10), "queued", "", at(9, 55))
    env.store._insert_run(row, at(10, 1), "queued", "", at(9, 55))
    env.store._insert_run(row, at(10, 2), "queued", "", at(9, 55))
    env.store._insert_run(row, at(10, 3), "queued", "", at(9, 55))
    queued, claimed, running, succeeded = [int(item["id"]) for item in runs(env)]

    claim_row(env, claimed, status="claimed", claimed_by="automation:2", generation=3)
    claim_row(env, running, status="running", claimed_by="automation:2", generation=3)
    claim_row(env, succeeded, status="succeeded", claimed_by="automation:2", generation=3)

    assert env.store.reap(at(10, 5)) == 2
    assert status_of(env, queued) == "queued"
    assert status_of(env, claimed) == "queued"
    assert status_of(env, running) == "queued"
    assert status_of(env, succeeded) == "succeeded"

    # A claim another instance took meanwhile is only released by the live generation.
    claim_row(env, claimed, status="claimed", claimed_by="automation:9", generation=9)
    head = Lease(env.db, instance_id="owner", host="host-a", pid=1)
    assert head.acquire() is not None
    assert head.acquire() is not None  # every acquisition raises the generation
    env.store.bind_lease(SimpleNamespace(instance_id="owner", generation=head.generation - 1))
    assert env.store.reap(at(10, 6)) == 0
    assert status_of(env, claimed) == "claimed"

    env.store.bind_lease(SimpleNamespace(instance_id="owner", generation=head.generation))
    assert env.store.reap(at(10, 7)) == 1
    assert status_of(env, claimed) == "queued"
