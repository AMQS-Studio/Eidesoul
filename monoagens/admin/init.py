"""`monoagens init` data layer: defaults, validation, build, and atomic host-file writes.

The interactive step machine lives in `admin/wizard.py` (ADR-0039 §4.6); this module owns only
the four files, their modes, and the one source of defaults (`templates.py`). Secrets never reach
YAML: `.env` holds the values, at `0600`.
"""


import contextlib
import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import yaml
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from ..engine import clock
from ..engine.config import Config, ConfigError
from ..engine.i18n import host_language, normalize_language
from ..providers import PROVIDERS
from . import brand, templates

PRIVATE_FILES = (".env", "roles.yaml")
HOST_FILES = ("config.yaml", "roles.yaml", "soul.yaml", ".env")
LLM_KEYS = ("provider", "base_url", "api_key_env", "model")
DEFAULT_LOG = Path.home() / ".monoagens" / "logs" / "install.log"


class InitCancelled(Exception):
    """The operator stopped the wizard; nothing new was left on disk."""


@dataclass
class Answers:
    """What the operator answered; every field lands in exactly one place."""

    location: str = ""
    provider: str = ""
    api_key: str = ""
    model: str = ""
    database_url: str = ""
    label: str = ""
    voice_on: bool = False
    voice_parts: tuple[str, ...] | None = None
    language: str = ""
    locale: str = ""
    timezone: str = ""
    websearch: str = ""
    websearch_url: str = ""
    tools: tuple[str, ...] | None = None
    gateways: tuple[str, ...] | None = None


# --- values -------------------------------------------------------------------------------


def host_locale() -> str:
    """The operator's locale from the process environment; empty when nothing states one."""
    for name in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        raw = str(os.environ.get(name) or "").strip()
        if not raw:
            continue
        text = raw.split(":")[0].split(".")[0].split("@")[0].strip().replace("_", "-")
        if text and text.lower() not in ("c", "posix"):
            return text
    return ""


def host_timezone() -> str:
    """The operator's zone: `TZ` when set, else the `/etc/localtime` target; empty otherwise."""
    stated = str(os.environ.get("TZ") or "").strip()
    if stated:
        return stated
    with contextlib.suppress(OSError):
        target = str(Path("/etc/localtime").resolve())
        marker = "zoneinfo/"
        if marker in target:
            return target.split(marker, 1)[1]
    return ""


def voice_enabled(value: str | bool | None) -> bool:
    """One master switch: `on` enables the layer, `off` (the default) disables it."""
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in ("", "off", "false", "no", "0"):
        return False
    if text in ("on", "true", "yes", "1"):
        return True
    raise ValueError("voice layer must be 'on' or 'off'")


def validate_provider(name: str) -> str | None:
    """`None` when the name is one of the providers; otherwise the operator message."""
    if name in PROVIDERS:
        return None
    return f"unknown provider '{name}'; choose one of: {', '.join(sorted(PROVIDERS))}"


def validate_database_url(url: str) -> str | None:
    """Blank is allowed (fill `.env` later); otherwise it must be a Postgres URL."""
    if not url.strip():
        return None
    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError) as exc:
        return f"not a database URL: {exc}"
    if parsed.get_backend_name() != "postgresql":
        return "the database URL must be postgresql+psycopg:// (Postgres with pgvector)"
    if not parsed.database:
        return "the database URL needs a database name"
    return None


def _probe(provider: str, entered_key: str = "") -> Config:
    """A read-only stand-in for `Config` so `admin/models.py` stays the only list fetcher."""
    defaults = PROVIDERS[provider]
    llm = SimpleNamespace(
        base_url=defaults.base_url,
        api_key_env=defaults.api_key_env,
        models_url=defaults.models_url,
        api_key_optional=defaults.api_key_optional,
        wire=defaults.wire,
        native=defaults.wire != "openai",
        models_native=defaults.wire != "openai",
    )

    def api_key() -> str:
        if entered_key.strip():
            return entered_key.strip()
        found = os.environ.get(llm.api_key_env)
        if found:
            return found
        if llm.api_key_optional:
            return "local"
        raise KeyError(llm.api_key_env)

    # The stand-in carries only `.llm` and `.api_key()`, which is all `list_models` reads.
    return cast(Config, SimpleNamespace(llm=llm, api_key=api_key))


def model_ids(provider: str, api_key: str = "") -> list[str]:
    """The provider's served model ids; the wizard's entered key wins over the environment."""
    from . import models as admin_models

    return admin_models.list_models(_probe(provider, api_key), timeout=5.0)


def validate_credential(provider: str, api_key: str = "") -> str | None:
    """Reject a key the provider answers with 401/403; other list failures are not auth errors."""
    key = api_key.strip()
    if not key or provider not in PROVIDERS:
        return None
    defaults = PROVIDERS[provider]
    if defaults.api_key_optional or not defaults.api_key_env:
        return None
    try:
        model_ids(provider, key)
    except ConfigError as exc:
        text = str(exc).casefold()
        markers = ("401", "403", "unauthorized", "forbidden", "invalid api key", "invalid_api_key")
        if any(marker in text for marker in markers):
            return f"{provider} rejected this key: {exc}"
    except (KeyError, ValueError, OSError):
        return None
    return None


def validate_model(provider: str, model: str, api_key: str = "") -> str | None:
    """A model outside the provider's reachable list; an unreachable list is not an error."""
    if not model.strip() or provider not in PROVIDERS:
        return None
    try:
        served = model_ids(provider, api_key)
    except (ConfigError, KeyError, ValueError, OSError):
        return None
    if not served or model in served:
        return None
    sample = ", ".join(served[:5])
    return f"model '{model}' is not in {provider}'s list; served includes: {sample}"


# --- the files ----------------------------------------------------------------------------


def _env_names(text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        pairs.append((name.strip(), value.strip()))
    return pairs


def _set_env(text: str, name: str, value: str) -> str:
    """Set one name in an env file, keeping its order and every other line."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.partition("=")[0].strip() == name and "=" in line:
            lines[index] = f"{name}={value}"
            return "\n".join(lines) + "\n"
    lines.append(f"{name}={value}")
    return "\n".join(lines) + "\n"


def build(  # noqa: C901, PLR0912
    *,
    provider: str,
    api_key: str = "",
    model: str = "",
    database_url: str = "",
    label: str = "",
    voice: str | bool = "off",
    language: str = "",
    locale: str = "",
    timezone: str = "",
    websearch: str = "",
    websearch_url: str = "",
    tools: tuple[str, ...] | None = None,
    gateways: tuple[str, ...] | None = None,
    voice_parts: tuple[str, ...] | None = None,
) -> dict[str, str]:
    """The four files: `templates.files()` plus the operator's answers, nothing else."""
    locale = locale or host_locale()
    language = language or (locale.split("-")[0] if locale else "") or host_language()
    language = normalize_language(language)
    locale = locale or language
    timezone = timezone or host_timezone()
    if not timezone:
        raise ValueError("a timezone is required; pass --timezone <IANA name>")
    clock.zone(timezone)
    from . import catalog

    backend_names = {item.name for item in catalog.websearch_backends()} | {"off"}
    if websearch and websearch not in backend_names:
        raise ValueError(f"unknown websearch backend '{websearch}'")
    if websearch == "searxng" and not websearch_url.strip():
        raise ValueError("searxng needs a URL")
    if tools is not None:
        unknown = set(tools).difference(catalog.tool_names())
        if unknown:
            raise ValueError(f"unknown tool(s): {', '.join(sorted(unknown))}")
    if gateways is not None:
        unknown = set(gateways).difference({item.name for item in catalog.gateways()})
        if unknown:
            raise ValueError(f"unknown gateway(s): {', '.join(sorted(unknown))}")
    if voice_parts is not None:
        unknown = set(voice_parts).difference(templates.VOICE_PARTS)
        if unknown:
            raise ValueError(f"unknown voice part(s): {', '.join(sorted(unknown))}")
    contents = templates.files(
        provider=provider,
        model=model,
        language=language,
        locale=locale,
        timezone=timezone,
        voice_enabled=voice_enabled(voice),
        voice_parts=voice_parts,
        websearch=websearch,
        websearch_url=websearch_url,
        tools=tools,
        gateways=gateways,
    )
    if label.strip():
        roles = yaml.safe_load(contents["roles.yaml"])
        roles["roles"]["administrator"]["label"] = label.strip()
        contents["roles.yaml"] = templates.dump(roles)
    if database_url.strip():
        contents[".env"] = _set_env(
            contents[".env"], "MONOAGENS_DATABASE_URL", database_url.strip()
        )
    if api_key.strip():
        if "\n" in api_key or "\r" in api_key:
            raise ValueError("the API key must be one line")
        key_env = PROVIDERS[provider].api_key_env
        if not key_env:
            raise ValueError(f"provider '{provider}' has no API key variable")
        contents[".env"] = _set_env(contents[".env"], key_env, api_key.strip())
    return contents


def mode_of(name: str) -> int:
    """The mode a host file lands with: `.env` and `roles.yaml` stay private."""
    return 0o600 if name in PRIVATE_FILES else 0o644


def _atomic_write(path: Path, text: str, mode: int) -> None:
    """Write a sibling temp file, fsync, then rename: a reader never sees a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    with contextlib.suppress(FileNotFoundError):
        temp.unlink()
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()


def _missing_chain(path: Path) -> list[Path]:
    """Directories that `mkdir -p` will create, outermost first."""
    missing: list[Path] = []
    current = path
    while not current.exists() and current != current.parent:
        missing.append(current)
        current = current.parent
    return list(reversed(missing))


def _merge(name: str, existing: str, plan: str) -> str:
    """`change`: the operator's answers land in the existing file, everything else stays."""
    if name == ".env":
        merged = existing
        for env_name, value in _env_names(plan):
            if value:
                merged = _set_env(merged, env_name, value)
        return merged
    if name == "soul.yaml":
        return plan
    try:
        old = yaml.safe_load(existing) or {}
        new = yaml.safe_load(plan) or {}
    except yaml.YAMLError:
        return plan
    if not isinstance(old, dict) or not isinstance(new, dict):
        return plan
    if name == "config.yaml":
        engine = old.setdefault("engine", {})
        llm = engine.setdefault("llm", {})
        for key in LLM_KEYS:
            if key in new["engine"]["llm"]:
                llm[key] = new["engine"]["llm"][key]
        old.setdefault("database", {})["url_env"] = new["database"]["url_env"]
        return templates.dump_config(old)
    label = ((new.get("roles") or {}).get("administrator") or {}).get("label")
    if label:
        old.setdefault("roles", {}).setdefault("administrator", {})["label"] = label
    return templates.dump(old)


def write_files(
    folder: str | Path,
    contents: dict[str, str],
    decisions: dict[str, str] | None = None,
) -> tuple[list[Path], list[Path]]:
    """Write the plan through rename; an interruption rolls the new files back."""
    base = Path(folder).expanduser().resolve()
    decided = decisions or {}
    created_dirs = _missing_chain(base)
    created_dirs += _missing_chain(base / ".monoagens")
    created_dirs += _missing_chain(base / ".monoagens" / "workspace")
    created_files: list[Path] = []
    written: list[Path] = []
    skipped: list[Path] = []
    try:
        for path in created_dirs:
            path.mkdir(parents=True, exist_ok=True)
        (base / ".monoagens" / "workspace").mkdir(parents=True, exist_ok=True)
        for name in HOST_FILES:
            path = base / name
            decision = decided.get(name, "new")
            if decision == "keep" or (decision == "new" and path.exists()):
                skipped.append(path)
                continue
            text = contents[name]
            if name == ".env" and decision == "replace" and path.exists():
                # Replacing the host files must not drop generated container credentials.
                text = _merge(name, path.read_text(encoding="utf-8"), text)
            elif decision == "change" and path.exists():
                text = _merge(name, path.read_text(encoding="utf-8"), text)
            existed = path.exists()
            _atomic_write(path, text, mode_of(name))
            if not existed:
                created_files.append(path)
            written.append(path)
    except BaseException:
        for path in created_files:
            with contextlib.suppress(OSError):
                path.unlink()
        for path in reversed(created_dirs):
            with contextlib.suppress(OSError):
                path.rmdir()
        raise
    return written, skipped


def init(
    folder: str | Path,
    *,
    force: bool = False,
    provider: str,
    api_key: str = "",
    model: str = "",
    database_url: str = "",
    label: str = "",
    voice_layer: str = "off",
    voice_parts: tuple[str, ...] | None = None,
    language: str = "",
    locale: str = "",
    timezone: str = "",
    websearch: str = "",
    websearch_url: str = "",
    tools: tuple[str, ...] | None = None,
    gateways: tuple[str, ...] | None = None,
) -> tuple[list[Path], list[Path]]:
    """Write the scaffold without prompting; return (written, skipped)."""
    error = validate_provider(provider) or validate_database_url(database_url)
    if error:
        raise ValueError(error)
    contents = build(
        provider=provider,
        api_key=api_key,
        model=model,
        database_url=database_url,
        label=label,
        voice=voice_layer,
        voice_parts=voice_parts,
        language=language,
        locale=locale,
        timezone=timezone,
        websearch=websearch,
        websearch_url=websearch_url,
        tools=tools,
        gateways=gateways,
    )
    decisions: dict[str, str] = dict.fromkeys(HOST_FILES, "replace" if force else "new")
    return write_files(folder, contents, decisions)


# --- reporting ----------------------------------------------------------------------------


def report(
    base: str | Path,
    written: list[Path],
    skipped: list[Path],
    *,
    provider: str,
    model: str = "",
    log_path: str | Path | None = None,
) -> str:
    """The receipt: files with modes, the next commands, and where the log lives."""
    lines = [f"MonoAgens scaffold: {Path(base).expanduser().resolve()}"]
    for path in written:
        lines.append(f"  wrote  {path} ({mode_of(path.name):04o})")
    for path in skipped:
        lines.append(f"  kept   {path} (already exists)")
    key_env = PROVIDERS[provider].api_key_env if provider in PROVIDERS else ""
    steps = [
        f"fill .env ({key_env}, MONOAGENS_DATABASE_URL)"
        if key_env
        else "fill .env (MONOAGENS_DATABASE_URL)"
    ]
    if not model:
        steps.append(
            "choose a model: `monoagens models` lists the ids the provider serves, then set "
            "engine.llm.model (rerun `monoagens init --model <id> --force` or edit config.yaml)"
        )
    steps.extend(
        [
            "monoagens db upgrade",
            "monoagens people add cli local --role administrator",
            "monoagens run",
        ]
    )
    lines.append("Next steps:")
    lines.extend(f"  {index}. {step}" for index, step in enumerate(steps, start=1))
    lines.append(f"Log: {Path(log_path) if log_path else DEFAULT_LOG}")
    return "\n".join(lines)


# --- the wizard ---------------------------------------------------------------------------


def banner(color: bool = False) -> list[str]:
    """The setup wordmark; the product logo remains `assets/logo.png`."""
    return brand.banner(color)


def __getattr__(name: str):
    # Keep `admin_init.wizard` working without importing the terminal layer on every host read.
    if name == "wizard":
        from .wizard import wizard

        return wizard
    raise AttributeError(name)
