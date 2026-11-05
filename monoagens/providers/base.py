"""One provider record: endpoint, key variable, wire, and the quirks that shape a request.

The record deliberately carries no model: a model id is an operator choice made at
`monoagens init` time, not a fact the code should pin. The quirk fields replace
per-provider branches elsewhere in the engine, so no module compares a provider name.
"""

from dataclasses import dataclass, field

WIRES = ("openai", "gemini", "anthropic")


@dataclass(frozen=True)
class ProviderDefaults:
    base_url: str
    api_key_env: str
    session_header: str = ""  # per-request session header, "" = none
    api_key_optional: bool = False  # a local endpoint accepts any non-empty key
    models_url: str = ""  # "" = {base_url}/models; set it when the list lives elsewhere
    wire: str = "openai"  # request/response protocol, never a brand
    sampling_supported: tuple[str, ...] = ("temperature", "top_p")
    safety_supported: bool = False  # request-level safety threshold


@dataclass(frozen=True)
class ProviderToolCall:
    """One normalized tool call: both wire dialects reduce to this shape."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ProviderUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class ProviderResponse:
    """The one response shape the turn loop consumes (ADR-0044 §4.2)."""

    text: str = ""
    thinking: str = ""
    tool_calls: tuple[ProviderToolCall, ...] = ()
    usage: ProviderUsage | None = None
    finish_reason: str = ""
    native_finish_reason: str = ""
    blocked: bool = False
    message: object | None = None
    native: dict = field(default_factory=dict)


def thinking_blocks(content: object) -> str:
    """Reasoning text from a content-block list: Claude `thinking`, Gemini `thought` parts.

    The blocks are data, not answer text; every wire keeps them out of `ProviderResponse.text`
    so a gateway that shows thinking never duplicates them in the reply bubble.
    """
    if not isinstance(content, list):
        return ""
    texts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") in {"thinking", "redacted_thinking"}:
            text = part.get("thinking") or part.get("text") or ""
        elif part.get("thought") is True:
            text = part.get("text") or ""
        else:
            continue
        text = str(text or "").strip()
        if text:
            texts.append(text)
    return "\n\n".join(texts)


class ProviderError(RuntimeError):
    """A native provider error with a status code the LLM loop can classify."""

    def __init__(self, message: str, *, status_code: int | None = None, native: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.native = native
