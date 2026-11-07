"""ADR-0001 episode contract: one closure, one distill job, one revision, scope stays put."""


import asyncio
import time

from support import build_config, database

from monoagens.engine.jobs.cognition import CognitionWorker
from monoagens.engine.memory import episodes
from monoagens.engine.memory.store import MemoryStore
from monoagens.engine.schema import message_media, messages, turn_steps, turns
from monoagens.engine.stores.skills import SkillStore

THREAD = "628111@s.whatsapp.net"
PERSON = "628111"
OTHER = "222@g.us"


def seed_turn(
    db, turn_id: str, *, thread: str = THREAD, created: float, tools=(), app: str = ""
) -> None:
    with db.connect() as conn:
        db.upsert(
            conn,
            turns,
            [
                {
                    "id": turn_id,
                    "thread_id": thread,
                    "person_id": PERSON,
                    "status": "delivered",
                    "step": "delivered",
                    "app": app,
                    "attempt": 1,
                    "lease_owner": "",
                    "lease_until": 0.0,
                    "generation": 0,
                    "planned_chunks": 1,
                    "delivered_chunks": 1,
                    "shutdown_epoch": 0,
                    "boot_epoch": 0,
                    "last_error": "",
                    "created_at": created,
                    "updated_at": created,
                }
            ],
            ["id"],
        )
        for index, (tool, status) in enumerate(tools):
            db.upsert(
                conn,
                turn_steps,
                [
                    {
                        "turn_id": turn_id,
                        "step_index": index,
                        "step": "drafted",
                        "tool": tool,
                        "args_hash": "0" * 64,
                        "result": "",
                        "status": status,
                        "started_at": created,
                        "finished_at": created,
                    }
                ],
                ["turn_id", "step_index"],
            )


def seed_artifact(db, msg_id: str, *, thread: str = THREAD, ts: float) -> None:
    with db.connect() as conn:
        db.upsert(
            conn,
            messages,
            [
                {
                    "thread_id": thread,
                    "msg_id": msg_id,
                    "ts": ts,
                    "sender_id": "",
                    "sender_name": "monoagens",
                    "sender_address": "",
                    "text": "hasil",
                    "outgoing": True,
                    "author": "bot",
                    "visibility": 2,
                    "deleted": False,
                    "deleted_by": "",
                    "deleted_admin": False,
                    "data": {},
                }
            ],
            ["thread_id", "msg_id"],
        )
        db.upsert(
            conn,
            message_media,
            [
                {
                    "thread_id": thread,
                    "message_id": msg_id,
                    "seq": 0,
                    "kind": "media",
                    "mime": "image/jpeg",
                    "bytes_ref": b"x",
                }
            ],
            ["thread_id", "message_id", "seq"],
        )


def worker(tmp_path, db) -> CognitionWorker:
    config = build_config(tmp_path)
    return CognitionWorker(
        db,
        config,
        MemoryStore(db, config),
        skills=SkillStore(db, config.skills),
    )


def test_episode_closes_once_and_distills_one_revision(tmp_path) -> None:
    db = database()
    created = time.time() - 3600
    seed_turn(db, "t1", created=created, tools=[("read", "executed")])
    engine = worker(tmp_path, db)

    closed = episodes.scan(db, now=created + 11 * 60, idle_minutes=10)
    assert closed == ["t1"]
    assert episodes.scan(db, now=created + 20 * 60, idle_minutes=10) == []
    episode = episodes.get(db, "t1")
    assert episode.status == "closed"
    assert episode.close_signal == "idle"
    assert episode.tool_calls == 1

    asyncio.run(engine._enqueue_distill("t1", time.time()))
    results = asyncio.run(engine.drain(kind="distill"))
    assert results
    assert engine.skills.names() == ["read"]
    assert len(engine.skills.revisions("read")) == 1
    assert episodes.get(db, "t1").distilled_at > 0

    asyncio.run(engine._enqueue_distill("t1", time.time()))
    assert asyncio.run(engine.drain(kind="distill")) == {}
    assert len(engine.skills.revisions("read")) == 1


def test_episode_without_tool_work_produces_no_job(tmp_path) -> None:
    db = database()
    created = time.time() - 3600
    seed_turn(db, "t2", created=created)
    engine = worker(tmp_path, db)

    assert episodes.scan(db, now=created + 11 * 60, idle_minutes=10) == ["t2"]
    asyncio.run(engine._enqueue_distill("t2", time.time()))

    assert engine.queue.claim(worker="t", limit=1) == []
    assert engine.skills.names() == []


def test_artifact_signal_closes_after_the_short_pause(tmp_path) -> None:
    db = database()
    created = time.time() - 600
    seed_turn(db, "t3", created=created, tools=[("write", "executed")])
    seed_artifact(db, "out-1", ts=created + 30)

    closed = episodes.scan(
        db, now=created + 30 + 3 * 60 + 1, idle_minutes=10, idle_after_send_minutes=3
    )

    assert closed == ["t3"]
    assert episodes.get(db, "t3").close_signal == "artifact"


def test_shutdown_closes_open_episodes(tmp_path) -> None:
    db = database()
    seed_turn(db, "t4", created=time.time(), tools=[("read", "executed")])
    assert episodes.scan(db, now=time.time()) == []
    assert episodes.get(db, "t4").status == "open"

    assert episodes.close_all(db, signal="shutdown") == ["t4"]
    assert episodes.get(db, "t4").close_signal == "shutdown"


def test_procedure_scope_does_not_leak_to_another_thread(tmp_path) -> None:
    db = database()
    config = build_config(tmp_path)
    skills = SkillStore(db, config.skills)
    skills.save(
        "deploy",
        "Deploy service to production.",
        "# Steps\n1. read the file.\n",
        scope="thread",
        stated_by=THREAD,
    language="id",
    )

    assert skills.retrieve("deploy production", thread_id=THREAD, catalog_tools=("read",))
    assert (
        skills.retrieve("deploy production", thread_id=OTHER, catalog_tools=("read",)) == ""
    )


def test_the_signature_names_scope_tools_and_target_types() -> None:
    """ADR-0001 §4.6: two runs compare only when the scope, tools, and target types match."""
    steps = [
        {"tool": "read", "args_hash": "a", "target": "file"},
        {"tool": "read", "args_hash": "b", "target": "file"},
        {"tool": "send", "args_hash": "c", "target": "room"},
    ]

    signature = episodes._signature(steps, "t")

    assert signature == "scope=thread;tools=read,send;targets=file,room"
    assert episodes._signature(steps, "t", "a") == (
        "scope=thread;app=a;tools=read,send;targets=file,room"
    )
