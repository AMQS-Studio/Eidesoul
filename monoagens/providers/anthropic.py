"""Claude on Anthropic's native `/v1/messages` wire (ADR-0044 §4.4).

The compatibility layer is test-oriented and loses the features Claude is chosen for: content
blocks (image, `thinking`, `tool_use`, `tool_result`), prompt caching (`cache_control: ephemeral`
on the prefix), and the native `stop_reason`/`usage` fields. This adapter keeps the engine's
OpenAI-shaped request and maps it here, so no other module learns Anthropic's vocabulary.
"""


import json
from collections.abc import Mapping
from typing import Any

import httpx

from .base import (
    ProviderDefaults,
    ProviderError,
    ProviderResponse,
    ProviderToolCall,
    ProviderUsage,
    thinking_blocks,
)

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 8192
CACHE_CONTROL = {"type": "ephemeral"}
FINISH = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "pause_turn": "pause",
    "refusal": "refusal",
}
EMPTY_SCHEMA = {"type": "object", "properties": {}}

ANTHROPIC = ProviderDefaults(
    "https://api.anthropic.com/v1/",
    "ANTHROPIC_API_KEY",
    wire="anthropic",
)


def _image(part: Mapping[str, Any]) -> dict | None:
    """OpenAI image part -> Anthropic image block: base64 data URLs or a remote URL."""
    raw_image = part.get("image_url")
    image = raw_image if isinstance(raw_image, Mapping) else {}
    url = str(image.get("url") or (raw_image if isinstance(raw_image, str) else "") or "")
    if url.startswith("data:") and "," in url:
        meta, data = url.split(",", 1)
        mime = meta[5:].split(";", 1)[0] or "image/png"
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": mime, "data": data},
        }
    if url.startswith(("http://", "https://")):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return None


def _blocks(content: object) -> list[dict]:
    """OpenAI content (string or parts) -> Anthropic content blocks."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, list):
        return []
    found: list[dict] = []
    for part in content:
        if not isinstance(part, Mapping):
            continue
        kind = part.get("type")
        if kind in {"text", "input_text"}:
            text = str(part.get("text") or "")
            if text:
                found.append({"type": "text", "text": text})
        elif kind in {"image_url", "input_image"}:
            block = _image(part)
            if block is not None:
                found.append(block)
    return found


def _tool_input(raw: object) -> dict:
    """A tool call's arguments as an object; an unparseable string becomes empty."""
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _tool_use(call: Mapping[str, Any]) -> dict:
    raw_function = call.get("function")
    function = raw_function if isinstance(raw_function, Mapping) else {}
    return {
        "type": "tool_use",
        "id": str(call.get("id") or ""),
        "name": str(function.get("name") or ""),
        "input": _tool_input(function.get("arguments")),
    }


def _messages(messages: list[Mapping[str, Any]]) -> list[dict]:
    """OpenAI messages -> Anthropic messages.

    System messages are hoisted out by `_system`. Tool results travel as `user` blocks with
    `tool_result`, and adjacent messages of the same role are merged because Anthropic requires
    alternating turns and a tool result must answer the immediately preceding `tool_use`.
    """
    found: list[dict] = []

    def append(role: str, blocks: list[dict]) -> None:
        if not blocks:
            return
        if found and found[-1]["role"] == role:
            found[-1]["content"].extend(blocks)
        else:
            found.append({"role": role, "content": list(blocks)})

    for message in messages:
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role") or "user")
        if role == "system":
            continue
        if role == "tool":
            append(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": str(message.get("tool_call_id") or ""),
                        "content": str(message.get("content") or ""),
                    }
                ],
            )
            continue
        blocks = _blocks(message.get("content"))
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                if isinstance(call, Mapping):
                    blocks.append(_tool_use(call))
            append("assistant", blocks)
        else:
            append("user", blocks)
    return found


def _system(messages: list[Mapping[str, Any]], direct: object = None) -> list[dict] | None:
    """System text as cache-marked blocks; the breakpoint sits on the prefix's last block."""
    texts = [
        str(message.get("content") or "")
        for message in messages
        if str(message.get("role") or "") == "system"
        and str(message.get("content") or "").strip()
    ]
    extra = str(direct or "").strip()
    if extra:
        texts.insert(0, extra)
    if not texts:
        return None
    blocks: list[dict[str, Any]] = [{"type": "text", "text": text} for text in texts]
    blocks[-1]["cache_control"] = dict(CACHE_CONTROL)
    return blocks


def _tools(tools: object) -> list[dict] | None:
    """OpenAI function tools -> Anthropic tools, with the cache breakpoint on the last one."""
    if not isinstance(tools, list) or not tools:
        return None
    declarations: list[dict[str, Any]] = []
    for item in tools:
        if not isinstance(item, Mapping):
            continue
        raw_function = item.get("function")
        function = raw_function if isinstance(raw_function, Mapping) else item
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        declarations.append(
            {
                "name": name,
                "description": str(function.get("description") or ""),
                "input_schema": function.get("parameters") or dict(EMPTY_SCHEMA),
            }
        )
    if not declarations:
        return None
    declarations[-1]["cache_control"] = dict(CACHE_CONTROL)
    return declarations


def _max_tokens(value: object) -> int:
    if value is None:
        return DEFAULT_MAX_TOKENS
    try:
        found = int(str(value))
    except (TypeError, ValueError):
        return DEFAULT_MAX_TOKENS
    return max(1, found)


def _payload(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """One `/v1/messages` request body from the engine's OpenAI-shaped call."""
    model = str(kwargs.get("model") or "").strip()
    if not model:
        raise ProviderError("Anthropic request has no model")
    raw_messages = kwargs.get("messages")
    messages = raw_messages if isinstance(raw_messages, list) else []
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": _max_tokens(kwargs.get("max_tokens")),
        "messages": _messages(messages),
    }
    system = _system(messages, kwargs.get("system"))
    if system:
        payload["system"] = system
    for key in ("temperature", "top_p", "top_k"):
        if kwargs.get(key) is not None:
            payload[key] = kwargs[key]
    tools = _tools(kwargs.get("tools"))
    if tools:
        payload["tools"] = tools
    thinking = kwargs.get("thinking")
    if isinstance(thinking, Mapping):
        payload["thinking"] = dict(thinking)
    return payload


def _response(body: Mapping[str, Any]) -> ProviderResponse:
    """Native response -> the one shape the turn loop consumes."""
    raw = body.get("content")
    blocks = raw if isinstance(raw, list) else []
    texts: list[str] = []
    calls: list[ProviderToolCall] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        kind = block.get("type")
        if kind == "text":
            text = str(block.get("text") or "")
            if text.strip():
                texts.append(text)
        elif kind == "tool_use":
            calls.append(
                ProviderToolCall(
                    id=str(block.get("id") or ""),
                    name=str(block.get("name") or ""),
                    arguments=json.dumps(block.get("input") or {}, ensure_ascii=False),
                )
            )
    stop = str(body.get("stop_reason") or "")
    raw_usage = body.get("usage")
    usage = None
    if isinstance(raw_usage, Mapping):
        usage = ProviderUsage(
            prompt_tokens=raw_usage.get("input_tokens"),
            completion_tokens=raw_usage.get("output_tokens"),
        )
    text = "\n".join(texts).strip()
    message: dict[str, Any] = {"role": "assistant"}
    if text:
        message["content"] = text
    if calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in calls
        ]
    return ProviderResponse(
        text=text,
        thinking=thinking_blocks(blocks),
        tool_calls=tuple(calls),
        usage=usage,
        finish_reason=FINISH.get(stop, stop),
        native_finish_reason=stop,
        blocked=stop == "refusal",
        message=message,
        native=dict(body),
    )


class AnthropicClient:
    """Non-streaming native `/v1/messages` adapter; retries stay in the LLM loop."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = float(timeout)
        self.transport = transport

    def headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    async def create(self, **kwargs: Any) -> ProviderResponse:
        payload = _payload(kwargs)
        url = f"{self.base_url}/messages"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport
            ) as client:
                response = await client.post(url, json=payload, headers=self.headers())
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Anthropic timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Anthropic request failed: {exc}") from exc
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            error = body.get("error") if isinstance(body, dict) else {}
            if not isinstance(error, Mapping):
                error = {}
            message = str(error.get("message") or response.text)[:500]
            raise ProviderError(
                f"Anthropic {response.status_code}: {message}",
                status_code=response.status_code,
                native=str(error.get("type") or ""),
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError("Anthropic returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise ProviderError("Anthropic returned an invalid response")
        return _response(body)
