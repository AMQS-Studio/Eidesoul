"""Section builders: YAML mapping in, validated dataclass out."""

import logging
from collections.abc import Mapping
from pathlib import Path

import yaml

from .models import (
    WEB_SEARCH_PROVIDERS,
    DatabaseConfig,
    FetchConfig,
    GrepConfig,
    MigrationsConfig,
    ReadConfig,
    SandboxConfig,
    SearchConfig,
    ShellConfig,
    ToolsConfig,
    WebSearchConfig,
)
from .schema import ConfigError
from .settings import (
    IntentConfig,
    MCPServerConfig,
    PluginConfig,
    PluginSettings,
    RetentionConfig,
    SelfKnowledgeConfig,
)

log = logging.getLogger(__name__)

def _expand(value: str, base: Path | None = None) -> Path:
    """Relative paths resolve from the config folder, not the CWD; `~` stays absolute."""
    path = Path(value).expanduser()
    return path if path.is_absolute() or base is None else base / path

def _str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, (str, int)) and str(item))

def _bool(value: object, default: bool) -> bool:
    """A YAML string `"false"` must be false, not truthy like `bool("false")`."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "yes", "1", "on"}:
        return True
    if text in {"false", "no", "0", "off"}:
        return False
    return default

def _num(value: object, default: float) -> float:
    if not isinstance(value, (str, bytes, int, float)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def _clamped(value: object, default: int, low: int, high: int) -> int:
    """Numeric level: a broken value falls back to the default, an out-of-range value is clamped."""
    if isinstance(value, bool) or not isinstance(value, (str, bytes, int, float)):
        return default
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))

def _num_clamped(value: object, default: float, low: float, high: float) -> float:
    number = _num(value, default)
    return max(low, min(high, number))

def _section(raw: Mapping, key: str) -> dict:
    body = raw.get(key)
    return body if isinstance(body, dict) else {}

_MISSING = object()

def _unknown_keys(
    mapping: Mapping, schema: Mapping[str, object], label: str, path: Path
) -> None:
    """Fail fast on a mistyped core key; `gateways.<name>.*` is validated by its gateway."""
    found: list[str] = []
    for key, value in mapping.items():
        expected = schema.get(key, _MISSING)
        if expected is _MISSING:
            found.append(f"{label}.{key}")
            continue
        if isinstance(expected, dict) and isinstance(value, Mapping):
            _unknown_keys(value, expected, f"{label}.{key}", path)
    if found:
        raise ConfigError(
            f"unknown config key(s) in {path}: {', '.join(sorted(found))}; "
            "remove them or fix the spelling"
        )

def _yaml_file(path: Path, label: str) -> dict:
    if not path.is_file():
        raise ConfigError(f"{label} not found: {path}")
    try:
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"{label} is invalid: {exc}") from exc
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ConfigError(f"{label} must be a mapping: {path}")
    return body

def _mcp_servers(raw: object) -> dict[str, MCPServerConfig]:
    servers: object = (raw or {}).get("servers") if isinstance(raw, dict) else None
    if not isinstance(servers, dict):
        return {}
    result: dict[str, MCPServerConfig] = {}
    for name, body in servers.items():
        body = body if isinstance(body, dict) else {}
        command = body.get("command")
        url = body.get("url")
        if command is None and url is None:
            raise ConfigError(f"mcp server '{name}' needs 'command' or 'url'")
        if command is not None and url is not None:
            raise ConfigError(f"mcp server '{name}' takes 'command' or 'url', not both")
        if url is not None:
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                raise ConfigError(f"mcp server '{name}' url must start with http:// or https://")
        elif not isinstance(command, str) or not command:
            raise ConfigError(f"mcp server '{name}' needs 'command'")
        stdio = url is None
        raw_env = body.get("env") if stdio else None
        env = raw_env if isinstance(raw_env, dict) else {}
        result[str(name)] = MCPServerConfig(
            name=str(name),
            command=command if stdio else None,
            args=_str_tuple(body.get("args")) if stdio else (),
            env={str(k): str(v) for k, v in env.items()},
            url=url,
        )
    return result

def _database(raw: Mapping) -> DatabaseConfig:
    body = _section(raw, "database")
    mig = _section(body, "migrations")
    mode = str(mig.get("mode") or "on_boot").strip().lower()
    if mode not in {"on_boot", "manual"}:
        raise ConfigError("database.migrations.mode must be 'on_boot' or 'manual'")
    migrations = MigrationsConfig(
        mode=mode,
        allow_down=_bool(mig.get("allow_down"), False),
        backfill_batch=max(1, int(_num(mig.get("backfill_batch"), 500))),
        advisory_lock_timeout_seconds=max(
            1.0, _num(mig.get("advisory_lock_timeout_seconds"), 30.0)
        ),
    )
    return DatabaseConfig(
        url=str(body.get("url") or "").strip(),
        url_env=(
            str(body.get("url_env") or "MONOAGENS_DATABASE_URL").strip()
            or "MONOAGENS_DATABASE_URL"
        ),
        pool_size=max(1, int(_num(body.get("pool_size"), 5))),
        max_overflow=max(0, int(_num(body.get("max_overflow"), 0))),
        pool_timeout=_num(body.get("pool_timeout"), 30.0),
        migrations=migrations,
    )

def _sandbox(raw: Mapping, base: Path) -> SandboxConfig:
    body = _section(raw, "sandbox")
    host_ws_raw = body.get("host_workspace")
    host_workspace = _expand(str(host_ws_raw), base) if host_ws_raw else None
    # The host path is not always visible from the container (daemon on the host), so it
    # is validated as a directory when the tool runs. Here we only reject obvious misses.
    if host_workspace is not None and host_workspace.exists() and not host_workspace.is_dir():
        raise ConfigError(f"sandbox.host_workspace '{host_workspace}' is not a directory")
    return SandboxConfig(
        runtime=str(body.get("runtime") or "").strip(),
        image=str(body.get("image") or "docker.io/library/python:3.12-slim"),
        timeout_seconds=_num(body.get("timeout_seconds"), 120.0),
        network=str(body.get("network") or "none").strip(),
        host_workspace=host_workspace,
        env=_str_tuple(body.get("env")),
        proxy=str(body.get("proxy") or "").strip(),
        pids_limit=max(1, int(_num(body.get("pids_limit"), 256))),
        memory=str(body.get("memory") or "512m").strip(),
        cpus=_num(body.get("cpus"), 1.0),
    )

REDACT_MODES = frozenset({"non_administrator", "never", "always"})

def _redact_mode(raw: Mapping) -> str:
    """`engine.prompt.self_block.redact_infra` (ADR-0015 §4.5)."""
    value = str(raw.get("redact_infra") or "non_administrator").strip().lower()
    if value not in REDACT_MODES:
        raise ConfigError(
            "engine.prompt.self_block.redact_infra must be one of "
            f"{sorted(REDACT_MODES)}"
        )
    return value

def _web_search_config(raw: Mapping) -> WebSearchConfig:
    """`web_search` (ADR-0004 §4.5): a provider typo fails at boot, never at query time."""
    base_url = str(raw.get("base_url") or "").strip().rstrip("/")
    # A legacy host file (and `monoagens init --websearch searxng`) only sets `base_url`;
    # an absent provider keeps that operator intent instead of silently querying ddgs.
    provider = str(raw.get("provider") or ("searxng" if base_url else "ddgs")).strip().lower()
    fallbacks = tuple(
        name.strip().lower() for name in _str_tuple(raw.get("fallbacks")) if name.strip()
    )
    for name in (provider, *fallbacks):
        if name not in WEB_SEARCH_PROVIDERS:
            where = "provider" if name == provider else "fallbacks"
            choices = ", ".join(sorted(WEB_SEARCH_PROVIDERS))
            raise ConfigError(
                f"web_search.{where}: unknown provider {name!r}; choose from {choices}"
            )
    return WebSearchConfig(
        provider=provider,
        fallbacks=fallbacks,
        base_url=base_url,
        api_key_env=str(raw.get("api_key_env") or "").strip(),
        engine_id_env=str(raw.get("engine_id_env") or "").strip(),
        ddgs_backend=str(raw.get("ddgs_backend") or "auto").strip() or "auto",
        region=str(raw.get("region") or "").strip(),
        timeout_seconds=_num(raw.get("timeout_seconds"), 10.0),
        fetch=_fetch_config(_section(raw, "fetch")),
    )

def _fetch_config(raw: Mapping) -> FetchConfig:
    """`web_search.fetch` (ADR-0021 §4.3); the SSRF ranges are code, never config."""
    ports: list[int] = []
    for item in raw.get("allow_ports") or ():
        try:
            port = int(item)
        except (TypeError, ValueError):
            continue
        if 0 < port < 65536:
            ports.append(port)
    return FetchConfig(
        enabled=_bool(raw.get("enabled"), True),
        max_output_chars=_clamped(raw.get("max_output_chars"), 12_000, 200, 12_000),
        timeout_seconds=_num_clamped(raw.get("timeout_seconds"), 8.0, 0.5, 60.0),
        max_redirects=_clamped(raw.get("max_redirects"), 3, 0, 10),
        allow_ports=tuple(ports) or (80, 443),
        deny_hosts=tuple(item.strip().lower() for item in _str_tuple(raw.get("deny_hosts"))),
    )

def _read_config_section(raw: Mapping) -> ReadConfig:
    """`read.grep` (ADR-0021 §4.2)."""
    grep_raw = _section(raw, "grep")
    return ReadConfig(
        grep=GrepConfig(
            max_matches=_clamped(grep_raw.get("max_matches"), 60, 1, 200),
            max_output_chars=_clamped(grep_raw.get("max_output_chars"), 4_000, 200, 20_000),
            max_file_bytes=_clamped(grep_raw.get("max_file_bytes"), 2_000_000, 1_024, 20_000_000),
        )
    )

def _tools_config(raw: Mapping) -> ToolsConfig:
    """`engine.tools` (ADR-0024 §5): validation has no `off`."""
    body = _section(raw, "tools")
    mode = str(body.get("args") or "strict").strip().lower()
    if mode != "strict":
        raise ConfigError("engine.tools.args must be 'strict'; validation cannot be disabled")
    hot = _str_tuple(body.get("hot")) or ToolsConfig().hot
    return ToolsConfig(
        args=mode,
        max_errors=_clamped(body.get("max_errors"), 5, 1, 50),
        hot=tuple(hot),
        activate_on_demand=_bool(body.get("activate_on_demand"), True),
    )

def _shell_config(raw: Mapping) -> ShellConfig:
    """`engine.shell.env` (ADR-0024 §4.6): a wildcard is a boot failure, not a shortcut."""
    body = _section(raw, "shell")
    if "env" not in body:
        return ShellConfig()
    names = _str_tuple(body.get("env"))
    if "*" in names:
        raise ConfigError("engine.shell.env must name variables; the wildcard '*' is refused")
    return ShellConfig(env=names)

def _search_config(raw: Mapping) -> SearchConfig:
    """`engine.search` (ADR-0026 §5)."""
    return SearchConfig(
        fts=_bool(raw.get("fts"), True),
        lang=str(raw.get("lang") or "simple").strip() or "simple",
        trgm=_bool(raw.get("trgm"), True),
        trgm_threshold=_num_clamped(raw.get("trgm_threshold"), 0.3, 0.0, 1.0),
        max_results=_clamped(raw.get("max_results"), 50, 1, 50),
        snippet_chars=_clamped(raw.get("snippet_chars"), 240, 60, 2000),
    )

def _self_knowledge_config(raw: Mapping) -> SelfKnowledgeConfig:
    """`engine.self_knowledge` (ADR-0015 §4.8/§5)."""
    defaults = SelfKnowledgeConfig()
    topics = _str_tuple(raw.get("topics")) or defaults.topics
    docs = _str_tuple(raw.get("docs"))
    return SelfKnowledgeConfig(
        enabled=_bool(raw.get("enabled"), True),
        capability_index=_bool(raw.get("capability_index"), True),
        topics=topics,
        coarse_buckets=_bool(raw.get("coarse_buckets"), True),
        topology=_bool(raw.get("topology"), True),
        max_chars_per_topic=_clamped(raw.get("max_chars_per_topic"), 1200, 200, 8000),
        forbidden_token_check=_bool(raw.get("forbidden_token_check"), True),
        docs=docs if "docs" in raw else defaults.docs,
        docs_max_chars=_clamped(raw.get("docs_max_chars"), 4000, 200, 20_000),
    )

def _plugin_config(raw: Mapping, base: Path, workspace: Path) -> PluginConfig:
    """`plugin` (ADR-0027 §5): root, fetch, and the operator-named source."""
    body = _section(raw, "plugin")
    root_text = str(body.get("root") or "").strip()
    return PluginConfig(
        root=_expand(root_text, base) if root_text else workspace / "plugins",
        fetch=_bool(body.get("fetch"), True),
        source=str(body.get("source") or "").strip(),
        local=_bool(body.get("local"), False),
    )

def _intent_config(raw: Mapping) -> IntentConfig:
    """`memory.intent.{remember,forget}` (ADR-0005 §4.6): enabled, floor, and margin each."""
    remember = _section(raw, "remember")
    forget = _section(raw, "forget")
    return IntentConfig(
        remember_enabled=_bool(remember.get("enabled"), True),
        remember_floor=_num_clamped(remember.get("floor"), 0.62, 0.0, 1.0),
        remember_margin=_num_clamped(remember.get("margin"), 0.06, 0.0, 1.0),
        forget_enabled=_bool(forget.get("enabled"), True),
        forget_floor=_num_clamped(forget.get("floor"), 0.64, 0.0, 1.0),
        forget_margin=_num_clamped(forget.get("margin"), 0.06, 0.0, 1.0),
    )

def _retention_config(raw: Mapping) -> RetentionConfig:
    """`memory.retention.purge` (ADR-0005 §4.7): grace before deletion and one batch size."""
    body = _section(raw, "purge")
    return RetentionConfig(
        purge_grace_days=_num_clamped(body.get("grace_days"), 1.0, 0.0, 365.0),
        purge_batch=_clamped(body.get("batch"), 500, 1, 100000),
    )

def _plugins_config(raw: Mapping) -> dict[str, PluginSettings]:
    """`plugins.<name>` (ADR-0028 §5): enabled and the pinned digest, both operator choices."""
    body = _section(raw, "plugins")
    result: dict[str, PluginSettings] = {}
    for name, value in body.items():
        value = value if isinstance(value, dict) else {}
        result[str(name)] = PluginSettings(
            enabled=_bool(value.get("enabled"), False),
            digest=str(value.get("digest") or "").strip(),
        )
    return result

