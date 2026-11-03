"""AI providers: OpenAI-compatible endpoints selectable by name.

This package holds client/auth facts only: endpoint, key variable, and quirks. It never
imports engine modules, so `eidesoul.engine` may import it while gateways stay out of the
core entirely. It carries no model id; the operator picks one at `eidesoul init` time and
`eidesoul models` lists what a provider actually serves.

One module per endpoint format: `openai.py`, `gemini.py`, `anthropic.py`. Every provider
here speaks the OpenAI wire protocol; a different protocol needs its own adapter module plus
a seam in `eidesoul/engine/llm.py`, never a data entry.
"""


from .anthropic import ANTHROPIC
from .base import ProviderDefaults
from .gemini import GOOGLE
from .openai import CUSTOM, DEEPSEEK, GROQ, OLLAMA, OPENAI, OPENCODE, OPENROUTER

PROVIDERS: dict[str, ProviderDefaults] = {
    "opencode": OPENCODE,
    "openai": OPENAI,
    "google": GOOGLE,
    "anthropic": ANTHROPIC,
    "openrouter": OPENROUTER,
    "groq": GROQ,
    "deepseek": DEEPSEEK,
    "ollama": OLLAMA,
    "custom": CUSTOM,
}

__all__ = ["PROVIDERS", "ProviderDefaults", "resolve"]


def resolve(provider: str, base_url: str, api_key_env: str) -> tuple[str, ProviderDefaults]:
    """Effective `(name, defaults)`; explicit endpoint values win, blanks come from the provider.

    Quirks (`session_header`, `api_key_optional`) always come from the provider record. The
    model is never filled here: it belongs to the operator's config.
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
    )
    if not found.base_url:
        raise ValueError(f"llm.provider={name} requires llm.base_url")
    return name, found
