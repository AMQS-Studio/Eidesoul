"""Part V of the remediation runbook: schema, migrations, skills, cognition, ambient.

One focused regression per finding (V.1-V.10). The migration tests run the real Alembic
chain on a throwaway database; everything else reuses the session-migrated store.
"""

import asyncio
import re
import threading
import time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text as sql
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex
from support import FakeLLM, build_config, database
from test_migrations import fresh_database as _migration_db  # noqa: F401  (pytest fixture)

from monoagens.engine.db import MIGRATIONS, SCHEMA_VERSION, Database
from monoagens.engine.jobs.ambient import Ambient
from monoagens.engine.jobs.cognition import CognitionQueue, CognitionWorker
from monoagens.engine.jobs.lease import Lease, Superseded
from monoagens.engine.memory.store import MemoryStore
from monoagens.engine.schema.automation import automations
from monoagens.engine.schema.memory import memories_readable
from monoagens.engine.soul import AffectConfig
from monoagens.engine.stores.affect import AffectStore
from monoagens.engine.stores.history import HistoryStore
from monoagens.engine.stores.skills import SkillStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(name="fresh_database")
def _fresh_database(request: pytest.FixtureRequest) -> str:
    """Reuse the migration suite's throwaway database fixture."""
    return request.getfixturevalue("_migration_db")


def _downgrade_source(name: str) -> str:
    source = (ROOT / "migrations" / "versions" / name).read_text()
    return source.split("def downgrade()", 1)[1]


def _alembic(url: str) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


# ------------------------------------------------------------------ migrations


def test_downgrade_to_0010_restores_the_google_identity_foreign_key(fresh_database) -> None:
    """V.1: 0015's downgrade must recreate the FK that 0011's downgrade then drops."""

    db = Database(fresh_database)
    try:
        assert db.upgrade() == SCHEMA_VERSION
        cfg = _alembic(db.url)
        # 0015's downgrade recreates the table, so the FK must be back before 0011 runs.
        with db.engine.connect() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "0014")
            conn.commit()
            names = {
                str(fk["name"])
                for fk in sa_inspect(conn).get_foreign_keys("google_identity")
            }
            assert "fk_google_identity_person" in names
        # 0011's downgrade drops that FK by name; it crashes if 0015 did not restore it.
        with db.engine.connect() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "0010")
            conn.commit()
        assert "google_identity" in sa_inspect(db.engine).get_table_names()
    finally:
        db.dispose()


def test_0022_downgrade_sql_settles_running_and_interrupted_steps() -> None:
    """V.2: the narrower check cannot be recreated while running/interrupted rows exist."""

    downgrade = _downgrade_source("0022_turn_identity_attachments.py")
    found = re.search(r'op\.execute\(\s*"(UPDATE turn_steps[^"]*)"', downgrade)
    assert found is not None, "0022 downgrade lost its turn_steps settlement"
    assert downgrade.find("UPDATE turn_steps") < downgrade.find(
        'op.drop_constraint("ck_turn_steps_status"'
    )

    db = database()
    with db.connect() as conn:
        for index, status in enumerate(("running", "interrupted", "executed", "failed")):
            conn.execute(
                sql(
                    "INSERT INTO turn_steps (turn_id, step_index, tool, args_hash, status) "
                    "VALUES ('t1', :index, 'shell', 'hash', :status)"
                ),
                {"index": index, "status": status},
            )
        conn.execute(sql(found.group(1)))

    rows = db.fetch_all(sql("SELECT step_index, status FROM turn_steps ORDER BY step_index"))
    assert [row["status"] for row in rows] == ["failed", "failed", "executed", "failed"]


# -------------------------------------------------------------------- schema


def test_memories_readable_mirrors_the_view_columns() -> None:
    """V.3: the selectable must carry every column the `SELECT *` view exposes."""

    assert {"embedding", "search_tsv"} <= set(memories_readable.c.keys())
    with database().engine.connect() as conn:
        keys = set(conn.execute(sql("SELECT * FROM memories_readable LIMIT 0")).keys())
    assert {"embedding", "search_tsv"} <= keys


def test_the_message_index_is_bound_to_automations() -> None:
    """V.4: an unbound Index never reaches create_all()."""

    index = next(item for item in automations.indexes if item.name == "ix_automations_message")
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "ON automations" in ddl
    assert "event ->> 'kind'" in ddl
    assert "WHERE state = 'active'" in ddl


# -------------------------------------------------------------------- skills


def test_evidence_skills_keep_their_app_namespace() -> None:
    """V.5: evidence rows must never be flattened to a bare logical name."""

    db = database()
    store = SkillStore(db)
    with db.connect() as conn:
        conn.execute(
            sql(
                "INSERT INTO episodes (id, signature, started_at) "
                "VALUES ('ep-1', 'sig-1', :now)"
            ),
            {"now": time.time()},
        )
    for app in ("a", "b"):
        store.save(
            "deploy",
            f"Deploy corpus {app}.",
            f"# {app}\n",
            app=app,
            source_episode="distill:ep-1",
            language="id",
        )

    evidence = store.evidence_skills("sig-1")
    assert {item.name for item in evidence} == {"deploy"}
    assert {item.app for item in evidence} == {"a", "b"}
    assert store.evidence_for_signature("sig-1") == ["deploy", "deploy"]


def test_evidence_rows_match_a_bare_episode_id() -> None:
    """ADR-0009 §4.5: distillation stores the bare episode id as its source."""

    db = database()
    store = SkillStore(db)
    with db.connect() as conn:
        conn.execute(
            sql(
                "INSERT INTO episodes (id, signature, started_at) "
                "VALUES ('ep-2', 'sig-2', :now)"
            ),
            {"now": time.time()},
        )
    store.save(
        "deploy",
        "Deploy the service.",
        "# Deploy\n",
        source_episode="ep-2",
        language="id",
    )

    assert store.evidence_for_signature("sig-2") == ["deploy"]


def test_fenced_skill_mutations_raise_superseded_when_the_row_exists() -> None:
    """V.7: a refused fenced mutation is a lost lease, not a silent no-op or KeyError."""

    db = database()
    store = SkillStore(db)
    store.save("deploy", "Deploy.", "# D\n", app="a", language="id")
    store.save("old", "Old.", "# O\n", app="a", language="id")
    store.archive("old", app="a")

    old = Lease(db, instance_id="old", host="host-a", pid=1)
    assert old.acquire() is not None
    store.bind_lease(old)
    new = Lease(db, instance_id="new", host="host-b", pid=2)
    assert new.acquire(takeover=True) is not None

    for mutate in (
        lambda: store.set_priority("deploy", 5, app="a"),
        lambda: store.archive("deploy", app="a"),
        lambda: store.pin("deploy", app="a"),
        lambda: store.restore_archived("old", app="a"),
    ):
        with pytest.raises(Superseded):
            mutate()


def test_a_racing_save_without_overwrite_raises_file_exists() -> None:
    """V.8: a competing creator must lose loudly instead of overwriting the winner."""

    db = database()
    store = SkillStore(db)
    inserted = threading.Event()

    def creator() -> None:
        with db.engine.connect() as conn:
            conn.execute(
                sql(
                    "INSERT INTO skills (name, description, body, archived, "
                    "created_at, updated_at) "
                    "VALUES ('deploy', 'first', '# 1', false, 0, 0)"
                )
            )
            inserted.set()
            time.sleep(0.5)  # hold the row uncommitted so the save's pre-check misses it
            conn.commit()

    thread = threading.Thread(target=creator, name="skill-creator")
    thread.start()
    try:
        assert inserted.wait(2.0)
        with pytest.raises(FileExistsError):
            store.save("deploy", "second", "# 2", language="id")
    finally:
        thread.join(5.0)
    row = db.fetch_one(sql("SELECT description FROM skills WHERE name = 'deploy'"))
    assert row["description"] == "first"


# ----------------------------------------------------------------- cognition


def test_jobs_done_only_counts_a_completion_the_queue_accepts(tmp_path) -> None:
    """V.9: a refused complete is not a done job; one failing job must not abort the drain."""

    db = database()
    config = build_config(tmp_path)
    queue = CognitionQueue(db, config)
    assert queue.enqueue_job("audit", "global", key="audit:a") is True
    assert queue.enqueue_job("audit", "global", key="audit:b") is True

    old = Lease(db, instance_id="old", host="host-a", pid=1)
    assert old.acquire() is not None
    jobs = queue.claim(worker="a", limit=2, generation=old.generation)
    assert len(jobs) == 2
    new = Lease(db, instance_id="new", host="host-b", pid=2)
    assert new.acquire(takeover=True) is not None

    worker = CognitionWorker(
        db, config, MemoryStore(db, config), llm=None, lease=new
    )
    good, boom = jobs[0], jobs[1]

    async def handle(job):
        if job.id == boom.id:
            raise RuntimeError("boom")
        return "ok"

    worker._handle = handle  # type: ignore[method-assign]
    results = asyncio.run(worker._drain_jobs(jobs))

    assert results == {good.id: "ok"}
    assert worker.counters["jobs_done"] == 0
    assert worker.counters["jobs_failed"] == 1
    row = db.fetch_one(
        sql("SELECT status FROM cognition_jobs WHERE id = :id"), {"id": good.id}
    )
    assert row["status"] == "claimed"


# ------------------------------------------------------------------- ambient


def _ambient(tmp_path) -> Ambient:
    db = database()
    config = build_config(tmp_path, affect=AffectConfig(enabled=True))
    affect = AffectStore(db, config.soul.voice.affect, soul=config.soul.persona)
    history = HistoryStore(db, config.workspace)
    return Ambient(config, FakeLLM(), affect, None, None, history, None)


def test_a_superseded_research_stops_the_tick_without_crashing(tmp_path) -> None:
    """V.10: a lost lease ends the tick cleanly; no further writes are attempted."""

    ambient = _ambient(tmp_path)
    ambient.known_threads.update({"a", "b"})
    calls: list[str] = []

    async def candidate(thread_id, moment):
        return True

    async def research(thread_id):
        calls.append(thread_id)
        raise Superseded("lease generation moved on")

    ambient._research_candidate = candidate  # type: ignore[method-assign]
    ambient._research = research  # type: ignore[method-assign]
    asyncio.run(ambient._tick(now=time.time()))
    assert calls == ["a"]


def test_one_chat_failing_research_does_not_stop_the_others(tmp_path) -> None:
    """V.10: an ordinary failure is logged and the next thread still gets its turn."""

    ambient = _ambient(tmp_path)
    ambient.known_threads.update({"a", "b"})
    calls: list[str] = []

    async def candidate(thread_id, moment):
        return True

    async def research(thread_id):
        calls.append(thread_id)
        if thread_id == "a":
            raise RuntimeError("boom")

    ambient._research_candidate = candidate  # type: ignore[method-assign]
    ambient._research = research  # type: ignore[method-assign]
    asyncio.run(ambient._tick(now=time.time()))
    assert calls == ["a", "b"]


def test_portable_json_dump_restores_what_it_dumped(tmp_path) -> None:
    """3.2: the no-pg_dump fallback is a real, reversible snapshot of every table."""
    from monoagens.admin.database import dump_portable, restore_portable
    from monoagens.engine.schema import state

    db = database()
    try:
        with db.connect() as conn:
            conn.execute(state.delete())
            conn.execute(
                state.insert().values(key="round-trip", value="kept", updated_at=time.time())
            )
        target = tmp_path / "portable.json"
        dump_portable(db.url, target)
        with db.connect() as conn:
            conn.execute(state.delete())
        restore_portable(db.url, target)
        with db.connect() as conn:
            value = conn.execute(
                sql("SELECT value FROM state WHERE key = 'round-trip'")
            ).scalar_one()
        assert value == "kept"
    finally:
        db.dispose()
