"""Operator settings: one interactive menu and scriptable `get`/`set` shortcuts.

Every write keeps the operator's comments and proves the result with the real loader before
the file is replaced: a rejected value leaves `config.yaml` exactly as it was.
"""


import os
import sys
from functools import lru_cache

import yaml

from ..engine.config import ENGINE_SCHEMA, ConfigError, load_config, load_host
from . import service
from .gateway import note, render_document
from .templates import ENGINE, HOST
from .yaml_io import write_text

SHORTCUTS = {
    "model": "engine.llm.model",
    "provider": "engine.llm.provider",
    "websearch": "web_search.provider",
    "approval": "engine.approval.mode",
}
MENU = (
    ("engine.llm.provider", "LLM provider"),
    ("engine.llm.model", "LLM model"),
    ("engine.llm.base_url", "LLM base URL"),
    ("web_search.provider", "Web search provider"),
    ("engine.approval.mode", "Approval mode"),
    ("engine.logging.level", "Log level"),
    ("sandbox.runtime", "Sandbox runtime"),
)
MISSING = object()


def _flatten(body: dict, prefix: str = "") -> set[str]:
    found: set[str] = set()
    for key, value in body.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and value:
            found |= _flatten(value, path)
        else:
            found.add(path)
    return found


@lru_cache(maxsize=1)
def known_keys() -> frozenset[str]:
    engine = _flatten(ENGINE) | _flatten(ENGINE_SCHEMA)
    return frozenset(_flatten(HOST) | {f"engine.{item}" for item in engine})


def canonical(key: str) -> str:
    text = str(key or "").strip()
    if not text:
        raise ValueError("a setting key is required")
    resolved = SHORTCUTS.get(text, text)
    if resolved not in known_keys() and not resolved.startswith("gateways."):
        raise ValueError(
            f"unknown setting '{key}'; run `monoagens setting list` for the effective values"
        )
    return resolved


def _walk(body: object, key: str) -> object:
    value = body
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return MISSING
        value = value[part]
    return value


def _default(key: str) -> object:
    parts = key.split(".")
    if parts[0] == "engine":
        return _walk(ENGINE, ".".join(parts[1:]))
    return _walk(HOST, key)


def read(config_path: str | None = None) -> dict:
    _host, paths = load_host(config_path)
    try:
        body = yaml.safe_load(paths.config.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"config YAML is invalid: {exc}") from exc
    if not isinstance(body, dict):
        raise ConfigError(f"config is empty or not a mapping: {paths.config}")
    return body


def get_value(config_path: str | None, key: str) -> object:
    resolved = canonical(key)
    body = read(config_path)
    found = _walk(body, resolved)
    if found is not MISSING:
        return found
    fallback = _default(resolved)
    if fallback is MISSING:
        raise ValueError(f"setting '{resolved}' is not present in config.yaml")
    return fallback


def _parse_bool(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered in {"true", "yes", "on", "1"}:
        return True
    if lowered in {"false", "no", "off", "0"}:
        return False
    raise ValueError(f"expected true or false, got {text!r}")


def coerce(key: str, text: str) -> object:
    """A value in the type the generated default already uses for this key."""
    default = _default(canonical(key))
    raw = str(text).strip()
    if isinstance(default, bool):
        return _parse_bool(raw)
    if isinstance(default, int) and not isinstance(default, bool):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, (list, tuple)):
        parsed = yaml.safe_load(raw)
        if isinstance(parsed, str):
            parsed = [item.strip() for item in parsed.split(",") if item.strip()]
        if not isinstance(parsed, list):
            raise ValueError(f"expected a list, got {text!r}")
        return parsed
    return raw


def _checked_write(config_path: str | None, updates: dict) -> None:
    """Render every change in one pass, prove it, and only then replace the file."""
    _host, paths = load_host(config_path)
    target = paths.config
    text = target.read_text(encoding="utf-8")
    comments = {key: note(key, value) for key, value in updates.items()}
    changed = render_document(text, updates, comments=comments)
    _prove(target, changed)
    write_text(target, changed)


def _prove(target, changed: str) -> None:
    """Load the rendered text through the real loader in the same directory."""
    probe = target.with_name(f".{target.name}.{os.getpid()}.probe")
    probe.write_text(changed, encoding="utf-8")
    try:
        load_config(probe, require_key=False, require_model=False)
    finally:
        probe.unlink(missing_ok=True)


def set_value(config_path: str | None, key: str, raw: str) -> object:
    resolved = canonical(key)
    value = coerce(resolved, raw)
    _checked_write(config_path, {resolved: value})
    print(f"{resolved}: {value}", flush=True)
    service.notify(config_path, reason=f"setting {resolved}")
    return value


def list_values(config_path: str | None = None) -> list[tuple[str, str]]:
    _host, paths = load_host(config_path)
    config = load_config(paths.config, require_key=False, require_model=False)
    return [
        ("provider", config.llm.provider or "-"),
        ("model", config.llm.model or "-"),
        ("base_url", config.llm.base_url or "-"),
        ("web_search", config.web_search.provider),
        ("approval", config.approval.mode),
        ("log_level", config.logging.level),
        ("sandbox", config.sandbox.runtime or "autodetect"),
        (
            "gateways",
            ", ".join(name for name, item in config.gateways.items() if item.enabled) or "-",
        ),
        (
            "plugins",
            ", ".join(name for name, item in config.plugins.items() if item.enabled) or "-",
        ),
    ]


def show(config_path: str | None = None) -> int:
    for key, value in list_values(config_path):
        print(f"{key}: {value}", flush=True)
    return 0


def get(config_path: str | None, key: str) -> int:
    value = get_value(config_path, key)
    print(value if not isinstance(value, (dict, list, tuple)) else yaml.safe_dump(value).strip())
    return 0


def _ask(stream_in, stream_out, prompt: str, default: str = "") -> str:
    stream_out.write(prompt)
    if default:
        stream_out.write(f" [{default}]")
    stream_out.write(": ")
    stream_out.flush()
    line = stream_in.readline()
    if not line:
        raise KeyboardInterrupt
    text = line.strip()
    return text or default


def menu(config_path: str | None = None, *, stdin=None, stdout=None) -> int:
    """Numbered menu; an empty answer keeps the current value, `q` leaves."""
    stream_in = stdin or sys.stdin
    stream_out = stdout or sys.stdout
    while True:
        stream_out.write("\nSettings\n")
        for index, (key, label) in enumerate(MENU, start=1):
            try:
                value = get_value(config_path, key)
            except (ConfigError, ValueError):
                value = "-"
            stream_out.write(f"  {index}) {label:<22} {value}\n")
        stream_out.write("  0) Done\n")
        try:
            answer = _ask(stream_in, stream_out, "Choose")
        except KeyboardInterrupt:
            stream_out.write("\n")
            return 0
        if answer in {"0", "q", "quit", "exit"}:
            return 0
        if not answer.isdigit() or not 1 <= int(answer) <= len(MENU):
            stream_out.write("choose a number from the list\n")
            continue
        key, label = MENU[int(answer) - 1]
        try:
            current = str(get_value(config_path, key))
            raw = _ask(stream_in, stream_out, f"{label}")
        except KeyboardInterrupt:
            stream_out.write("\n")
            return 0
        if raw == current or raw == "":
            continue
        try:
            set_value(config_path, key, raw)
        except (ConfigError, ValueError) as exc:
            stream_out.write(f"not changed: {exc}\n")


def provider(config_path: str | None, name: str) -> int:
    from ..providers import PROVIDERS

    chosen = str(name or "").strip().lower()
    if chosen not in PROVIDERS:
        raise ValueError(
            f"unknown provider '{name}'; choose one of: {', '.join(sorted(PROVIDERS))}"
        )
    defaults = PROVIDERS[chosen]
    body = read(config_path)
    previous = str((body.get("engine") or {}).get("llm", {}).get("provider") or "").strip()
    current_base = str((body.get("engine") or {}).get("llm", {}).get("base_url") or "").strip()
    updates = {
        "engine.llm.provider": chosen,
        "engine.llm.api_key_env": defaults.api_key_env,
    }
    old_default = PROVIDERS[previous].base_url if previous in PROVIDERS else ""
    if not current_base or current_base == old_default:
        updates["engine.llm.base_url"] = defaults.base_url
    _checked_write(config_path, updates)
    print(f"provider: {chosen} (key variable {defaults.api_key_env})", flush=True)
    service.notify(config_path, reason="setting provider")
    return 0


def model(config_path: str | None, model_id: str) -> int:
    chosen = str(model_id or "").strip()
    if not chosen:
        raise ValueError("a model id is required")
    set_value(config_path, "engine.llm.model", chosen)
    return 0


def websearch(config_path: str | None, name: str) -> int:
    from ..engine.config import WEB_SEARCH_PROVIDERS

    chosen = str(name or "").strip().lower()
    if chosen not in WEB_SEARCH_PROVIDERS:
        raise ValueError(
            f"unknown web search provider '{name}'; choose one of: "
            f"{', '.join(sorted(WEB_SEARCH_PROVIDERS))}"
        )
    host, _paths = load_host(config_path)
    if chosen == "searxng" and not host.web_search.base_url:
        raise ValueError("searxng needs web_search.base_url in config.yaml")
    research = "off" if chosen == "off" else "auto"
    _checked_write(
        config_path,
        {"web_search.provider": chosen, "engine.research.mode": research},
    )
    print(f"web_search: {chosen}", flush=True)
    service.notify(config_path, reason="setting websearch")
    return 0
