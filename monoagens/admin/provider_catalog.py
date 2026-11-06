"""Operator labels for the provider picker; IDs and auth facts stay in `monoagens.providers`."""

from urllib.parse import urlparse

from ..providers import PROVIDERS

LABELS = {
    "opencode": ("OpenCode Go", "opencode.ai/go"),
    "openai": ("OpenAI", "api.openai.com"),
    "openrouter": ("OpenRouter", "openrouter.ai"),
    "groq": ("Groq", "api.groq.com"),
    "deepinfra": ("DeepInfra", "api.deepinfra.com"),
    "deepseek": ("DeepSeek", "api.deepseek.com"),
    "ollama": ("Ollama", "local endpoint"),
    "custom": ("Custom endpoint", "OpenAI-compatible base URL"),
}


def label(provider: str) -> str:
    return LABELS.get(provider, (provider.replace("-", " ").title(), ""))[0]


def detail(provider: str) -> str:
    _name, hint = LABELS.get(provider, (provider, ""))
    defaults = PROVIDERS.get(provider)
    if defaults and not hint:
        hint = urlparse(defaults.base_url).netloc
    key = defaults.api_key_env if defaults and defaults.api_key_env else "no key"
    return f"{provider} · {hint} · {key}" if hint else f"{provider} · {key}"
