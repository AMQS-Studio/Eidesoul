"""Lease contract: CAS, fencing generation, database clock, freeze, and takeover."""


import asyncio
import inspect
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, text
from support import database, make_agent

from monoagens.engine.agent import Agent
from monoagens.engine.jobs import lease as lease_module
from monoagens.engine.jobs.lease import Lease, Superseded
from monoagens.engine.jobs.supervisor import Supervisor
from monoagens.engine.schema import actions


def lease(db, name: str, host: str = "host-a", pid: int = 1) -> Lease:
    return Lease(db, instance_id=name, host=host, pid=pid)


def test_acquire_renew_release(tmp_path) -> None:
    db = database()
    first = lease(db, "a")
    holder = first.acquire()
    assert holder is not None
    assert holder.instance_id == "a"
    assert holder.generation == 1
    assert first.acquire() is not None
    assert first.generation == 2  # every acquisition raises the fencing token
    assert first.renew() is True
    assert first.holder().pid == 1
    first.release()
    assert first.holder() is None
    assert first.renew() is False


def test_second_instance_stands_by_and_takeover(tmp_path) -> None:
    db = database()
    first = lease(db, "a")
    second = lease(db, "b", host="host-b", pid=2)
    assert first.acquire() is not None
    assert second.acquire() is None
    assert second.holder().instance_id == "a"

    taken = second.acquire(takeover=True)
    assert taken is not None
    assert taken.instance_id == "b"
    assert taken.generation == 2
    assert first.renew() is False
    first.release()  # only the generation owner may release: this is a no-op
    assert second.holder().instance_id == "b"


def test_expired_heartbeat_allows_acquire(tmp_path) -> None:
    db = database()
    first = lease(db, "a")
    first.acquire()
    with db.connect() as conn:
        conn.execute(
            text(
                "UPDATE engine_lease "
                "SET heartbeat_at = EXTRACT(EPOCH FROM clock_timestamp()) - :age"
            ),
            {"age": first.ttl + 1},
        )
    second = lease(db, "b")
    assert second.acquire() is not None


def test_expiry_ignores_the_application_clock(tmp_path) -> None:
    db = database()
    first = lease(db, "a")
    first.acquire()
    with db.connect() as conn:
        # A host whose clock runs ahead wrote this heartbeat: the database still says alive.
        conn.execute(
            text(
                "UPDATE engine_lease "
                "SET heartbeat_at = EXTRACT(EPOCH FROM clock_timestamp()) + 3600"
            )
        )
    assert lease(db, "b").acquire() is None
    source = Path(inspect.getsourcefile(lease_module)).read_text(encoding="utf-8")
    assert "time.time(" not in source


def test_concurrent_acquire_has_exactly_one_winner(tmp_path) -> None:
    db = database()
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def race(name: str) -> None:
        contender = lease(db, name, host=f"host-{name}", pid=0)
        barrier.wait()
        results[name] = contender.acquire()

    threads = [threading.Thread(target=race, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [name for name, holder in results.items() if holder is not None]
    assert len(winners) == 1
    holder = lease(db, "observer").holder()
    assert holder is not None
    assert holder.instance_id == winners[0]
    assert holder.generation == 1


def test_two_standbys_on_an_expired_row_write_one_generation(tmp_path) -> None:
    db = database()
    first = lease(db, "a")
    assert first.acquire() is not None
    with db.connect() as conn:
        conn.execute(
            text(
                "UPDATE engine_lease "
                "SET heartbeat_at = EXTRACT(EPOCH FROM clock_timestamp()) - :age"
            ),
            {"age": first.ttl + 1},
        )

    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def race(name: str) -> None:
        contender = lease(db, name, host=f"host-{name}", pid=0)
        barrier.wait()
        results[name] = contender.acquire()

    threads = [threading.Thread(target=race, args=(name,)) for name in ("b", "c")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [name for name, holder in results.items() if holder is not None]
    assert len(winners) == 1
    holder = lease(db, "observer").holder()
    assert holder is not None
    assert holder.instance_id == winners[0]
    assert holder.generation == 2


def test_a_db_error_is_a_lease_failure_not_a_crash(tmp_path, monkeypatch) -> None:
    db = database()
    item = lease(db, "a")
    assert item.acquire() is not None

    def boom():
        raise ConnectionError("database is gone")

    monkeypatch.setattr(db, "connect", boom)
    monkeypatch.setattr(db, "control", boom)
    assert item.renew() is False
    assert item.holder() is None
    assert item.frozen() is False
    assert item.acquire() is None


def test_stale_generation_is_rejected_and_recorded(tmp_path) -> None:
    db = database()
    first = lease(db, "a")
    assert first.acquire() is not None
    second = lease(db, "b", host="host-b", pid=2)
    assert second.acquire(takeover=True) is not None

    assert second.guard.require("send") == second.generation
    with pytest.raises(Superseded):
        first.guard.require("send")
    records = db.fetch_all(select(actions).where(actions.c.kind == "lease"))
    summaries = " ".join(str(row["summary"]) for row in records)
    assert "takeover from" in summaries
    assert "superseded send" in summaries


def test_freeze_blocks_acquire_until_cleared(tmp_path) -> None:
    db = database()
    first = lease(db, "a")
    second = lease(db, "b")
    first.acquire()
    first.release()
    first.request_freeze("a")
    assert first.frozen() is True
    assert second.acquire() is None
    assert second.acquire(takeover=True) is not None
    assert second.frozen() is False


def test_ensure_does_not_clear_freeze(tmp_path) -> None:
    db = database()
    lease(db, "a").request_freeze("a")
    db.ensure()
    db.ensure()
    assert lease(db, "b").frozen() is True


def test_a_standby_agent_construction_writes_no_rows(tmp_path) -> None:
    """ADR-0008 §4.3: building a standby Agent is pure; only the primary start path writes."""
    env = make_agent(tmp_path)
    statements: list[str] = []

    def record(engine, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    event.listen(env.db.engine, "before_cursor_execute", record)
    try:
        standby = Agent(
            env.config,
            env.db,
            llm=env.llm,
            hub=None,
            windows=env.windows,
            history=env.history,
            memory=env.memory,
            ledger=env.ledger,
            gateways=(env.gateway,),
        )
    finally:
        event.remove(env.db.engine, "before_cursor_execute", record)

    writes = [
        statement
        for statement in statements
        if not statement.lstrip().lower().startswith("select")
        and "pg_catalog" not in statement
        and "information_schema" not in statement
    ]
    assert standby.mode == "standby"
    assert writes == []


def test_primary_activation_syncs_soul_rules_once_before_recovery(tmp_path) -> None:
    """ADR-0008 §4.3: the soul rules are mirrored on activation, never on construction."""
    env = make_agent(tmp_path)
    order: list[str] = []
    env.agent._sync_soul_rules = lambda: order.append("sync") or "ok"

    async def recover() -> int:
        order.append("recover")
        return 0

    env.agent.recover = recover
    supervisor = Supervisor.__new__(Supervisor)
    supervisor.agent = env.agent
    supervisor.gateways = []
    supervisor.automations = None
    supervisor.cognition = SimpleNamespace(start=lambda: None, status=lambda: "off")
    supervisor._set_shutting_down = lambda value: None
    holder = SimpleNamespace(host="host-a", pid=1, generation=1, started_at=0.0)

    asyncio.run(supervisor.activate(holder))

    assert order == ["sync", "recover"]
    assert env.agent.mode == "primary"


# ------------------------------------------------------- atomic SQL fencing

def bump_generation(db) -> int:
    """A takeover reduces to this one row write; every store must see it in its own SQL."""
    with db.connect() as conn:
        live = conn.execute(
            text("UPDATE engine_lease SET generation = generation + 1 RETURNING generation")
        ).scalar()
    return int(live)


def held(instance_id: str, generation: int) -> SimpleNamespace:
    """A store binding without a guard: the in-process check is deliberately bypassed."""
    return SimpleNamespace(instance_id=instance_id, generation=generation)


def seed_turn(db) -> None:
    with db.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO turns (id, thread_id, person_id, status, step, created_at, "
                "updated_at) VALUES ('turn-fence', 'thread-fence', 'person-fence', "
                "'received', 'received', 0, 0)"
            )
        )


def seed_run(db) -> int:
    with db.connect() as conn:
        automation_id = conn.execute(
            text(
                "INSERT INTO automations (owner, name, event, condition, steps, policy, state, "
                "reason, owner_roles_at_creation, approval_id, next_occurrence, fire_count, "
                "ends_at, created_at, updated_at) VALUES ('owner', 'name', '{}'::jsonb, "
                "'{}'::jsonb, '[]'::jsonb, '{}'::jsonb, 'active', '', '[]'::jsonb, 0, 0, 0, 0, "
                "0, 0) RETURNING id"
            )
        ).scalar()
        run_id = conn.execute(
            text(
                "INSERT INTO automation_runs (automation_id, occurrence_key, scheduled_at, "
                "status, created_at, updated_at) VALUES (:automation, 'fence:occurrence', 0, "
                "'queued', 0, 0) RETURNING id"
            ),
            {"automation": int(automation_id)},
        ).scalar()
    return int(run_id)


def test_a_deposed_instance_is_rejected_by_sql_on_canonical_writes(tmp_path) -> None:
    """ADR-0002 §4.3: the predicate rides the write, so a stale guard cannot let it pass."""
    from support import build_config

    from monoagens.engine.jobs.automation_store import AutomationStore
    from monoagens.engine.memory.store import MemoryStore
    from monoagens.engine.stores.turns import TurnStore

    db = database()
    config = build_config(tmp_path)
    first = lease(db, "a")
    assert first.acquire() is not None
    memory = MemoryStore(db, config)
    memory.bind_lease(first)
    turns = TurnStore(db)
    turns.bind_lease(first)
    automations = AutomationStore(db, config.automations)
    automations.bind_lease(first)
    seed_turn(db)
    run_id = seed_run(db)
    assert memory.write("before the takeover", scope="global", level=2)

    deposed = first.generation
    live = bump_generation(db)
    assert live != deposed

    # The deposed store no longer reaches a canonical row, even with no guard bound.
    memory.bind_lease(held("a", deposed))
    turns.bind_lease(held("a", deposed))
    automations.bind_lease(held("a", deposed))
    with pytest.raises(Superseded):
        memory.write("after the takeover", scope="global", level=2)
    with pytest.raises(Superseded):
        turns.checkpoint("turn-fence", "decided")
    assert turns.claim(
        "turn-fence", owner="a", generation=deposed, lease_seconds=60
    ) is None
    assert automations.claim(0.0, worker="automation:a", generation=deposed) == []
    automations.finish(run_id, "succeeded")
    stored = db.fetch_one(
        text("SELECT status FROM automation_runs WHERE id = :id"), {"id": run_id}
    )
    assert stored["status"] == "queued"

    # The live generation writes everything the deposed one was refused.
    memory.bind_lease(held("a", live))
    turns.bind_lease(held("a", live))
    automations.bind_lease(held("a", live))
    assert memory.write("the live generation", scope="global", level=2)
    claimed = turns.claim("turn-fence", owner="a", generation=live, lease_seconds=60)
    assert claimed is not None
    assert claimed["generation"] == live
    assert turns.checkpoint("turn-fence", "decided")["step"] == "decided"
    runs = automations.claim(0.0, worker="automation:a", generation=live)
    assert len(runs) == 1
    assert runs[0]["generation"] == live
    automations.finish(run_id, "succeeded")
    stored = db.fetch_one(
        text("SELECT status FROM automation_runs WHERE id = :id"), {"id": run_id}
    )
    assert stored["status"] == "succeeded"


def test_a_side_effecting_tool_is_refused_when_the_generation_moved(tmp_path) -> None:
    """ADR-0002 §4.3: the fence is asked immediately before the tool touches the world."""
    from support import build_config

    from monoagens.engine.access.roles import Role
    from monoagens.engine.registry import Handler
    from monoagens.engine.tools import ToolContext, execute

    db = database()
    config = build_config(tmp_path)
    first = lease(db, "a")
    assert first.acquire() is not None
    calls: list[str] = []

    async def run(args, ctx):
        calls.append("ran")
        return "ok"

    spec = Handler(
        name="note", description="", fn=run, target="file", approval_class="consent"
    )
    role = Role("member", tools=("*",))
    ctx = ToolContext(
        config, (role,), ("*",), approved=True, agent=SimpleNamespace(lease=first)
    )
    assert asyncio.run(execute({"note": spec}, "note", {}, ctx)) == "ok"
    assert calls == ["ran"]

    bump_generation(db)
    answer = asyncio.run(execute({"note": spec}, "note", {}, ctx))
    assert answer.startswith("error: refused by the live lease")
    assert calls == ["ran"]


def test_the_primary_worker_binds_every_store_to_the_lease(tmp_path) -> None:
    """ADR-0002 §4.3: one wiring point, so no store is left writing without a generation."""
    from support import build_config

    from monoagens.engine.jobs.automation_worker import AutomationWorker
    from monoagens.engine.memory.store import MemoryStore
    from monoagens.engine.stores.skills import SkillStore
    from monoagens.engine.stores.turns import TurnStore

    db = database()
    config = build_config(tmp_path)
    first = lease(db, "a")
    assert first.acquire() is not None
    agent = SimpleNamespace(
        config=config,
        db=db,
        lease=first,
        memory=MemoryStore(db, config),
        turns=TurnStore(db),
        skills=SkillStore(db, config.skills),
        affect=None,
        approvals=None,
    )

    AutomationWorker(agent)

    assert agent.memory.fence is not None
    assert agent.turns.fence is not None
    assert agent.skills.fence is not None
    seed_turn(db)
    bump_generation(db)
    with pytest.raises(Superseded):
        agent.turns.checkpoint("turn-fence", "decided")
