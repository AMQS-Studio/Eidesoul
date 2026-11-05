"""Turn cost: one `llm_calls` row per call, a call ceiling, and a stable prefix (ADR-0025)."""


import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import select
from support import build_config, database

from monoagens.engine.access.roles import Role
from monoagens.engine.llm import LLM, prompt_prefix_hash
from monoagens.engine.registry import Handler
from monoagens.engine.schema import llm_calls
from monoagens.engine.tools import ToolContext
from monoagens.engine.turn.prompts import Prompt, system_prompt


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def usage(prompt=10, completion=5):
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)


def text_response(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))],
        usage=usage(),
    )


def tool_response(name: str, args: dict):
    call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[call]))],
        usage=usage(),
    )


def rows():
    return database().fetch_all(select(llm_calls).order_by(llm_calls.c.id))


def test_one_row_per_call_with_purpose_and_usage(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("K", "x")
    config = build_config(tmp_path)
    db = database()
    llm = LLM(config, db=db)
    llm.client = FakeClient([text_response("ok")])

    assert asyncio.run(llm.complete("s", "u", purpose="decision")) == "ok"
    found = rows()

    assert len(found) == 1
    assert found[0]["purpose"] == "decision"
    assert found[0]["tokens_in"] == 10
    assert found[0]["tokens_out"] == 5
    assert found[0]["ok"] is True


def test_failed_call_is_recorded_as_not_ok(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("K", "x")
    config = build_config(tmp_path)
    db = database()
    llm = LLM(config, db=db)

    class Broken:
        def __init__(self):
            self.chat = SimpleNamespace(completions=self)

        async def create(self, **kwargs):
            raise OSError("down")

    llm.client = Broken()
    assert asyncio.run(llm.complete("s", "u")) == ""
    found = rows()
    assert len(found) == 1
    assert found[0]["ok"] is False


def test_call_ceiling_stops_the_loop_with_a_notice(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("K", "x")
    config = build_config(tmp_path)
    from dataclasses import replace

    config = replace(config, llm=replace(config.llm, max_calls_per_turn=3))
    db = database()
    llm = LLM(config, db=db)

    async def echo(args, ctx):
        return "ok"

    spec = Handler(
        name="probe",
        description="p",
        fn=echo,
        family="read",
        parameters={"type": "object", "properties": {}},
    )
    llm.client = FakeClient(
        [tool_response("probe", {}), tool_response("probe", {}), text_response("done")]
    )
    ctx = ToolContext(config, (Role("member", tools=("*",)),), ("*",))

    answer = asyncio.run(llm.run("system", "user", [spec], ctx))

    assert answer == "done"
    assert len(llm.client.calls) == 3
    assert "tools" not in llm.client.calls[-1]  # the last call is the wrap-up
    assert len(rows()) == 3


def test_two_system_messages_and_a_stable_prefix(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("K", "x")
    config = build_config(tmp_path)
    llm = LLM(config)
    prompt = Prompt("stable", "volatile")

    messages = llm._system_messages(prompt)

    assert [item["content"] for item in messages] == ["stable", "volatile"]
    first = prompt_prefix_hash("stable", [])
    second = prompt_prefix_hash("stable", [])
    assert first == second


def test_merge_fallback_for_a_provider_without_two_system_messages(monkeypatch) -> None:
    monkeypatch.setenv("K", "x")
    config = build_config("/tmp")
    llm = LLM(config)
    messages = [
        {"role": "system", "content": "a"},
        {"role": "system", "content": "b"},
        {"role": "user", "content": "u"},
    ]

    assert llm._merge_system(messages, SimpleNamespace(status_code=400)) is True
    assert len(messages) == 2
    assert messages[0]["content"] == "a\n\nb"


def test_system_prompt_keeps_date_out_of_the_stable_part(tmp_path) -> None:
    config = build_config(tmp_path)
    prompt = system_prompt(config, (Role("member"),))

    assert isinstance(prompt, Prompt)
    assert "Date:" not in prompt.stable
    assert "Date:" in prompt.volatile


def test_normalized_tool_calls_keep_their_name_and_arguments(tmp_path, monkeypatch) -> None:
    """ADR-0044 §4.2: the loop consumes ProviderToolCall, not the raw SDK shape."""
    monkeypatch.setenv("K", "x")
    config = build_config(tmp_path)
    db = database()
    llm = LLM(config, db=db)
    seen: dict = {}

    async def probe(args, ctx):
        seen.update(args)
        return "ok"

    spec = Handler(
        name="probe",
        description="p",
        fn=probe,
        family="read",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    llm.client = FakeClient([tool_response("probe", {"path": "a"}), text_response("done")])
    ctx = ToolContext(config, (Role("member", tools=("*",)),), ("*",))
    steps: list[tuple] = []

    answer = asyncio.run(
        llm.run(
            "system",
            "user",
            [spec],
            ctx,
            on_step=lambda name, args, result, *_: steps.append((name, args, result)),
        )
    )

    assert answer == "done"
    assert seen == {"path": "a"}
    assert steps == [("probe", {"path": "a"}, "ok")]
    tool = [item for item in llm.client.calls[1]["messages"] if item["role"] == "tool"][-1]
    assert tool["tool_call_id"] == "call-1"
    assert tool["content"] == "ok"
