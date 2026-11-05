"""Native Anthropic wire: request mapping, response normalization, and HTTP failures."""


import asyncio
import json

import httpx
import pytest

from monoagens.providers import build_client, normalize_response
from monoagens.providers.anthropic import ANTHROPIC_VERSION, AnthropicClient
from monoagens.providers.base import (
    ProviderError,
    ProviderResponse,
    ProviderToolCall,
    ProviderUsage,
)


def request_kwargs(**overrides) -> dict:
    body = {
        "model": "operator-model",
        "max_tokens": 1024,
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": "stable prefix"},
            {"role": "system", "content": "volatile suffix"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,AAAA"},
                    },
                ],
            },
            {
                "role": "assistant",
                "content": "calling",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "search", "arguments": '{"query": "x"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "result text"},
            {"role": "user", "content": "finish"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "Search the archive.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                },
            }
        ],
    }
    body.update(overrides)
    return body


def client(handler, *, base_url: str = "https://api.anthropic.com/v1") -> AnthropicClient:
    return AnthropicClient(
        base_url,
        "test-key",
        timeout=5.0,
        transport=httpx.MockTransport(handler),
    )


def test_the_request_maps_system_messages_images_and_tools() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"},
        )

    response = asyncio.run(client(handler).create(**request_kwargs()))

    assert response.text == "ok"
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "test-key"
    assert seen["headers"]["anthropic-version"] == ANTHROPIC_VERSION
    assert seen["headers"]["content-type"] == "application/json"
    payload = seen["body"]
    assert payload["model"] == "operator-model"
    assert payload["max_tokens"] == 1024
    assert payload["temperature"] == 0.3
    assert payload["system"] == [
        {"type": "text", "text": "stable prefix"},
        {"type": "text", "text": "volatile suffix", "cache_control": {"type": "ephemeral"}},
    ]
    messages = payload["messages"]
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == [
        {"type": "text", "text": "look"},
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
        },
    ]
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == [
        {"type": "text", "text": "calling"},
        {"type": "tool_use", "id": "call_1", "name": "search", "input": {"query": "x"}},
    ]
    assert messages[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "result text"},
        {"type": "text", "text": "finish"},
    ]
    assert messages[2]["role"] == "user"
    tools = payload["tools"]
    assert tools[0]["name"] == "search"
    assert tools[0]["description"] == "Search the archive."
    assert tools[0]["input_schema"] == {
        "type": "object",
        "properties": {"query": {"type": "string"}},
    }
    assert tools[0]["cache_control"] == {"type": "ephemeral"}


def test_a_native_response_normalizes_text_thinking_and_tool_calls() -> None:
    body = {
        "content": [
            {"type": "thinking", "thinking": "plan first", "signature": "sig"},
            {"type": "text", "text": "Here is the answer."},
            {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {"query": "x"}},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 12, "output_tokens": 34},
    }

    response = asyncio.run(
        client(lambda request: httpx.Response(200, json=body)).create(**request_kwargs())
    )

    assert response.text == "Here is the answer."
    assert response.thinking == "plan first"
    assert response.tool_calls == (
        ProviderToolCall(id="toolu_1", name="search", arguments='{"query": "x"}'),
    )
    assert response.usage == ProviderUsage(prompt_tokens=12, completion_tokens=34)
    assert response.finish_reason == "tool_calls"
    assert response.native_finish_reason == "tool_use"
    assert isinstance(response.message, dict)
    assert response.message["tool_calls"][0]["function"] == {
        "name": "search",
        "arguments": '{"query": "x"}',
    }
    assert response.native["stop_reason"] == "tool_use"


def test_an_http_error_is_a_provider_error_with_its_status() -> None:
    body = {"error": {"type": "invalid_request_error", "message": "bad max_tokens"}}

    with pytest.raises(ProviderError) as caught:
        asyncio.run(
            client(lambda request: httpx.Response(400, json=body)).create(**request_kwargs())
        )

    assert caught.value.status_code == 400
    assert caught.value.native == "invalid_request_error"
    assert "bad max_tokens" in str(caught.value)


def test_a_request_without_a_model_is_refused() -> None:
    with pytest.raises(ProviderError, match="no model"):
        asyncio.run(client(lambda request: httpx.Response(200, json={})).create(messages=[]))


def test_an_already_normalized_response_is_returned_unchanged() -> None:
    response = ProviderResponse(text="hi", thinking="why", finish_reason="stop")

    assert normalize_response(response) is response


def test_build_client_selects_the_anthropic_wire() -> None:
    found = build_client("anthropic", "https://api.anthropic.com/v1/", "key", timeout=3.0)

    assert isinstance(found, AnthropicClient)
    assert found.base_url == "https://api.anthropic.com/v1"


def test_a_top_level_system_string_is_hoisted_and_cached() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [], "stop_reason": "end_turn"})

    asyncio.run(
        client(handler).create(
            model="operator-model",
            system="direct system",
            messages=[{"role": "user", "content": "hi"}],
        )
    )

    assert seen["body"]["system"] == [
        {"type": "text", "text": "direct system", "cache_control": {"type": "ephemeral"}}
    ]
    assert seen["body"]["max_tokens"] == 8192
