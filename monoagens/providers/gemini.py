"""Gemini native `generateContent` wire, not the OpenAI compatibility shim.

The adapter can carry `safetySettings`, `topK`, and native block reasons. Gemini category names
stay inside the adapter, never in engine code (ADR-0044 §4.3).
"""

import json
from typing import Any

import httpx

from .base import ProviderDefaults, ProviderError, ProviderResponse, ProviderToolCall, ProviderUsage

GOOGLE = ProviderDefaults(
    "https://generativelanguage.googleapis.com/v1beta",
    "GEMINI_API_KEY",
    models_url="https://generativelanguage.googleapis.com/v1beta/models",
    wire="gemini",
    sampling_supported=("temperature", "top_p", "top_k"),
    safety_supported=True,
)

SAFETY_CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
)
SAFETY_OFF = "OFF"


def _parts(content: Any) -> list[dict]:
    """OpenAI content -> Gemini parts; data URLs become inline_data."""
    if isinstance(content, str):
        return [{"text": content}] if content else []
    parts: list[dict] = []
    for part in content or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            text = str(part.get("text") or "")
            if text:
                parts.append({"text": text})
        elif part.get("type") == "image_url":
            image = part.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            url = str(url or "")
            if url.startswith("data:") and "," in url:
                meta, data = url.split(",", 1)
                mime = meta[5:].split(";", 1)[0] or "image/png"
                parts.append({"inline_data": {"mime_type": mime, "data": data}})
    return parts


def _tool_name_map(messages: list[dict]) -> dict[str, str]:
    """Assistant tool_call id -> function name, needed by Gemini functionResponse."""
    found: dict[str, str] = {}
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            raw_function = call.get("function")
            function = raw_function if isinstance(raw_function, dict) else {}
            found[str(call.get("id") or "")] = str(function.get("name") or "")
    return found


def _contents(messages: list[dict]) -> list[dict]:
    names = _tool_name_map(messages)
    contents: list[dict] = []
    for message in messages:
        role = str(message.get("role") or "user")
        if role == "system":
            continue
        if role == "tool":
            name = names.get(str(message.get("tool_call_id") or ""), "")
            if not name:
                name = "tool"
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": name,
                                "response": {"result": str(message.get("content") or "")},
                            }
                        }
                    ],
                }
            )
            continue
        parts = _parts(message.get("content"))
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            raw_function = call.get("function")
            function = raw_function if isinstance(raw_function, dict) else {}
            raw = function.get("arguments")
            try:
                args = json.loads(raw) if isinstance(raw, str) and raw else raw
            except json.JSONDecodeError:
                args = {}
            parts.append(
                {
                    "functionCall": {
                        "name": str(function.get("name") or ""),
                        "args": args if isinstance(args, dict) else {},
                    }
                }
            )
        if parts:
            contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})
    return contents


def _system(messages: list[dict]) -> str:
    return "\n\n".join(
        str(message.get("content") or "")
        for message in messages
        if message.get("role") == "system" and str(message.get("content") or "").strip()
    )


def _tools(tools: object) -> list[dict] | None:
    if not isinstance(tools, list) or not tools:
        return None
    declarations: list[dict] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        raw_function = item.get("function")
        function = raw_function if isinstance(raw_function, dict) else item
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        declaration = {
            "name": name,
            "description": str(function.get("description") or ""),
            "parameters": function.get("parameters") or {"type": "object", "properties": {}},
        }
        declarations.append(declaration)
    return [{"functionDeclarations": declarations}] if declarations else None


def _generation(kwargs: dict) -> dict:
    config: dict[str, Any] = {}
    if "temperature" in kwargs:
        config["temperature"] = kwargs["temperature"]
    if kwargs.get("top_p") is not None:
        config["topP"] = kwargs["top_p"]
    if kwargs.get("top_k") is not None:
        config["topK"] = kwargs["top_k"]
    if kwargs.get("max_tokens") is not None:
        config["maxOutputTokens"] = kwargs["max_tokens"]
    return config


def _usage(body: dict) -> ProviderUsage | None:
    meta = body.get("usageMetadata")
    if not isinstance(meta, dict):
        return None
    return ProviderUsage(
        prompt_tokens=meta.get("promptTokenCount"),
        completion_tokens=meta.get("candidatesTokenCount"),
    )


def _response(body: dict) -> ProviderResponse:
    candidates = body.get("candidates") or []
    candidate = candidates[0] if isinstance(candidates, list) and candidates else {}
    if not isinstance(candidate, dict):
        candidate = {}
    raw_content = candidate.get("content")
    content = raw_content if isinstance(raw_content, dict) else {}
    raw_parts = content.get("parts")
    parts = raw_parts if isinstance(raw_parts, list) else []
    text_parts: list[str] = []
    thought_parts: list[str] = []
    calls: list[ProviderToolCall] = []
    for index, part in enumerate(parts):
        if not isinstance(part, dict):
            continue
        if isinstance(part.get("text"), str):
            if part.get("thought") is True:
                thought_parts.append(part["text"])
            else:
                text_parts.append(part["text"])
        function = part.get("functionCall")
        if isinstance(function, dict):
            calls.append(
                ProviderToolCall(
                    id=f"gemini-{index}",
                    name=str(function.get("name") or ""),
                    arguments=json.dumps(function.get("args") or {}, ensure_ascii=False),
                )
            )
    finish = str(candidate.get("finishReason") or "")
    raw_block = body.get("promptFeedback")
    block = raw_block if isinstance(raw_block, dict) else {}
    block_reason = str(block.get("blockReason") or "")
    blocked = bool(block_reason) or finish in {
        "SAFETY",
        "RECITATION",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
    }
    message = {
        "role": "assistant",
        "content": "".join(text_parts),
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in calls
        ],
    }
    normalized = "stop" if finish == "STOP" else finish.lower()
    if not normalized and blocked:
        normalized = "blocked"
    return ProviderResponse(
        text="".join(text_parts).strip(),
        thinking="\n\n".join(part.strip() for part in thought_parts if part.strip()),
        tool_calls=tuple(calls),
        usage=_usage(body),
        finish_reason=normalized,
        native_finish_reason=finish or block_reason,
        blocked=blocked,
        message={key: value for key, value in message.items() if value},
        native=body,
    )


class GeminiClient:
    """Non-streaming native `generateContent` adapter; retries stay in the LLM loop."""

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = float(timeout)

    async def create(self, **kwargs: dict) -> ProviderResponse:
        model = str(kwargs.get("model") or "").strip()
        if not model:
            raise ProviderError("Gemini request has no model")
        raw_messages = kwargs.get("messages")
        messages = raw_messages if isinstance(raw_messages, list) else []
        payload: dict[str, Any] = {
            "contents": _contents(messages),
            "generationConfig": _generation(kwargs),
        }
        system = _system(messages)
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        tools = _tools(kwargs.get("tools"))
        if tools:
            payload["tools"] = tools
        if kwargs.get("safety") == "off":
            payload["safetySettings"] = [
                {"category": category, "threshold": SAFETY_OFF}
                for category in SAFETY_CATEGORIES
            ]
        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": "monoagens",
        }
        url = f"{self.base_url}/models/{model}:generateContent"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Gemini timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Gemini request failed: {exc}") from exc
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            error = body.get("error") if isinstance(body, dict) else {}
            code = str(error.get("code") or "") if isinstance(error, dict) else ""
            message = (
                str(error.get("message") or response.text)[:500]
                if isinstance(error, dict)
                else response.text[:500]
            )
            raise ProviderError(
                f"Gemini {response.status_code}: {message}",
                status_code=response.status_code,
                native=code,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError("Gemini returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise ProviderError("Gemini returned an invalid response")
        return _response(body)
