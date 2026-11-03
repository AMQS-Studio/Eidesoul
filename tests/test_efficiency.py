"""ADR-0020 §4.6 efficiency ladder: evidence, eligibility, actions, and the proposal cooldown."""


import asyncio
import json
import time

from support import build_config, database

from eidesoul.engine.cognition import CognitionWorker
from eidesoul.engine.memory import efficiency
from eidesoul.engine.memory.ledger import ActionLedger
from eidesoul.engine.memory.store import MemoryStore
from eidesoul.engine.schema import (
    approvals,
    automation_runs,
    automations,
    llm_calls,
    people,
    thread_people,
    threads,
    turn_steps,
    turns,
)
from eidesoul.engine.schema import (
    episodes as episodes_table,
)
from eidesoul.engine.stores.skills import SkillStore

THREAD = "628111@s.whatsapp.net"
PERSON = "628111"
SIGNATURE = "scope=thread;tools=read,write;targets=file"


def seed_episode(
    db, episode_id: str, *, signature: str = SIGNATURE, closed: float, tokens: int = 100
):
    with db.connect() as conn:
        db.upsert(
            conn,
            turns,
            [
                {
                    "id": episode_id,
                    "thread_id": THREAD,
                    "person_id": PERSON,
                    "status": "delivered",
                    "step": "delivered",
                    "attempt": 1,
                    "lease_owner": "",
                    "lease_until": 0.0,
                    "generation": 0,
                    "planned_chunks": 1,
                    "delivered_chunks": 1,
                    "shutdown_epoch": 0,
                    "boot_epoch": 0,
                    "last_error": "",
                    "created_at": closed - 60,
                    "updated_at": closed - 60,
                }
            ],
            ["id"],
        )
        db.upsert(
            conn,
            episodes_table,
            episode_rows(episode_id, signature=signature, closed=closed),
            ["id"],
        )
        for index, (tool, status) in enumerate((("read", "executed"), ("write", "executed"))):
            db.upsert(
                conn,
                turn_steps,
                [
                    {
                        "turn_id": episode_id,
                        "step_index": index,
                        "step": "drafted",
                        "tool": tool,
                        "args_hash": f"{index}" * 64,
                        "result": "",
                        "status": status,
                        "started_at": closed - 60,
                        "finished_at": closed - 60,
                    }
                ],
                ["turn_id", "step_index"],
            )
        conn.execute(
            llm_calls.insert().values(
                ts=closed - 60,
                turn_id=episode_id,
                purpose="run",
                model="m",
                tokens_in=tokens,
                tokens_out=0,
                latency_ms=10,
                ok=True,
            )
        )


def episode_rows(episode_id: str, *, signature: str, closed: float) -> list[dict]:
    return [
        {
            "id": episode_id,
            "thread_id": THREAD,
            "person_id": PERSON,
            "status": "closed",
            "close_signal": "idle",
            "signature": signature,
            "turns": 1,
            "tool_calls": 2,
            "refs": [f"turn:{episode_id}"],
            "distilled_at": 0.0,
            "started_at": closed - 120,
            "closed_at": closed,
        }
    ]


class ScriptedLLM:
    """One JSON answer per call, in order; the ladder prompt is the only consumer here."""

    def __init__(self, answers: list[dict]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def complete(self, system, user, **kwargs):
        self.prompts.append(str(system))
        return json.dumps(self.answers.pop(0)) if self.answers else "{}"


def engine(tmp_path, db, llm=None, *, skills=None) -> CognitionWorker:
    config = build_config(tmp_path)
    return CognitionWorker(
        db,
        config,
        MemoryStore(db, config),
        skills=skills or SkillStore(db, config.skills),
        llm=llm,
        language="id",
    )


def test_two_episodes_of_one_signature_measure_together(tmp_path) -> None:
    db = database()
    now = time.time()
    seed_episode(db, "e1", closed=now - 600, tokens=100)
    seed_episode(db, "e2", closed=now - 300, tokens=300)

    found = efficiency.evidence(db, SIGNATURE, now=now)

    assert found.count == 2
    assert found.median_tokens == 200.0
    assert found.steps == 4 and found.failed_steps == 0
    assert efficiency.eligible(found, min_episodes=2)
    assert efficiency.signatures(db, now=now) == [SIGNATURE]


def test_another_signature_never_compares(tmp_path) -> None:
    db = database()
    now = time.time()
    seed_episode(db, "e1", closed=now - 600)
    seed_episode(db, "e2", signature="scope=thread;tools=send;targets=room", closed=now - 300)

    assert efficiency.evidence(db, SIGNATURE, now=now).count == 1
    assert not efficiency.eligible(efficiency.evidence(db, SIGNATURE, now=now), min_episodes=2)


def test_a_failed_step_makes_the_evidence_ineligible(tmp_path) -> None:
    db = database()
    now = time.time()
    seed_episode(db, "e1", closed=now - 600)
    seed_episode(db, "e2", closed=now - 300)
    with db.connect() as conn:
        db.upsert(
            conn,
            turn_steps,
            [
                {
                    "turn_id": "e2",
                    "step_index": 9,
                    "step": "drafted",
                    "tool": "write",
                    "args_hash": "f" * 64,
                    "result": "error: denied",
                    "status": "failed",
                    "started_at": now - 300,
                    "finished_at": now - 300,
                }
            ],
            ["turn_id", "step_index"],
        )

    found = efficiency.evidence(db, SIGNATURE, now=now)

    assert found.failed_steps == 1
    assert not efficiency.eligible(found, min_episodes=2)


def test_promote_writes_a_revision_a_priority_and_a_notice(tmp_path) -> None:
    db = database()
    now = time.time()
    seed_episode(db, "e1", closed=now - 600)
    seed_episode(db, "e2", closed=now - 300)
    config = build_config(tmp_path)
    skills = SkillStore(db, config.skills)
    skills.save(
        "read-write",
        "Read then write a file.",
        "# Steps\n1. read\n2. write\n",
        source_episode="distill:e1",
    )
    llm = ScriptedLLM([{"action": "promote", "skill": "read-write", "reason": "cheaper"}])

    asyncio.run(engine(tmp_path, db, llm, skills=skills)._ladder())

    assert len(skills.revisions("read-write")) == 2
    assert skills.get("read-write").priority == 1
    notices = ActionLedger(db).recent_actions()
    assert any(str(row["summary"]).startswith("promoted:") for row in notices)


def test_propose_automation_creates_an_approval_and_no_row(tmp_path) -> None:
    from support import make_agent

    db = database()
    now = time.time()
    seed_episode(db, "e1", closed=now - 600)
    seed_episode(db, "e2", closed=now - 300)
    with db.connect() as conn:
        db.upsert(conn, people, [{"person_id": PERSON, "name": "Budi"}], ["person_id"])
        db.upsert(
            conn,
            threads,
            [{"thread_id": THREAD, "name": "", "reset_ts": 0.0, "kind": "dm", "gateway": "fake"}],
            ["thread_id"],
        )
        db.upsert(
            conn,
            thread_people,
            [{"thread_id": THREAD, "person_id": PERSON, "last_seen": 0.0, "interactions": 1}],
            ["thread_id", "person_id"],
        )
    env = make_agent(tmp_path)
    env.agent.db = db
    env.history.db = db
    config = build_config(tmp_path)
    skills = SkillStore(db, config.skills)
    skills.save(
        "read-write",
        "Read then write a file.",
        "# Steps\n1. read\n2. write\n",
        source_episode="distill:e1",
    )
    manifest = {
        "owner": PERSON,
        "name": "nightly",
        "event": {"kind": "schedule", "at": ["01:00"], "tz": "UTC"},
        "steps": [{"kind": "send", "to": "owner_dm"}],
        "policy": {"budget_per_day": 1, "quiet_hours": "22:00-07:00", "quiet": "skip"},
    }
    llm = ScriptedLLM(
        [
            {
                "action": "propose_automation",
                "skill": "read-write",
                "reason": "repeats",
                "manifest": manifest,
            }
        ]
    )
    from eidesoul.engine.automation import AutomationStore

    store = AutomationStore(db, env.agent.config.automations)

    async def propose(body, thread_id, gateway=None):
        return await store.propose(env.agent, body, thread_id=thread_id, gateway=gateway)

    engine_obj = engine(tmp_path, db, llm, skills=skills)
    engine_obj.history = env.history
    engine_obj.propose = propose
    asyncio.run(engine_obj._ladder())

    assert db.fetch_all(automations.select()) == []
    assert db.fetch_all(automation_runs.select()) == []
    pending = db.fetch_all(approvals.select())
    assert len(pending) == 1 and pending[0]["kind"] == "automation"


def test_the_same_signature_does_not_propose_twice_inside_the_cooldown(tmp_path) -> None:
    db = database()
    now = time.time()
    seed_episode(db, "e1", closed=now - 600)
    seed_episode(db, "e2", closed=now - 300)
    config = build_config(tmp_path)
    skills = SkillStore(db, config.skills)
    skills.save(
        "read-write",
        "Read then write a file.",
        "# Steps\n1. read\n2. write\n",
        source_episode="distill:e1",
    )
    skills.record_notice("read-write", "proposal:read-write: first")
    manifest = {
        "owner": PERSON,
        "name": "nightly",
        "event": {"kind": "schedule", "at": ["01:00"], "tz": "UTC"},
        "steps": [{"kind": "send", "to": "owner_dm"}],
        "policy": {"budget_per_day": 1, "quiet_hours": "22:00-07:00", "quiet": "skip"},
    }
    llm = ScriptedLLM(
        [
            {
                "action": "propose_automation",
                "skill": "read-write",
                "reason": "again",
                "manifest": manifest,
            }
        ]
    )
    called: list[dict] = []

    async def propose(body, thread_id, gateway=None):
        called.append(body)

    engine_obj = engine(tmp_path, db, llm, skills=skills)
    engine_obj.propose = propose
    engine_obj.history = None
    asyncio.run(engine_obj._ladder())

    assert called == []
    notices = " ".join(str(row["summary"]) for row in ActionLedger(db).recent_actions())
    assert "cooldown" in notices
