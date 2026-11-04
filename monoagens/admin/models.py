"""Ask a provider which model ids it serves; the operator picks one, the code does not."""


import json
import urllib.error
import urllib.request

from ..engine.config import Config, ConfigError
from ..engine.llm import USER_AGENT


def models_url(config: Config) -> str:
    """The provider's model-list URL; the chat base URL only supplies a default."""
    return config.llm.models_url or f"{config.llm.base_url.rstrip('/')}/models"


def list_models(config: Config, *, timeout: float = 10.0) -> list[str]:
    """Sorted model ids from the provider's list URL; ConfigError carries the operator message."""
    llm = config.llm
    url = models_url(config)
    try:
        key = config.api_key()
    except KeyError as exc:
        raise ConfigError(f"env {llm.api_key_env} is not set (put it in .env)") from exc
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if getattr(llm, "models_native", False):
        headers["x-goog-api-key"] = key
    else:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        raise ConfigError(
            f"cannot list models at {url}: {exc}; set engine.llm.model from the provider's "
            "docs if it does not expose a model list"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot list models at {url}: {exc}") from exc
    if not isinstance(body, dict):
        return []
    if getattr(llm, "models_native", False):
        rows = body.get("models")
        ids = set()
        for item in rows or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                ids.add(name.rsplit("/", 1)[-1])
        return sorted(ids)
    data = body.get("data")
    ids = {str(item.get("id")) for item in data or [] if isinstance(item, dict) and item.get("id")}
    return sorted(ids)
