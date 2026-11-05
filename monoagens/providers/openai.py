"""OpenAI wire: `POST {base_url}/chat/completions` with `Authorization: Bearer $api_key_env`.

Tools travel as `{"type": "function", "function": {...}}` and results come back as
`{"role": "tool", "tool_call_id": ...}`. Every entry here is a base URL that speaks that
shape and nothing else: no model id is pinned, the operator chooses one at init time.
"""

from collections.abc import Callable

from .base import (
    ProviderDefaults,
    ProviderResponse,
    ProviderToolCall,
    ProviderUsage,
    thinking_blocks,
)

OPENCODE = ProviderDefaults(
    "https://opencode.ai/inference/go/openai/v1",
    "OPENCODE_GO_API_KEY",
    session_header="x-opencode-session",
    models_url="https://opencode.ai/inference/v1/models",
)
OPENAI = ProviderDefaults("https://api.openai.com/v1", "OPENAI_API_KEY")
OPENROUTER = ProviderDefaults("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY")
GROQ = ProviderDefaults("https://api.groq.com/openai/v1", "GROQ_API_KEY")
DEEPSEEK = ProviderDefaults("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY")
OLLAMA = ProviderDefaults("http://127.0.0.1:11434/v1", "OLLAMA_API_KEY", api_key_optional=True)
CUSTOM = ProviderDefaults("", "")


def normalize_response(response: object) -> ProviderResponse:
    """OpenAI SDK/fake response -> the one shape the loop consumes (ADR-0044 §4.2).

    A native adapter already answers in that shape; it is returned unchanged instead of being
    read as an empty OpenAI response.
    """
    if isinstance(response, ProviderResponse):
        return response
    choices = getattr(response, "choices", None) or []
    message = choices[0].message if choices else None
    content = _field(message, "content")
    text = _content_text(content)
    thinking = str(_field(message, "reasoning_content") or "").strip()
    if not thinking:
        # Claude's compatibility layer and native thinking blocks travel as content parts.
        thinking = thinking_blocks(content)
    calls: list[ProviderToolCall] = []
    for call in getattr(message, "tool_calls", None) or []:
        call_id, name, arguments = call_parts(call)
        calls.append(ProviderToolCall(id=call_id, name=name, arguments=arguments))
    usage = getattr(response, "usage", None)
    if usage is not None:
        usage = ProviderUsage(
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )
    finish = str(getattr(choices[0], "finish_reason", "") or "") if choices else ""
    return ProviderResponse(
        text=text,
        thinking=thinking,
        tool_calls=tuple(calls),
        usage=usage,
        finish_reason=finish,
        native_finish_reason=finish,
        message=message,
    )


def _field(message: object, name: str):
    """One message attribute from an SDK object, a stub, or a plain mapping."""
    if isinstance(message, dict):
        return message.get(name)
    return getattr(message, name, None)


def _content_text(content: object) -> str:
    """Answer text only; reasoning blocks never leak into the reply."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    texts = [
        str(part.get("text") or "")
        for part in content
        if isinstance(part, dict)
        and part.get("type", "text") == "text"
        and part.get("thought") is not True
    ]
    return "\n".join(texts).strip()


def call_parts(call: object) -> tuple[str, str, str]:
    """Tool call id/name/arguments from the SDK object or a plain mapping."""
    function = getattr(call, "function", None)
    if function is None and isinstance(call, dict):
        function = call.get("function") or {}
    name = str(
        getattr(function, "name", "")
        or (function.get("name") if isinstance(function, dict) else "")
        or ""
    )
    arguments = str(
        getattr(function, "arguments", "")
        or (function.get("arguments") if isinstance(function, dict) else "")
        or "{}"
    )
    return str(getattr(call, "id", "") or ""), name, arguments


def message_dict(message: object) -> dict:
    """Assistant message as a plain dict; a stub, pydantic model, or normalized mapping."""
    if isinstance(message, dict):
        return dict(message)
    dump: Callable[..., dict] | None = getattr(message, "model_dump", None)
    if callable(dump):
        return dump(exclude_none=True)
    body: dict = {
        "role": getattr(message, "role", "assistant") or "assistant",
        "content": getattr(message, "content", None),
    }
    calls = getattr(message, "tool_calls", None) or []
    if calls:
        body["tool_calls"] = [
            {
                "id": getattr(call, "id", ""),
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in calls
        ]
    return {key: value for key, value in body.items() if value is not None}
