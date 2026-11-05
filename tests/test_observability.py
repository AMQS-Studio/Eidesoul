"""Observability: one turn id, a parseable JSON switch, cost rows, and `/status` (ADR-0019)."""


import asyncio
import json
import logging
import time
from io import StringIO
from types import SimpleNamespace

from sqlalchemy import insert, select
from support import FakeLLM, build_config, database, inbound_message, make_agent

from monoagens.engine.access.roles import Role
from monoagens.engine.commands.status import latency, percentile
from monoagens.engine.llm import LLM
from monoagens.engine.memory.store import MemoryStore
from monoagens.engine.schema import llm_calls, messages, turns
from monoagens.engine.soul import Soul
from monoagens.engine.stores.turns import turn_identity
from monoagens.engine.turn import observability


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.stream = StringIO()

    def emit(self, record: logging.LogRecord) -> None:
        self.stream.write(self.format(record) + "\n")

    def records(self) -> list[dict]:
        return [
            json.loads(line)
            for line in self.stream.getvalue().splitlines()
            if line.strip()
        ]


def captured(fmt: str = "json") -> Capture:
    handler = Capture()
    handler.setFormatter(observability.formatter(fmt))
    handler.addFilter(observability.ContextFilter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return handler


def test_json_lines_have_the_mandatory_fields() -> None:
    handler = captured("json")
    try:
        with observability.bind(turn_id="t-1", thread_id="th-1", gateway="console"):
            logging.getLogger("monoagens.test").info(
                "turn done", extra={"event": "turn.done", "duration_ms": 12}
            )
        lines = handler.records()
    finally:
        logging.getLogger().removeHandler(handler)
    found = next(line for line in lines if line["event"] == "turn.done")
    assert found["ts"]
    assert found["level"] == "INFO"
    assert found["logger"] == "monoagens.test"
    assert found["turn_id"] == "t-1"
    assert found["thread_id"] == "th-1"
    assert found["gateway"] == "console"
    assert found["duration_ms"] == 12
    assert "prompt" not in json.dumps(found)


def test_json_formatter_redacts_credentials() -> None:
    handler = captured("json")
    try:
        with observability.bind(turn_id="t-2"):
            logging.getLogger("monoagens.test").warning(
                "dsn postgresql://user:supersecret@db/x key sk-abcdefghijkl",
                extra={"event": "db.failed"},
            )
        raw = handler.stream.getvalue()
        found = handler.records()[0]
    finally:
        logging.getLogger().removeHandler(handler)
    assert "supersecret" not in raw
    assert "sk-abcdefghijkl" not in raw
    assert found["level"] == "WARNING"
    assert found["turn_id"] == "t-2"


def test_a_turn_logs_and_cost_rows_share_one_turn_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("K", "x")
    env = make_agent(tmp_path)
    db = env.db
    llm = LLM(env.config, db=db)
    decision = FakeLLM().decision
    llm.client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeClient(
            [
                text_response(json.dumps(decision)),
                text_response("halo, aku di sini"),
                text_response("ok"),
            ]
        ))
    )
    env.agent.llm = llm
    secret = "jangan-sebut-rahasia-ini"
    inbound = inbound_message(f"halo {secret}")
    handler = captured("json")
    try:
        asyncio.run(env.agent.handle(inbound, env.gateway))
        raw = handler.stream.getvalue()
        lines = handler.records()
    finally:
        logging.getLogger().removeHandler(handler)

    turn_id = turn_identity(env.gateway.name, inbound.thread, inbound.payload.id)
    turn_lines = [line for line in lines if line.get("turn_id") == turn_id]
    assert turn_lines, "the turn must log with its turn id"
    assert all(line["event"] and line["level"] and line["ts"] for line in turn_lines)
    assert secret not in raw

    rows = db.fetch_all(select(llm_calls).order_by(llm_calls.c.id))
    assert rows
    assert {row["turn_id"] for row in rows} == {turn_id}
    assert all(int(row["tokens_in"] or 0) > 0 for row in rows)
    assert {"decision", "reply"} <= {row["purpose"] for row in rows}
    assert db.fetch_one(select(turns).where(turns.c.id == turn_id)) is not None

    observability.clear()
    asyncio.run(llm.complete("s", "u", purpose="digest"))
    background = db.fetch_all(select(llm_calls).order_by(llm_calls.c.id.desc()).limit(1))[0]
    assert background["turn_id"] is None
    assert background["purpose"] == "digest"


class FakeClient:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def with_options(self, **kwargs):
        return self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def text_response(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _turn_row(turn_id: str, status: str, *, created: float, updated: float) -> dict:
    return {
        "id": turn_id,
        "thread_id": "th",
        "person_id": "628111",
        "status": status,
        "step": "received",
        "attempt": 0,
        "lease_owner": "",
        "lease_until": 0.0,
        "generation": 0,
        "planned_chunks": 0,
        "delivered_chunks": 0,
        "shutdown_epoch": 0,
        "boot_epoch": 0,
        "last_error": "",
        "created_at": created,
        "updated_at": updated,
    }


def test_status_reports_real_counters_and_rough_buckets(tmp_path) -> None:
    env = make_agent(tmp_path, soul=Soul(language="en", locale="en-US", timezone="UTC"))
    db = env.db
    now = time.time()
    rows = [
        _turn_row(f"d{index}", "delivered", created=now - 5, updated=now - 5 + ms / 1000)
        for index, ms in enumerate((10, 20, 30, 40, 50))
    ]
    rows += [
        _turn_row("i1", "interrupted", created=now, updated=now),
        _turn_row("e1", "expired", created=now, updated=now),
        _turn_row("r1", "received", created=now, updated=now),
    ]
    with db.connect() as conn:
        conn.execute(insert(turns), rows)
        conn.execute(
            insert(messages),
            [
                {
                    "thread_id": "th",
                    "msg_id": "m-notice",
                    "ts": now,
                    "text": "notice",
                    "data": {"kind": "notice:shutdown"},
                    "author": "bot",
                }
            ],
        )
        conn.execute(
            insert(llm_calls),
            [
                {
                    "ts": now,
                    "turn_id": "d0",
                    "purpose": "reply",
                    "model": "m",
                    "tokens_in": 30,
                    "tokens_out": 15,
                    "latency_ms": 5,
                    "ok": True,
                },
                {
                    "ts": now,
                    "turn_id": "d1",
                    "purpose": "decision",
                    "model": "m",
                    "tokens_in": 10,
                    "tokens_out": 5,
                    "latency_ms": 5,
                    "ok": True,
                },
            ],
        )

    async def run(roles):
        return await env.agent._command(
            "status", "", "th", "628111", roles, env.gateway, inbound_message("/status")
        )

    admin = (
        Role("administrator", protected=True, tools=("*",), commands=("*",), memory_admin=True),
    )
    text = asyncio.run(run(admin))
    assert "• Turns: 1 received, 0 running, 5 delivered, 1 interrupted, 1 expired" in text
    assert "• Turn latency: p50 30 ms, p95 50 ms (5 turns)" in text
    assert "2 calls, 40 in / 20 out (24h), 30 per turn" in text
    assert "Corpus notices: 1 in 24h" in text
    assert "Lease generation" in text
    assert "Schema: version" in text
    assert "Automation: 0 active" in text
    assert "Lexicon: 0 warning(s)" in text
    assert "Search:" in text

    rough = asyncio.run(run((Role("member", tools=("read",), commands=("status",)),)))
    assert "Activity:" in rough
    assert "Uptime:" in rough
    assert "Turn latency" not in rough
    assert "• Turns:" not in rough
    assert "Cost:" not in rough
    assert "Database:" not in rough


def test_latency_helpers_are_honest_when_empty() -> None:
    assert percentile([], 0.5) == 0.0
    assert latency([]) == (0, 0, 0.0)
    assert latency([10.0, 100.0])[1] == 100


def test_llm_calls_retention_deletes_old_rows(tmp_path) -> None:
    config = build_config(tmp_path)
    db = database()
    now = time.time()
    with db.connect() as conn:
        conn.execute(
            insert(llm_calls),
            [
                {
                    "ts": now - 100 * 86400,
                    "purpose": "reply",
                    "model": "m",
                    "tokens_in": 1,
                    "tokens_out": 1,
                    "latency_ms": 1,
                    "ok": True,
                },
                {
                    "ts": now,
                    "purpose": "reply",
                    "model": "m",
                    "tokens_in": 1,
                    "tokens_out": 1,
                    "latency_ms": 1,
                    "ok": True,
                },
            ],
        )
    store = MemoryStore(db, config)
    asyncio.run(store.purge(now=now))
    remaining = db.fetch_all(select(llm_calls))
    assert len(remaining) == 1
    assert float(remaining[0]["ts"]) >= now - 86400

