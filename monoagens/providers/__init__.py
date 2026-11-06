"""AI providers: endpoints selectable by name, grouped by wire protocol.

This package holds client/auth facts only: endpoint, key variable, wire, and quirks. It never
imports engine modules, so `monoagens.engine` may import it while gateways stay out of the core
entirely. It carries no model id; the operator picks one at `monoagens init` time and
`monoagens models` lists what a provider actually serves.

One module per endpoint format: `openai.py`, `gemini.py`, `anthropic.py`. A provider that speaks
the OpenAI wire stays an entry; a different protocol is selected by `wire` and handled by the
matching adapter in `engine/llm.py`, never by comparing a provider brand.
"""

from .anthropic import ANTHROPIC, AnthropicClient
from .base import (
    WIRES,
    ProviderDefaults,
    ProviderError,
    ProviderResponse,
    ProviderToolCall,
    ProviderUsage,
)
from .gemini import GOOGLE, GeminiClient
from .openai import (
    CUSTOM,
    DEEPINFRA,
    DEEPSEEK,
    GROQ,
    OLLAMA,
    OPENAI,
    OPENCODE,
    OPENROUTER,
    call_parts,
    message_dict,
    normalize_response,
)
from .speech import (
    REALTIME_PROVIDERS,
    STT_PROVIDERS,
    TTS_PROVIDERS,
    RealtimeVoiceSession,
    SpeechTarget,
    STTProvider,
    TTSProvider,
    build_realtime,
    build_stt,
    build_tts,
)

PROVIDERS: dict[str, ProviderDefaults] = {
    "opencode": OPENCODE,
    "openai": OPENAI,
    "google": GOOGLE,
    "anthropic": ANTHROPIC,
    "openrouter": OPENROUTER,
    "groq": GROQ,
    "deepinfra": DEEPINFRA,
    "deepseek": DEEPSEEK,
    "ollama": OLLAMA,
    "custom": CUSTOM,
}

def build_client(wire: str, base_url: str, api_key: str, *, timeout: float):
    """Adapter selected by the wire name; the engine never imports a brand module."""
    if wire == "gemini":
        return GeminiClient(base_url, api_key, timeout=timeout)
    if wire == "anthropic":
        return AnthropicClient(base_url, api_key, timeout=timeout)
    raise ValueError(f"no native client for wire '{wire}'")


__all__ = [
    "PROVIDERS",
    "REALTIME_PROVIDERS",
    "STT_PROVIDERS",
    "TTS_PROVIDERS",
    "WIRES",
    "AnthropicClient",
    "GeminiClient",
    "ProviderDefaults",
    "ProviderError",
    "ProviderResponse",
    "ProviderToolCall",
    "ProviderUsage",
    "RealtimeVoiceSession",
    "STTProvider",
    "SpeechTarget",
    "TTSProvider",
    "build_realtime",
    "build_stt",
    "build_tts",
    "call_parts",
    "message_dict",
    "normalize_response",
    "resolve",
]


def resolve(provider: str, base_url: str, api_key_env: str) -> tuple[str, ProviderDefaults]:
    """Effective `(name, defaults)`; explicit endpoint values win, blanks come from the provider.

    Quirks, wire, and capability facts always come from the provider record. The model is never
    filled here: it belongs to the operator's config.
    """
    name = str(provider or "").strip().lower()
    if name not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"unknown provider '{name}'; available: {known}")
    defaults = PROVIDERS[name]
    found = ProviderDefaults(
        str(base_url or "").strip() or defaults.base_url,
        str(api_key_env or "").strip() or defaults.api_key_env,
        defaults.session_header,
        defaults.api_key_optional,
        defaults.models_url,
        defaults.wire,
        defaults.sampling_supported,
        defaults.safety_supported,
    )
    if not found.base_url:
        raise ValueError(f"llm.provider={name} requires llm.base_url")
    return name, found
