"""Privacy contract: level matrix, fluidity, audit gate, confirmations, veto, credentials."""


import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

from support import FakeLLM, FakeMem0, build_config, database, inbound_message, make_agent

from eidesoul.engine.cognition import CognitionWorker
from eidesoul.engine.config import DatabaseConfig, PrivacyConfig
from eidesoul.engine.memory import MemoryHit
from eidesoul.engine.memory.assess import assess_budget
from eidesoul.engine.memory.mem0 import Mem0Backend
from eidesoul.engine.memory.privacy import PrivacyPolicy
from eidesoul.engine.memory.store import MemoryStore
from eidesoul.engine.roles import Role
from eidesoul.engine.stores.history import HistoryStore
from eidesoul.engine.turn.context import Message


def policy() -> PrivacyPolicy:
    return PrivacyPolicy(PrivacyConfig())


def hit(level: int, **kwargs) -> MemoryHit:
    base = {
        "id": f"m{level}",
        "text": "fakta",
        "level": level,
        "owner_id": "628111",
        "subject_id": "628111",
    }
    base.update(kwargs)
    return MemoryHit(**base)


def test_level_matrix_group_and_dm() -> None:
    rules = policy()

    assert rules.show(hit(2), direct=False) is True
    assert rules.disclose(hit(2), requester="999", direct=False) == "allow"
    assert rules.show(hit(3), direct=False) is False
    assert rules.disclose(hit(3), requester="628111", direct=False) == "ask"
    assert rules.disclose(hit(3), requester="999", direct=False) == "deny"
    assert rules.disclose(hit(3, confirmed=False), requester="628111", direct=False) == "deny"

    assert rules.disclose(hit(3), requester="628111", direct=True) == "allow"
    assert rules.disclose(hit(4, confirmed=False), requester="628111", direct=True) == "ask"
    assert rules.disclose(hit(4), requester="628111", direct=True) == "allow"
    assert rules.disclose(hit(4), requester="999", direct=True) == "deny"

    vetoed = hit(1, vetoed=True)
    assert rules.show(vetoed, direct=True) is False
    assert rules.disclose(vetoed, requester="628111", direct=True) == "deny"


class ScriptLLM(FakeLLM):
    """FakeLLM with scripted audit/answer JSON."""

    def __init__(self, *, audit=None, answer=None, text: str = "halo"):
        super().__init__(answer=text)
        self.audit = audit or {"mentions": [], "verdict": "allow", "reply": "", "question": ""}
        self.answer_payload = answer or {"kind": "unclear", "level": 0}

    async def complete(self, system, user, **kwargs):
        self.decisions += 1
        if "audit" in system.lower():
            return json.dumps(self.audit)
        if "Classify the sender" in system:
            return json.dumps(self.answer_payload)
        return await super().complete(system, user, **kwargs)


class CaptureLLM(FakeLLM):
    """The worker's assess call: one scripted item per attempt; `None` means no JSON."""

    def __init__(self, replies: list[dict | None]):
        super().__init__()
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def complete(self, system, user, **kwargs):
        if "long-term memory" not in system:
            return await super().complete(system, user, **kwargs)
        self.calls.append({"system": system, "max_tokens": kwargs.get("max_tokens")})
        scripted = self.replies.pop(0) if self.replies else None
        if scripted is None:
            return ""
        wanted = json.loads(user)["items"][0]["id"]
        return json.dumps({"items": [{"id": wanted, **scripted}]})


def capture_env(tmp_path, replies: list[dict | None], *, ask=None) -> SimpleNamespace:
    """The real intake seam: MemoryStore + HistoryStore + CognitionQueue, no gateway."""
    config = build_config(tmp_path)
    db = database()
    store = MemoryStore(db, config)
    worker = CognitionWorker(
        db, config, store, llm=CaptureLLM(replies), language="id", ask=ask
    )
    history = HistoryStore(db, config.workspace, memories=store, job_sink=worker.queue)
    return SimpleNamespace(
        config=config, db=db, store=store, worker=worker, history=history, llm=worker.llm
    )


def capture(
    env: SimpleNamespace,
    text: str,
    *,
    thread: str = "628111@s.whatsapp.net",
    person: str = "628111",
    message_id: str = "m1",
    audience: str = "private",
) -> dict[str, str]:
    message = Message(
        id=message_id,
        sender_name="Budi",
        sender_id=person,
        when=datetime.now(),
        text=text,
        sender_address=f"{person}@s.whatsapp.net",
    )
    assert env.history.intake(thread, message, person, audience=audience) is True
    return asyncio.run(env.worker.drain(kind="capture", limit=10))


def row(env: SimpleNamespace, table: str = "memories") -> dict | None:
    from sqlalchemy import text as sql

    return env.db.fetch_one(sql(f"SELECT * FROM {table} ORDER BY created_at LIMIT 1"))


def counted(env: SimpleNamespace, table: str) -> int:
    from sqlalchemy import text as sql

    return int(env.db.scalar(sql(f"SELECT count(*) FROM {table}")) or 0)


def assess(level: int, *, subject: str = "", ask: bool = False) -> dict:
    return {
        "worth": True,
        "level": level,
        "subject": subject,
        "kind": "fact",
        "text": "fakta",
        "supersedes": None,
        "category": "general",
        "durability": "normal",
        "ask": ask,
    }


def review(env, inbound, reply, hits):
    return asyncio.run(env.agent.privacy.review(inbound, reply, hits, env.gateway))


def test_fluidity_level_0_2_never_calls_the_auditor(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM()
    inbound = inbound_message("halo", thread="111@g.us", dm=False)

    result = review(env, inbound, "kopi ya", [hit(0), hit(2)])

    assert result.handled is False
    assert result.reply == "kopi ya"
    assert env.agent.llm.decisions == 0


def test_group_level_3_without_audit_asks(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM()
    inbound = inbound_message("halo", thread="111@g.us", dm=False)

    result = review(env, inbound, "si budi pernah bilang begitu", [hit(3)])

    assert result.handled is True
    assert env.gateway.sent
    assert "privat" in env.gateway.sent[0][1]
    approval = env.agent.approvals.latest("111@g.us", "628111")
    assert approval is not None
    assert approval.kind == "disclosure"
    assert approval.payload["reply"] == "si budi pernah bilang begitu"


def test_dm_owner_level_3_stays_fluid(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM()
    inbound = inbound_message("halo", thread="628111@s.whatsapp.net", dm=True)

    result = review(env, inbound, "ingat kopi kita", [hit(3)])

    assert result.handled is False
    assert result.reply == "ingat kopi kita"
    assert env.agent.llm.decisions == 0


def test_vetoed_hit_is_denied_and_audit_rewrite_wins(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM(
        audit={"mentions": [], "verdict": "allow", "reply": "aman kok", "question": ""}
    )
    inbound = inbound_message("halo", thread="111@g.us", dm=False)

    result = review(env, inbound, "dia pernah begitu", [hit(1, vetoed=True)])

    assert result.handled is False
    assert result.reply == "aman kok"


def test_disclosure_confirmation_sends_the_stored_draft(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM(answer={"kind": "yes", "level": 0})
    inbound = inbound_message("halo", thread="111@g.us", dm=False)
    review(env, inbound, "draft rahasia", [hit(3)])

    handled = asyncio.run(
        env.agent.privacy.handle_pending(
            inbound_message("ya", thread="111@g.us", dm=False),
            env.gateway,
            "628111",
        )
    )

    assert handled is True
    assert env.gateway.sent[-1][1] == "draft rahasia"
    approval = env.agent.approvals.get(1)
    assert approval.status == "approved"


def test_other_sender_cannot_confirm(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM(answer={"kind": "yes", "level": 0})
    inbound = inbound_message("halo", thread="111@g.us", dm=False)
    review(env, inbound, "draft rahasia", [hit(3)])

    handled = asyncio.run(
        env.agent.privacy.handle_pending(
            inbound_message(
                "ya", thread="111@g.us", dm=False, sender="628999", person_id="628999"
            ),
            env.gateway,
            "628999",
        )
    )

    assert handled is False
    assert len(env.gateway.sent) == 1
    assert env.agent.approvals.get(1).status == "pending"


def test_capture_level_4_stores_unconfirmed_and_asks(tmp_path) -> None:
    asked: list[tuple] = []

    async def ask(thread_id, person_id, ids, level, question):
        asked.append((thread_id, person_id, ids, level))

    env = capture_env(tmp_path, [assess(4, ask=True)], ask=ask)
    capture(env, "aku pernah melakukan hal memalukan")

    found = row(env)
    assert found["state"] == "ok"
    assert int(found["level"]) == 4
    assert found["confirmed"] is False
    assert found["via"] == "turn"
    assert asked
    assert asked[0][1] == "628111"
    assert asked[0][3] == 4


def test_capture_level_3_is_stored_without_a_question(tmp_path) -> None:
    env = capture_env(tmp_path, [assess(3)])

    capture(env, "aku lagi banyak pikiran")

    found = row(env)
    assert int(found["level"]) == 3
    assert found["confirmed"] is True


def test_capture_skips_credentials_before_the_journal(tmp_path) -> None:
    env = capture_env(tmp_path, [assess(2)])

    assert capture(env, "api_key=abc123") == {}

    assert counted(env, "memory_events") == 0
    assert counted(env, "memories") == 0


def test_capture_drops_a_third_party_fact_in_a_group(tmp_path) -> None:
    env = capture_env(tmp_path, [assess(2, subject="Budi")])

    capture(env, "budi baru pindah kerja", thread="111@g.us", audience="group")

    found = row(env)
    assert found["state"] == "not_worth"
    assert found["level"] is None


def test_assess_budget_grows_with_the_turn_and_stays_capped() -> None:
    assert assess_budget("x" * 100) == 812
    assert assess_budget("x" * 3000) == 1175
    assert assess_budget("x" * 100_000) == 4000


def test_capture_retries_assess_with_a_bigger_budget(tmp_path) -> None:
    env = capture_env(tmp_path, [None, assess(3)])

    capture(env, "aku lagi banyak pikiran")

    found = row(env)
    assert int(found["level"]) == 3
    assert found["confirmed"] is True
    assert len(env.llm.calls) == 2
    first, retry = env.llm.calls
    assert first["max_tokens"] >= 800
    assert retry["max_tokens"] == first["max_tokens"] * 2


def test_capture_is_dropped_when_assess_returns_nothing(tmp_path) -> None:
    env = capture_env(tmp_path, [None, None])

    capture(env, "halo apa kabar semuanya")

    found = row(env)
    assert found["state"] == "failed"
    assert found["level"] is None
    assert len(env.llm.calls) == 2


def test_privacy_confirmation_sets_level_and_confirmation(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM(answer={"kind": "level", "level": 4})
    approval = asyncio.run(
        env.agent.approvals.create(
            kind="privacy",
            thread_id="628111@s.whatsapp.net",
            sender="628111",
            tool="memory",
            args={"memory_ids": ["m1"]},
            payload={"level": 4, "question": "seberapa rahasia?"},
            summary="seberapa rahasia?",
        )
    )
    env.memory.hits["m1"] = MemoryHit(
        id="m1", text="hal memalukan", level=4, owner_id="628111", confirmed=False
    )

    handled = asyncio.run(
        env.agent.privacy.handle_pending(
            inbound_message("rahasia", thread="628111@s.whatsapp.net"),
            env.gateway,
            "628111",
        )
    )

    assert handled is True
    hit_after = env.memory.hits["m1"]
    assert hit_after.level == 4
    assert hit_after.confirmed is True
    assert env.agent.approvals.get(approval.id).status == "approved"


def test_unclear_answer_keeps_pending(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM(answer={"kind": "unclear", "level": 0})
    inbound = inbound_message("halo", thread="111@g.us", dm=False)
    review(env, inbound, "draft", [hit(3)])

    handled = asyncio.run(
        env.agent.privacy.handle_pending(
            inbound_message("hmm gimana ya", thread="111@g.us", dm=False),
            env.gateway,
            "628111",
        )
    )

    assert handled is True
    assert env.agent.approvals.get(1).status == "pending"
    assert "ya atau tidak" in env.gateway.sent[-1][1]


def test_memory_tool_requires_admin_for_global_and_subject_for_veto(tmp_path) -> None:
    from eidesoul.engine.tools.memory import _memory

    env = make_agent(tmp_path)
    member = env.config.roles.default_role()
    ctx = env.agent.tool_context(
        "chat", "628111", (member,), env.gateway, person_id="628111", direct=True
    )
    denied = asyncio.run(_memory({"action": "add", "scope": "global", "text": "fakta"}, ctx))
    assert "memory_admin" in denied

    owner = Role("owner", tools=("*",), memory_admin=True)
    owner_ctx = env.agent.tool_context(
        "chat", "628111", (owner,), env.gateway, person_id="628111", direct=True
    )
    saved = asyncio.run(
        _memory({"action": "add", "scope": "global", "text": "fakta", "level": 1}, owner_ctx)
    )
    assert saved.startswith("ok:")

    memory_id = next(reversed(env.memory.hits))
    env.memory.hits[memory_id] = MemoryHit(
        id=memory_id, text="fakta", subject_id="628999", owner_id="628111", level=1
    )
    wrong = asyncio.run(_memory({"action": "veto", "id": memory_id}, owner_ctx))
    assert "error:" in wrong

    subject_ctx = env.agent.tool_context(
        "chat", "628999", (member,), env.gateway, person_id="628999", direct=True
    )
    right = asyncio.run(_memory({"action": "veto", "id": memory_id}, subject_ctx))
    assert right.startswith("ok:")


def test_agent_turn_asks_then_sends_after_confirmation(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM(
        audit={"mentions": [], "verdict": "ask", "reply": "", "question": ""},
        answer={"kind": "yes", "level": 0},
        text="halo juga",
    )
    person = env.agent.identity.resolve("fake", "628111")
    env.memory.hits["m3"] = MemoryHit(
        id="m3",
        text="aib budi",
        level=3,
        owner_id=person,
        subject_id=person,
        confirmed=True,
    )
    group = inbound_message(
        "halo eidesoul", thread="111@g.us", dm=False, mention=True
    )

    asyncio.run(env.agent.handle(group, env.gateway))

    assert env.gateway.sent[-1][1] == (
        "Itu menyentuh hal privat. Boleh aku sebutkan di sini? Balas ya atau tidak."
    )
    approval = env.agent.approvals.latest("111@g.us", "628111")
    assert approval.kind == "disclosure"

    asyncio.run(
        env.agent.handle(inbound_message("ya", thread="111@g.us", dm=False), env.gateway)
    )

    assert env.gateway.sent[-1][1] == "halo juga"
    assert env.agent.approvals.get(approval.id).status == "approved"


def test_vetoed_memory_is_excluded_from_context(tmp_path) -> None:
    config = build_config(
        tmp_path,
        database_config=DatabaseConfig(url="postgresql+psycopg://u:p@db:5432/eidesoul", url_env=""),
    )
    memory = Mem0Backend(config=config, db=None)
    memory._memory = FakeMem0()
    memory_id = asyncio.run(
        memory.add("rahasia kecil", scope="person", person_id="1", thread_id="c", level=1)
    )[0]

    assert asyncio.run(memory.veto(memory_id, subject_id="2")) is False
    assert asyncio.run(memory.veto(memory_id, subject_id="1")) is True
    context = asyncio.run(
        memory.context(person_id="1", thread_id="c", query="rahasia", direct=True)
    )
    assert context.text == ""
    assert context.hits == ()


def test_capture_raises_visibility_of_the_turn(tmp_path) -> None:
    env = capture_env(tmp_path, [assess(3)])

    capture(env, "aku lagi banyak pikiran", message_id="m-capture")

    assert env.history.find("628111@s.whatsapp.net", "m-capture").visibility == 3
    assert int(row(env)["level"]) == 3
