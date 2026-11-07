"""ADR-0003: native wire request/response fixtures, no live network."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from support import build_config

from monoagens.engine.llm import LLM
from monoagens.providers import gemini as gemini_provider
from monoagens.providers.gemini import GeminiClient, _contents, _response
from monoagens.providers.openai import normalize_response


def test_openai_response_normalizes_text_usage_and_tool_calls() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="ok",
                    tool_calls=[
                        SimpleNamespace(
                            id="c1",
                            function=SimpleNamespace(name="read", arguments='{"path":"a"}'),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4),
    )

    found = normalize_response(response)

    assert found.text == "ok"
    assert found.tool_calls[0].name == "read"
    assert found.usage.prompt_tokens == 3
    assert found.finish_reason == "tool_calls"


def test_openai_reasoning_content_is_thinking_not_answer_text() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="The answer.",
                    reasoning_content="First I check the dates.",
                ),
                finish_reason="stop",
            )
        ]
    )

    found = normalize_response(response)

    assert found.text == "The answer."
    assert found.thinking == "First I check the dates."


def test_anthropic_thinking_blocks_are_separated_from_the_answer() -> None:
    """Claude's compatibility wire returns thinking as content blocks, never as text."""
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=[
                        {"type": "thinking", "thinking": "Weigh the options."},
                        {"type": "text", "text": "Here is the answer."},
                    ]
                ),
                finish_reason="stop",
            )
        ]
    )

    found = normalize_response(response)

    assert found.text == "Here is the answer."
    assert found.thinking == "Weigh the options."


def test_gemini_thought_parts_are_thinking_not_answer_text() -> None:
    found = _response(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Consider the source.", "thought": True},
                            {"text": "The answer."},
                        ]
                    },
                    "finishReason": "STOP",
                }
            ]
        }
    )

    assert found.text == "The answer."
    assert found.thinking == "Consider the source."



def test_gemini_request_carries_native_tools_sampling_and_safety(monkeypatch) -> None:
    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "candidates": [
                    {"content": {"parts": [{"text": "halo"}]}, "finishReason": "STOP"}
                ],
                "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
            }

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json=None, headers=None):
            captured.update(url=url, json=json, headers=headers)
            return FakeResponse()

    monkeypatch.setattr(gemini_provider.httpx, "AsyncClient", FakeClient)
    client = GeminiClient("https://example.invalid/v1beta", "key")
    result = asyncio.run(
        client.create(
            model="m",
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": "hi"},
            ],
            tools=[{"type": "function", "function": {"name": "read", "parameters": {}}}],
            temperature=0.9,
            top_p=0.95,
            top_k=40,
            safety="off",
        )
    )

    body = captured["json"]
    assert captured["url"].endswith("/models/m:generateContent")
    assert captured["headers"]["x-goog-api-key"] == "key"
    assert body["systemInstruction"]["parts"][0]["text"] == "system"
    assert body["contents"][0]["role"] == "user"
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "read"
    assert body["generationConfig"] == {"temperature": 0.9, "topP": 0.95, "topK": 40}
    assert {item["threshold"] for item in body["safetySettings"]} == {"OFF"}
    assert result.text == "halo"
    assert result.usage.prompt_tokens == 2


def test_gemini_function_call_and_block_reason_are_visible() -> None:
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "checking"},
                        {"functionCall": {"name": "read", "args": {"path": "a"}}},
                    ]
                },
                "finishReason": "STOP",
            }
        ]
    }
    found = _response(body)
    assert found.text == "checking"
    assert found.tool_calls[0].name == "read"
    assert json.loads(found.tool_calls[0].arguments) == {"path": "a"}

    blocked = _response(
        {
            "promptFeedback": {"blockReason": "SAFETY"},
            "candidates": [],
        }
    )
    assert blocked.blocked is True
    assert blocked.finish_reason == "blocked"


def test_gemini_tool_message_uses_the_function_name() -> None:
    contents = _contents(
        [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "read", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ]
    )

    assert contents[-1]["parts"][0]["functionResponse"]["name"] == "read"


def test_llm_builds_the_native_client_from_the_wire(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("K", "key")
    config = build_config(tmp_path)
    config = replace(
        config,
        llm=replace(config.llm, wire="gemini", native=True, base_url="http://x"),
    )
    llm = LLM(config)

    assert isinstance(llm.client, GeminiClient)


def test_llm_rejects_an_unknown_wire(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("K", "key")
    config = build_config(tmp_path)
    config = replace(config, llm=replace(config.llm, wire="nope", native=True))
    with pytest.raises(ValueError, match="no native client"):
        LLM(config)
