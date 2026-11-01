"""AI providers: OpenAI-compatible endpoints selectable by name.

This package holds client/auth/default-model facts only. It never imports engine modules,
so `eidesoul.engine` may import it while gateways stay out of the core entirely. Every
provider here speaks the OpenAI wire protocol; a different protocol needs its own module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderDefaults:
    base_url: str
    api_key_env: str
    model: str


PROVIDERS: dict[str, ProviderDefaults] = {
    "opencode": ProviderDefaults(
        "https://opencode.ai/inference/go/openai/v1", "OPENCODE_GO_API_KEY", "deepseek-v4.1-flash"
    ),
    "openai": ProviderDefaults("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4.1-mini"),
    "google": ProviderDefaults(
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY",
        "gemini-2.0-flash",
    ),
    "openrouter": ProviderDefaults(
        "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "openai/gpt-4.1-mini"
    ),
    "groq": ProviderDefaults(
        "https://api.groq.com/openai/v1", "GROQ_API_KEY", "llama-3.3-70b-versatile"
    ),
    "deepseek": ProviderDefaults(
        "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "deepseek-chat"
    ),
    "ollama": ProviderDefaults("http://127.0.0.1:11434/v1", "OLLAMA_API_KEY", "qwen2.5:7b"),
    "custom": ProviderDefaults("", "", ""),
}

__all__ = ["PROVIDERS", "ProviderDefaults", "resolve"]


def resolve(
    provider: str, base_url: str, api_key_env: str, model: str
) -> tuple[str, str, str, str]:
    """Effective `(provider, base_url, api_key_env, model)`; explicit values win."""
    name = str(provider or "").strip().lower()
    if name not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"unknown provider '{name}'; available: {known}")
    defaults = PROVIDERS[name]
    found = (
        name,
        str(base_url or "").strip() or defaults.base_url,
        str(api_key_env or "").strip() or defaults.api_key_env,
        str(model or "").strip() or defaults.model,
    )
    if not found[1]:
        raise ValueError(f"llm.provider={name} requires llm.base_url")
    return found
