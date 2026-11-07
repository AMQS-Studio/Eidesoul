"""Task engine: `/task` and the `task` tool over the one automation scheduler (P3.4 §3)."""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from support import inbound_message, make_agent

from monoagens.engine.access.roles import Role
from monoagens.engine.commands.task import parse_spec
from monoagens.engine.config import ApprovalConfig
from monoagens.engine.jobs import automation_worker
from monoagens.engine.jobs.automation_store import AutomationStore
from monoagens.engine.jobs.automation_worker import AutomationWorker
from monoagens.engine.jobs.schedule import ManifestError
from monoagens.engine.schema import automations
from monoagens.engine.stores.jobs import JobStore, render
from monoagens.engine.tools.task import TOOL as TASK_TOOL

OWNER = "628111"
CHAT = "628111"
TZ = UTC
KINDS = ("cron", "at", "goal")


def at(hour: int, minute: int = 0, *, day: int = 1) -> float:
    return datetime(2026, 1, day, hour, minute, tzinfo=TZ).timestamp()


def build(tmp_path, *, confirm_tasks: bool = False, tools=("*",)):
    env = make_agent(
        tmp_path,
        roles={"member": Role("member", users=(OWNER,), tools=tools, commands=("*",))},
        default_role="member",
        approvals=ApprovalConfig(confirm_tasks=confirm_tasks),
    )
    env.agent.mode = "primary"
    env.jobs = JobStore(env.db, env.agent.config.automations)
    env.store = AutomationStore(env.db, env.agent.config.automations)
    seed_dm(env)
    return env


def seed_dm(env, thread: str = OWNER) -> str:
    """A send needs a routed DM thread with the owner (ADR-0004 §4.1)."""
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


def spec(kind: str, **overrides) -> dict:
    body: dict = {"owner": OWNER, "name": f"task-{kind}", "schedule_kind": kind}
    if kind == "cron":
        body["at"] = ["10:00"]
    elif kind == "at":
        body["at"] = "2026-01-02 09:00"
    else:
        body.update({"every": "1h", "condition": {"kind": "never_notified"}})
    body.update(overrides)
    return body


def create_job(env, body: dict, now: float) -> dict:
    return asyncio.run(
        env.jobs.create(env.agent, body, thread_id=CHAT, now=now)
    )


def run_command(env, args: str) -> str:
    roles = env.agent.config.roles.for_person(OWNER)
    return asyncio.run(
        env.agent._command(
            "task", args, CHAT, OWNER, roles, env.gateway, inbound_message(f"/task {args}")
        )
    )


def run_tool(env, args: dict) -> str:
    roles = env.agent.config.roles.for_person(OWNER)
    ctx = env.agent.tool_context(CHAT, OWNER, roles, env.gateway)
    fn = TASK_TOOL.fn
    assert fn is not None
    return asyncio.run(fn(args, ctx))


def stored(env) -> list[dict]:
    return env.db.fetch_all(select(automations).order_by(automations.c.id))


def test_each_schedule_kind_is_created_listed_and_stopped(tmp_path) -> None:
    env = build(tmp_path)
    now = at(8, 0)
    for kind in KINDS:
        result = create_job(env, spec(kind), now)
        assert result["state"] == "created"
        assert result["task"]["schedule_kind"] == kind
        assert result["task"]["owner"] == OWNER
        assert result["task"]["state"] == "active"
        assert result["task"]["next_occurrence"] > 0

        paused = env.jobs.pause(result["task"]["id"], actor=OWNER, now=now)
        assert paused["state"] == "paused"
        resumed = env.jobs.resume(result["task"]["id"], actor=OWNER, now=now)
        assert resumed["state"] == "active"
        stopped = env.jobs.cancel(result["task"]["id"], actor=OWNER, now=now)
        assert stopped["state"] == "stopped"

    rows = env.jobs.rows(OWNER)
    assert len(rows) == len(stored(env)) == 3
    assert {row["schedule_kind"] for row in rows} == set(KINDS)
    assert all(row["state"] == "stopped" for row in rows)
    assert [row["id"] for row in rows] == sorted((row["id"] for row in rows), reverse=True)


def test_a_goal_task_needs_a_condition(tmp_path) -> None:
    env = build(tmp_path)
    body = spec("goal")
    body.pop("condition")

    with pytest.raises(ManifestError, match="condition"):
        create_job(env, body, at(8, 0))


def test_an_at_task_must_name_a_future_moment(tmp_path) -> None:
    env = build(tmp_path)

    with pytest.raises(ManifestError, match="future"):
        create_job(env, spec("at"), at(9, 0, day=2))


def test_only_the_owner_changes_a_task(tmp_path) -> None:
    env = build(tmp_path)
    result = create_job(env, spec("cron"), at(8, 0))

    with pytest.raises(ManifestError, match="owner"):
        env.jobs.pause(result["task"]["id"], actor="629999")


def test_goal_task_notifies_only_when_the_condition_holds(tmp_path, monkeypatch) -> None:
    env = build(tmp_path)
    result = create_job(env, spec("goal"), at(9, 50))
    task_id = result["task"]["id"]
    worker = AutomationWorker(env.agent, store=env.store)
    answers = iter([False, True])
    monkeypatch.setattr(
        automation_worker, "evaluate_condition", lambda condition, facts: next(answers)
    )

    asyncio.run(worker.run_once(now=at(10, 1)))

    assert env.gateway.sent == []
    assert env.store.runs(task_id)[0]["status"] == "skipped"

    asyncio.run(worker.run_once(now=at(11, 1)))

    assert [item[0] for item in env.gateway.sent] == [OWNER]
    assert env.store.runs(task_id)[0]["status"] == "succeeded"


def test_quiet_hours_skip_and_defer_through_the_facade(tmp_path) -> None:
    env = build(tmp_path)
    skipped = create_job(env, spec("cron", at=["23:00"]), at(22, 55))
    deferred = create_job(
        env, spec("cron", name="defer-me", at=["23:00"], quiet="defer"), at(22, 55)
    )
    worker = AutomationWorker(env.agent, store=env.store)

    asyncio.run(worker.run_once(now=at(23, 1)))

    skip_run = env.store.runs(skipped["task"]["id"])[0]
    defer_run = env.store.runs(deferred["task"]["id"])[0]
    assert skip_run["status"] == "skipped"
    assert "quiet" in skip_run["detail"]
    assert defer_run["status"] == "queued"
    assert defer_run["deferred"] is True
    assert defer_run["scheduled_at"] == at(7, 0, day=2)
    assert env.gateway.sent == []

    asyncio.run(worker.run_once(now=at(7, 1, day=2)))

    assert env.store.runs(deferred["task"]["id"])[0]["status"] == "succeeded"
    assert [item[0] for item in env.gateway.sent] == [OWNER]


def test_daily_budget_blocks_the_second_run(tmp_path) -> None:
    env = build(tmp_path)
    result = create_job(env, spec("cron", budget_per_day=1), at(9, 50))
    task_id = result["task"]["id"]
    worker = AutomationWorker(env.agent, store=env.store)
    asyncio.run(worker.run_once(now=at(10, 1)))
    assert env.store.runs(task_id)[0]["status"] == "succeeded"

    # A second occurrence on the same local day arrives after the budget was spent.
    env.store._insert_run(env.store.get(task_id), at(12), "queued", "", at(10, 5))
    asyncio.run(worker.run_once(now=at(12, 1)))

    found = sorted(env.store.runs(task_id), key=lambda row: row["scheduled_at"])
    assert [row["status"] for row in found] == ["succeeded", "skipped"]
    assert "budget" in found[1]["detail"]
    assert [item[0] for item in env.gateway.sent] == [OWNER]


def test_confirm_tasks_stages_an_approval_instead_of_a_row(tmp_path) -> None:
    env = build(tmp_path, confirm_tasks=True)
    result = create_job(env, spec("cron"), at(9, 50))

    assert result["state"] == "staged"
    assert env.db.fetch_all(select(automations)) == []
    assert env.agent.approvals.count_pending() == 1
    approval = env.agent.approvals.get(result["approval_id"])
    assert approval is not None
    assert approval.kind == "automation"
    assert approval.payload["manifest"]["event"]["schedule_kind"] == "cron"
    assert "at 10:00" in approval.summary

    roles = env.agent.config.roles.for_person(OWNER)
    text = asyncio.run(
        env.agent._command(
            "approve",
            str(approval.id),
            CHAT,
            OWNER,
            roles,
            env.gateway,
            inbound_message(f"/approve {approval.id}"),
        )
    )

    assert str(approval.id) in text
    rows = env.jobs.rows(OWNER)
    assert len(rows) == 1
    assert rows[0]["state"] == "active"
    assert rows[0]["schedule_kind"] == "cron"
    assert env.db.fetch_one(select(automations))["approval_id"] == approval.id


def test_command_and_tool_return_the_same_rows(tmp_path) -> None:
    env = build(tmp_path)
    create_job(env, spec("cron"), at(9, 50))
    create_job(env, spec("goal"), at(9, 50))

    listed = run_command(env, "list")
    via_tool = run_tool(env, {"action": "list"})

    assert listed == via_tool == render(env.jobs.rows(OWNER), env.agent.soul.language)
    assert "#1" in listed
    assert "#2" in listed

    paused = run_command(env, "pause 1")
    assert paused == run_tool(env, {"action": "pause", "task_id": 1})
    assert env.jobs.get(1)["state"] == "paused"
    resumed = run_command(env, "resume 1")
    assert resumed == run_tool(env, {"action": "resume", "task_id": 1})
    assert env.jobs.get(1)["state"] == "active"
    stopped = run_command(env, "stop 1")
    assert stopped == run_tool(env, {"action": "cancel", "task_id": 1})
    assert env.jobs.get(1)["state"] == "stopped"


def test_command_and_tool_create_tasks_through_the_same_facade(tmp_path) -> None:
    env = build(tmp_path)

    created = run_command(env, "new rekap at 10:00")
    assert "#1" in created
    goal = run_tool(
        env,
        {
            "action": "new",
            "name": "cek inbox",
            "schedule_kind": "goal",
            "every": "1h",
            "condition": "inbox kosong",
        },
    )
    assert "#2" in goal

    rows = env.jobs.rows(OWNER)
    assert [row["schedule_kind"] for row in rows] == ["goal", "cron"]
    assert rows[1]["name"] == "rekap"
    assert rows[1]["event"]["at"] == ["10:00"]
    assert rows[0]["condition"]["kind"] == "nl"


def test_parse_spec_reads_flags_around_a_multi_word_name() -> None:
    parsed = parse_spec(
        "rekap pagi at 10:00,16:00 days mon,wed budget 2 quiet 22:00-07:00 defer "
        "tz Asia/Jakarta"
    )

    assert parsed["name"] == "rekap pagi"
    assert parsed["at"] == "10:00,16:00"
    assert parsed["days"] == "mon,wed"
    assert parsed["budget_per_day"] == 2
    assert parsed["quiet_hours"] == "22:00-07:00"
    assert parsed["quiet"] == "defer"
    assert parsed["timezone"] == "Asia/Jakarta"

    once = parse_spec("tidur on 2026-12-01 21:30")
    assert once["name"] == "tidur"
    assert once["schedule_kind"] == "at"
    assert once["at"] == "2026-12-01 21:30"


def test_one_task_is_one_manifest_and_the_scheduler_stays_the_automation_worker(
    tmp_path,
) -> None:
    env = build(tmp_path)
    create_job(env, spec("cron"), at(9, 50))
    create_job(env, spec("goal"), at(9, 50))

    rows = stored(env)
    assert len(rows) == 2
    assert [row["event"]["schedule_kind"] for row in rows] == ["cron", "goal"]
    assert [step["kind"] for step in rows[0]["steps"]] == ["compose", "send"]


def test_a_recipe_step_is_stored_and_its_step_index_is_logged(tmp_path) -> None:
    env = build(tmp_path)
    public = env.config.workspace / "public" / "catatan.txt"
    (env.config.workspace / "public").mkdir(parents=True, exist_ok=True)
    public.write_text("halo dari resep", encoding="utf-8")
    env.recipes.save(
        "baca",
        "recipe",
        "Baca catatan.",
        steps=[{"tool": "read", "args": {"path": "public/catatan.txt"}}],
    )
    result = create_job(
        env,
        spec("cron", steps=[{"kind": "recipe", "name": "baca"}]),
        at(9, 50),
    )
    task_id = result["task"]["id"]
    worker = AutomationWorker(env.agent, store=env.store)

    asyncio.run(worker.run_once(now=at(10, 1)))

    found = env.store.runs(task_id)[0]
    assert found["status"] == "succeeded"
    assert found["step_index"] == 0
    assert env.jobs.get(task_id)["steps"] == [{"kind": "recipe", "name": "baca", "args": ""}]
