"""Load .env plus the combined config.yaml (host, engine, gateways, soul) and roles.yaml.

Validation is strict and never touches the filesystem beyond reading; one file is the
source of truth for the host, and DB documents override its engine/soul/role seeds.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml

from ..providers import resolve
from . import lexicon
from .roles import RoleError, RoleRegistry, load_roles
from .soul import Soul, load_soul

log = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Invalid configuration; the message is safe to show to the operator."""


DEFAULT_SENSITIVE_COMMANDS: tuple[str, ...] = (
    "rm *",
    "rmdir *",
    "shred *",
    "dd *",
    "truncate *",
    "wipefs *",
    "mkfs*",
    "fdisk *",
    "parted *",
    "shutdown *",
    "reboot *",
    "poweroff *",
    "halt *",
    "systemctl *",
    "service *",
    "kill *",
    "pkill *",
    "killall *",
    "chmod *",
    "chown *",
    "chgrp *",
    "mount *",
    "umount *",
    "git reset --hard*",
    "git clean -*f*",
    "git push *--force*",
    "git push -f*",
    "find * -delete*",
    "find * -exec rm*",
)


class Documents(Protocol):
    def get(self, name: str) -> str | None: ...

    def names(self, prefix: str = "") -> list[str]: ...


@dataclass(frozen=True)
class RetryConfig:
    """Retry per path (ADR-0017 §4.6): the turn fails visibly, background work backs off."""

    turn_attempts: int = 1
    worker_attempts: int = 3
    base_seconds: float = 2.0
    cap_seconds: float = 60.0
    jitter: float = 0.2
    retry_after_max_seconds: float = 10.0


@dataclass(frozen=True)
class IOConfig:
    workers: int = 8


@dataclass(frozen=True)
class ConcurrencyConfig:
    max_chat_locks: int = 256


@dataclass(frozen=True)
class NoticeRetryConfig:
    attempts: int = 2
    backoff_seconds: float = 1.0


@dataclass(frozen=True)
class ResumeNoticeConfig:
    after_seconds: int = 300


@dataclass(frozen=True)
class ShutdownConfig:
    grace_seconds: float = 25.0
    drain_seconds: float = 15.0
    notice_seconds: float = 5.0
    notice_per_chat: bool = True
    notice_retry: NoticeRetryConfig = field(default_factory=NoticeRetryConfig)
    resume_notice: ResumeNoticeConfig = field(default_factory=ResumeNoticeConfig)
    stop_grace_period: float = 30.0  # the orchestrator's stopped deadline (ADR-0013 §9)


@dataclass(frozen=True)
class TurnConfig:
    resume: bool = True
    max_attempts: int = 2
    max_age_seconds: int = 900
    stale_after_seconds: int = 21600
    partial_delivery: str = "skip"  # skip | resend
    max_resume_per_chat: int = 5
    max_resume_per_minute: int = 10
    backlog_policy: str = "per_message_then_consolidate"
    timeout_seconds: float = 90.0
    min_call_seconds: float = 5.0
    shutdown: ShutdownConfig = field(default_factory=ShutdownConfig)


@dataclass(frozen=True)
class WorkerRetryConfig:
    attempts: int = 5
    backoff_seconds: float = 2.0
    dead_letter_after: int = 5


@dataclass(frozen=True)
class ReaperConfig:
    every_seconds: float = 30.0
    batch: int = 100


@dataclass(frozen=True)
class WorkerConfig:
    retry: WorkerRetryConfig = field(default_factory=WorkerRetryConfig)
    reaper: ReaperConfig = field(default_factory=ReaperConfig)


@dataclass(frozen=True)
class LLMConfig:
    provider: str = ""  # chosen by the operator at init; never a provider default
    base_url: str = ""
    model: str = ""  # chosen by the operator at init; never filled from a provider default
    api_key_env: str = ""
    reasoning_effort: str = ""  # "" = the provider's own default
    session_header: str = ""
    api_key_optional: bool = False
    models_url: str = ""  # "" = {base_url}/models
    timeout_seconds: float = 120.0
    temperature: float = 0.9
    run_budget_seconds: int = 240
    retry: RetryConfig = field(default_factory=RetryConfig)

    def session_headers(self, session: str) -> dict[str, str]:
        """Per-request headers for a session; empty when the provider wants none."""
        if not self.session_header or not session:
            return {}
        return {self.session_header: session}


@dataclass(frozen=True)
class MigrationsConfig:
    mode: str = "on_boot"  # on_boot | manual
    allow_down: bool = False  # `down` only in dev
    backfill_batch: int = 500
    advisory_lock_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class DatabaseConfig:
    url: str = ""
    url_env: str = "EIDESOUL_DATABASE_URL"
    pool_size: int = 5
    pool_timeout: float = 30.0
    migrations: MigrationsConfig = field(default_factory=MigrationsConfig)


@dataclass(frozen=True)
class SandboxConfig:
    runtime: str = ""  # empty = autodetect podman then docker
    image: str = "docker.io/library/python:3.12-slim"
    timeout_seconds: float = 120.0
    network: str = "none"  # dedicated internal network; bridge/none/host are rejected
    host_workspace: Path | None = None  # source mount when the daemon runs on the host
    env: tuple[str, ...] = ()  # extra variables allowed into the container
    proxy: str = ""  # http://sandbox-proxy:3128
    pids_limit: int = 256
    memory: str = "512m"
    cpus: float = 1.0


@dataclass(frozen=True)
class WebSearchConfig:
    base_url: str = ""  # empty = only the ddgs backend
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class PrivacyConfig:
    """Disclosure policy limits: level 0-4, group vs DM ceilings, optional auditor."""

    enabled: bool = True
    default_level: int = 2
    ask_level: int = 4
    group_max_level: int = 2
    dm_max_level: int = 4
    auditor: bool = True


@dataclass(frozen=True)
class MemoryConfig:
    auto_capture: bool = True
    capture_min_chars: int = 8
    max_items: int = 12
    max_chars: int = 3000
    collection: str = "eidesoul_memories"
    extraction_model: str = ""  # empty = llm.model
    telemetry: bool = False
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)


@dataclass(frozen=True)
class ApprovalConfig:
    mode: str = "yolo"  # yolo | off | strict
    sensitive_tools: tuple[str, ...] = ()
    sensitive_commands: tuple[str, ...] = DEFAULT_SENSITIVE_COMMANDS
    settings: bool = True
    ttl_seconds: int = 900


@dataclass(frozen=True)
class SkillsConfig:
    max_index_chars: int = 3000


@dataclass(frozen=True)
class PromptConfig:
    salience: bool = True
    max_chars: int = 9000
    thread_chars: int = 4000


@dataclass(frozen=True)
class HumanizeConfig:
    enabled: bool = False
    cpms: float = 25.0
    min_delay: float = 0.5
    max_delay: float = 5.0
    between_delay: float = 0.7
    max_bubbles: int = 3
    reaction_stickers: tuple[str, ...] = ()


@dataclass(frozen=True)
class VibeConfig:
    enabled: bool = True  # fail-open when the embedder is unavailable
    attack: float = 0.35
    baseline_influence: float = 0.05
    hold_seconds: float = 1.2
    tick_seconds: float = 1.0


@dataclass(frozen=True)
class AffectConfig:
    enabled: bool = True
    volatility: str = "expressive"
    grudge_half_life_hours: float = 1.5
    vibe: VibeConfig = field(default_factory=VibeConfig)


@dataclass(frozen=True)
class StyleConfig:
    enabled: bool = True
    max_chars: int = 800


@dataclass(frozen=True)
class CharacterConfig:
    """Character layer: polish robotic replies and keep the register inside its level.

    Levels replace absolute bans. `toxicity` 0-4: 0 no jabs or swearing, 1 light teasing,
    2 one short jab plus mild swearing when the room is joking, 3 harder jabs (sexual
    innuendo only towards men who already joined the joke), 4 the room's register with no
    filter. `adult_memes` 0-3: 0 recognise only, 1 mirror only when this chat already uses
    them, 2 use when the room jokes about memes, 3 start it in a clearly joking group with
    a male target.
    """

    enabled: bool = False
    max_chars: int = 400
    max_tokens: int = 500
    temperature: float = 0.7
    toxicity: int = 2
    adult_memes: int = 1
    gender_gate: bool = True
    gender_confidence: float = 0.6
    roast_jabs: int = 1
    gender_avatar: bool = False  # avatar is only a fallback signal
    max_questions: int = 1  # questions per reply, 1..2


@dataclass(frozen=True)
class GuardConfig:
    """Sandbox guard limits; `model` empty means `engine.llm.model`."""

    enabled: bool = True
    model: str = ""
    timeout_seconds: float = 6.0
    max_command_chars: int = 4000


@dataclass(frozen=True)
class LanguageConfig:
    """Language pack location; empty means the package `library/lexicon/<code>.json`."""

    lexicon_dir: Path | None = None  # test-only `engine.language.lexicon_dir` override


@dataclass(frozen=True)
class AmbientConfig:
    enabled: bool = True
    tick_seconds: int = 10
    digest_every: int = 12
    digest_cooldown: int = 900
    history_window: int = 30
    idle_minutes: int = 20
    research_cooldown: int = 21600
    research_per_day: int = 6
    research_results: int = 5
    context_enabled: bool = True
    context_hours: int = 6
    context_topics: int = 2
    context_results: int = 4


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    command: str | None = None  # stdio; None when the server uses a url
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None  # Streamable HTTP


@dataclass(frozen=True)
class GatewaySettings:
    name: str
    enabled: bool = False
    options: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Paths:
    config: Path
    roles: Path
    soul_yaml: Path
    workspace: Path


@dataclass(frozen=True)
class HostConfig:
    database: DatabaseConfig
    workspace: Path
    sandbox: SandboxConfig
    web_search: WebSearchConfig
    mcp: dict[str, MCPServerConfig]
    roles_file: Path
    soul_file: Path
    sync_documents: bool


@dataclass(frozen=True)
class Config:
    paths: Paths
    database: DatabaseConfig
    documents: Documents | None
    llm: LLMConfig
    workspace: Path
    approval: ApprovalConfig
    roles: RoleRegistry
    default_role: str
    gateways: dict[str, GatewaySettings]
    soul: Soul
    sandbox: SandboxConfig
    mcp: dict[str, MCPServerConfig]
    web_search: WebSearchConfig = field(default_factory=WebSearchConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    prompt: PromptConfig = field(default_factory=PromptConfig)
    humanize: HumanizeConfig = field(default_factory=HumanizeConfig)
    style: StyleConfig = field(default_factory=StyleConfig)
    character: CharacterConfig = field(default_factory=CharacterConfig)
    affect: AffectConfig = field(default_factory=AffectConfig)
    ambient: AmbientConfig = field(default_factory=AmbientConfig)
    guard: GuardConfig = field(default_factory=GuardConfig)
    language: LanguageConfig = field(default_factory=LanguageConfig)
    turns: TurnConfig = field(default_factory=TurnConfig)
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    io: IOConfig = field(default_factory=IOConfig)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)

    def api_key(self) -> str:
        """The key for this provider; a local endpoint accepts any non-empty string."""
        found = os.environ.get(self.llm.api_key_env)
        if found:
            return found
        if self.llm.api_key_optional:
            return "local"
        return os.environ[self.llm.api_key_env]

    def database_url(self) -> str:
        return resolve_database_url(self.database)


def resolve_database_url(database: DatabaseConfig) -> str:
    """Effective URL: env `url_env` when set, otherwise `url`."""
    env_name = str(database.url_env or "").strip()
    if env_name and os.environ.get(env_name):
        return str(os.environ[env_name]).strip()
    return str(database.url or "").strip()


def load_env(path: str | Path = ".env") -> None:
    """Read simple KEY=VALUE lines from a file in the working directory. Missing is fine."""
    file = Path(str(path)).expanduser()
    if file.is_absolute() or file.name != str(path):
        raise ConfigError(f"env file must be a bare file name in the working directory: {path}")
    file = (Path.cwd() / file.name).resolve()
    if not file.is_file():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def secure_db(path: Path) -> None:
    """The session DB holds credentials; never leave it world-readable."""
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    if path.exists():
        try:
            path.chmod(0o600)
        except OSError:
            pass


def drop_session(path: Path) -> None:
    """Delete a temporary session DB together with its WAL/SHM files."""
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(f"{path}{suffix}").unlink(missing_ok=True)
        except OSError:
            log.warning("failed to delete session %s", path, exc_info=True)


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
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamped(value: object, default: int, low: int, high: int) -> int:
    """Numeric level: a broken value falls back to the default, an out-of-range value is clamped."""
    if isinstance(value, bool):
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
        env = body.get("env") if stdio and isinstance(body.get("env"), dict) else {}
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
        url_env=str(body.get("url_env") or "EIDESOUL_DATABASE_URL").strip() or "EIDESOUL_DATABASE_URL",
        pool_size=max(1, int(_num(body.get("pool_size"), 5))),
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


def load_host(path: str | Path | None = None) -> tuple[HostConfig, Paths]:
    """Read config.yaml without creating directories or touching the DB."""
    target = Path(path or os.environ.get("EIDESOUL_CONFIG") or "config.yaml")
    if not target.is_file():
        raise ConfigError(f"config not found: {target} (run `eidesoul init`)")
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"config YAML is invalid: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config is empty or not a mapping: {target}")
    base = target.resolve().parent
    workspace = _expand(str(raw.get("workspace") or ".eidesoul/workspace"), base)
    roles_file = _expand(str(raw.get("roles_file") or "roles.yaml"), base)
    soul_file = _expand(str(raw.get("soul_file") or "soul.yaml"), base)
    search_raw = _section(raw, "web_search")
    host = HostConfig(
        database=_database(raw),
        workspace=workspace,
        sandbox=_sandbox(raw, base),
        web_search=WebSearchConfig(
            base_url=str(search_raw.get("base_url") or "").strip().rstrip("/"),
            timeout_seconds=_num(search_raw.get("timeout_seconds"), 10.0),
        ),
        mcp=_mcp_servers(raw.get("mcp")),
        roles_file=roles_file,
        soul_file=soul_file,
        sync_documents=_bool(raw.get("sync_documents"), True),
    )
    paths = Paths(
        config=target.resolve(),
        roles=roles_file,
        soul_yaml=soul_file,
        workspace=workspace,
    )
    return host, paths


def _engine(raw: Mapping, documents: Documents | None) -> dict:
    """Engine document wins; otherwise the `engine` section of config.yaml."""
    text = documents.get("engine") if documents is not None else None
    if text:
        try:
            body = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConfigError(f"engine document is invalid: {exc}") from exc
        if body is None:
            return {}
        if not isinstance(body, dict):
            raise ConfigError("engine document must be a mapping")
        return body
    return _section(raw, "engine")


def _soul(raw: Mapping, paths: Paths, documents: Documents | None) -> Soul:
    """The `soul` document wins over soul.yaml; config.yaml carries no soul keys."""
    text = documents.get("soul") if documents is not None else None
    source: Path | Mapping[str, object] = paths.soul_yaml
    if text:
        try:
            body = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConfigError(f"soul document is invalid: {exc}") from exc
        source = body if isinstance(body, Mapping) else {}
    try:
        return load_soul(source, source_path=paths.soul_yaml)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _roles(
    raw: Mapping, paths: Paths, documents: Documents | None, default_role: str
) -> RoleRegistry:
    """Role documents win; otherwise read every role from `roles.yaml`."""
    source: Mapping[str, str | dict] = {}
    names = documents.names("role:") if documents is not None else []
    if names:
        source = {
            name.split(":", 1)[1]: documents.get(name) or ""
            for name in names
            if ":" in name
        }
    else:
        body = _roles_file(paths)
        if body:
            source = body
    if not source:
        raise ConfigError(f"no roles found: {paths.roles} (run `eidesoul init`)")
    try:
        return load_roles(source, default_role)
    except RoleError as exc:
        raise ConfigError(str(exc)) from exc


def _roles_file(paths: Paths) -> dict:
    """Read the `roles` mapping from roles.yaml; empty when the file is absent."""
    if not paths.roles.is_file():
        return {}
    raw = _yaml_file(paths.roles, "roles.yaml")
    body = raw.get("roles")
    if not isinstance(body, dict):
        raise ConfigError(f"roles.yaml must contain a 'roles' mapping: {paths.roles}")
    return body


def _gateways(raw: Mapping, *, required: bool = False) -> dict[str, GatewaySettings]:
    body = raw.get("gateways")
    if body is None:
        if required:
            raise ConfigError("config.yaml is missing the 'gateways' section (run `eidesoul init`)")
        return {}
    if not isinstance(body, dict):
        raise ConfigError("config.yaml: 'gateways' must be a mapping")
    result: dict[str, GatewaySettings] = {}
    for name, value in body.items():
        value = value if isinstance(value, dict) else {}
        options = {str(key): item for key, item in value.items() if key != "enabled"}
        result[str(name)] = GatewaySettings(
            name=str(name),
            enabled=_bool(value.get("enabled"), False),
            options=options,
        )
    return result


def _retry_config(raw: Mapping) -> RetryConfig:
    """`engine.llm.retry`: one retry on the turn path, backoff for background work."""
    return RetryConfig(
        turn_attempts=max(1, int(_num(raw.get("turn_attempts"), 1))),
        worker_attempts=max(1, int(_num(raw.get("worker_attempts"), 3))),
        base_seconds=max(0.0, _num(raw.get("base_seconds"), 2.0)),
        cap_seconds=max(0.1, _num(raw.get("cap_seconds"), 60.0)),
        jitter=max(0.0, min(1.0, _num(raw.get("jitter"), 0.2))),
        retry_after_max_seconds=max(0.0, _num(raw.get("retry_after_max_seconds"), 10.0)),
    )


def _llm_config(
    llm_raw: Mapping, *, require_key: bool, require_model: bool
) -> LLMConfig:
    """The `engine.llm` block: a provider name is required, never defaulted in code."""
    provider = str(llm_raw.get("provider") or "").strip()
    if not provider:
        raise ConfigError(
            "engine.llm.provider is required; choose one with `eidesoul init --provider <name>` "
            "or set engine.llm.provider in config.yaml"
        )
    try:
        provider, defaults = resolve(
            provider,
            str(llm_raw.get("base_url") or ""),
            str(llm_raw.get("api_key_env") or ""),
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if not defaults.api_key_env:
        raise ConfigError("engine.llm.api_key_env is required when the provider has no default")
    model = str(llm_raw.get("model") or "").strip()
    if require_model and not model:
        raise ConfigError(
            "engine.llm.model is required; list ids with `eidesoul models` and set one with "
            "`eidesoul init --model <id>` or by editing config.yaml"
        )
    if require_key and not defaults.api_key_optional and not os.environ.get(defaults.api_key_env):
        raise ConfigError(f"env {defaults.api_key_env} is not set (put it in .env)")
    return LLMConfig(
        provider=provider,
        base_url=defaults.base_url,
        model=model,
        api_key_env=defaults.api_key_env,
        reasoning_effort=str(llm_raw.get("reasoning_effort") or "").strip(),
        session_header=defaults.session_header,
        api_key_optional=defaults.api_key_optional,
        models_url=defaults.models_url,
        timeout_seconds=_num(llm_raw.get("timeout_seconds"), 120.0),
        temperature=_num(llm_raw.get("temperature"), 0.9),
        run_budget_seconds=int(_num(llm_raw.get("run_budget_seconds"), 240)),
        retry=_retry_config(_section(llm_raw, "retry")),
    )


BACKLOG_POLICIES = frozenset(
    {"per_message", "per_message_then_consolidate", "consolidate", "last_only"}
)
PARTIAL_DELIVERY = frozenset({"skip", "resend"})


def _turn_config(raw: Mapping, turn_raw: Mapping) -> TurnConfig:
    """`engine.turns` (resume/shutdown) plus `engine.turn` (the one-turn deadline)."""
    shutdown_raw = _section(raw, "shutdown")
    notices = _section(shutdown_raw, "notice_retry")
    resume_raw = _section(shutdown_raw, "resume_notice")
    partial = str(raw.get("partial_delivery") or "skip").strip().lower()
    if partial not in PARTIAL_DELIVERY:
        raise ConfigError("turns.partial_delivery must be 'skip' or 'resend'")
    policy = str(raw.get("backlog_policy") or "per_message_then_consolidate").strip().lower()
    if policy not in BACKLOG_POLICIES:
        raise ConfigError(f"turns.backlog_policy must be one of {sorted(BACKLOG_POLICIES)}")
    shutdown = ShutdownConfig(
        grace_seconds=max(1.0, _num(shutdown_raw.get("grace_seconds"), 25.0)),
        drain_seconds=max(0.0, _num(shutdown_raw.get("drain_seconds"), 15.0)),
        notice_seconds=max(0.0, _num(shutdown_raw.get("notice_seconds"), 5.0)),
        notice_per_chat=_bool(shutdown_raw.get("notice_per_chat"), True),
        notice_retry=NoticeRetryConfig(
            attempts=max(0, int(_num(notices.get("attempts"), 2))),
            backoff_seconds=max(0.0, _num(notices.get("backoff_seconds"), 1.0)),
        ),
        resume_notice=ResumeNoticeConfig(
            after_seconds=max(0, int(_num(resume_raw.get("after_seconds"), 300)))
        ),
        stop_grace_period=max(1.0, _num(shutdown_raw.get("stop_grace_period"), 30.0)),
    )
    if not shutdown.grace_seconds > shutdown.drain_seconds + shutdown.notice_seconds:
        raise ConfigError(
            "turns.shutdown.grace_seconds must be greater than drain_seconds + notice_seconds "
            f"({shutdown.drain_seconds:g} + {shutdown.notice_seconds:g})"
        )
    if not shutdown.grace_seconds < shutdown.stop_grace_period:
        raise ConfigError(
            "turns.shutdown.grace_seconds must be less than stop_grace_period "
            f"({shutdown.stop_grace_period:g}); the orchestrator would cut the shutdown short"
        )
    return TurnConfig(
        resume=_bool(raw.get("resume"), True),
        max_attempts=max(1, int(_num(raw.get("max_attempts"), 2))),
        max_age_seconds=max(1, int(_num(raw.get("max_age_seconds"), 900))),
        stale_after_seconds=max(1, int(_num(raw.get("stale_after_seconds"), 21600))),
        partial_delivery=partial,
        max_resume_per_chat=max(1, int(_num(raw.get("max_resume_per_chat"), 5))),
        max_resume_per_minute=max(1, int(_num(raw.get("max_resume_per_minute"), 10))),
        backlog_policy=policy,
        timeout_seconds=max(1.0, _num(turn_raw.get("timeout_seconds"), 90.0)),
        min_call_seconds=max(0.0, _num(turn_raw.get("min_call_seconds"), 5.0)),
        shutdown=shutdown,
    )


def _worker_config(raw: Mapping) -> WorkerConfig:
    retry = _section(raw, "retry")
    reaper = _section(raw, "reaper")
    return WorkerConfig(
        retry=WorkerRetryConfig(
            attempts=max(1, int(_num(retry.get("attempts"), 5))),
            backoff_seconds=max(0.0, _num(retry.get("backoff_seconds"), 2.0)),
            dead_letter_after=max(1, int(_num(retry.get("dead_letter_after"), 5))),
        ),
        reaper=ReaperConfig(
            every_seconds=max(1.0, _num(reaper.get("every_seconds"), 30.0)),
            batch=max(1, int(_num(reaper.get("batch"), 100))),
        ),
    )


def load_config(
    path: str | Path | None = None,
    *,
    documents: Documents | None = None,
    require_key: bool = True,
    require_model: bool = True,
    require_gateway: bool = False,
) -> Config:
    host, paths = load_host(path)
    try:
        raw = yaml.safe_load(paths.config.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"config YAML is invalid: {exc}") from exc
    raw = raw if isinstance(raw, dict) else {}
    engine = _engine(raw, documents)
    llm = _llm_config(
        _section(engine, "llm"), require_key=require_key, require_model=require_model
    )
    default_role = str(engine.get("default_role") or "").strip()
    if not default_role:
        raise ConfigError("engine.default_role is required")
    roles = _roles(raw, paths, documents, default_role)
    gateways = _gateways(raw, required=require_gateway)
    for name, settings in gateways.items():
        if not settings.enabled:
            continue
        role = settings.options.get("role")
        if role is not None and roles.get(str(role)) is None:
            raise ConfigError(f"gateway '{name}': role '{role}' is not among the loaded roles")
    database = host.database
    url = resolve_database_url(database)
    if not url:
        raise ConfigError(
            f"database URL is empty; set database.url in config.yaml or env {database.url_env}"
        )
    database = DatabaseConfig(
        url=database.url,
        url_env=database.url_env,
        pool_size=database.pool_size,
        pool_timeout=database.pool_timeout,
        migrations=database.migrations,
    )

    memory_raw = _section(engine, "memory")
    approval_raw = _section(engine, "approval")
    skills_raw = _section(engine, "skills")
    prompt_raw = _section(engine, "prompt")
    humanize_raw = _section(engine, "humanize")
    style_raw = _section(engine, "style")
    character_raw = _section(engine, "character")
    affect_raw = _section(engine, "affect")
    ambient_raw = _section(engine, "ambient")
    guard_raw = _section(engine, "guard")
    vibe_raw = _section(affect_raw, "vibe")
    language_dir = str(_section(engine, "language").get("lexicon_dir") or "").strip()
    language = LanguageConfig(
        lexicon_dir=_expand(language_dir, paths.config.parent) if language_dir else None
    )
    if language.lexicon_dir is not None:
        lexicon.configure(language.lexicon_dir)

    approval_mode = str(approval_raw.get("mode") or "yolo").strip().lower()
    if approval_mode not in {"yolo", "off", "strict"}:
        raise ConfigError("approval.mode must be 'yolo', 'off', or 'strict'")
    approval_commands = approval_raw.get("sensitive_commands")
    privacy_raw = _section(memory_raw, "privacy")

    def _level(key: str, default: int) -> int:
        try:
            value = int(_num(privacy_raw.get(key), float(default)))
        except (TypeError, ValueError):
            return default
        return value if 0 <= value <= 4 else default

    privacy = PrivacyConfig(
        enabled=_bool(privacy_raw.get("enabled"), True),
        default_level=_level("default_level", 2),
        ask_level=_level("ask_level", 4),
        group_max_level=_level("group_max_level", 2),
        dm_max_level=_level("dm_max_level", 4),
        auditor=_bool(privacy_raw.get("auditor"), True),
    )

    return Config(
        paths=paths,
        database=database,
        documents=documents,
        llm=llm,
        workspace=host.workspace,
        approval=ApprovalConfig(
            mode=approval_mode,
            sensitive_tools=_str_tuple(approval_raw.get("sensitive_tools")),
            sensitive_commands=(
                DEFAULT_SENSITIVE_COMMANDS
                if approval_commands is None
                else _str_tuple(approval_commands)
            ),
            settings=_bool(approval_raw.get("settings"), True),
            ttl_seconds=int(_num(approval_raw.get("ttl_seconds"), 900)),
        ),
        roles=roles,
        default_role=default_role,
        gateways=gateways,
        soul=_soul(raw, paths, documents),
        sandbox=host.sandbox,
        mcp=host.mcp,
        web_search=host.web_search,
        memory=MemoryConfig(
            auto_capture=_bool(memory_raw.get("auto_capture"), True),
            capture_min_chars=int(_num(memory_raw.get("capture_min_chars"), 8)),
            max_items=int(_num(memory_raw.get("max_items"), 12)),
            max_chars=int(_num(memory_raw.get("max_chars"), 3000)),
            collection=str(memory_raw.get("collection") or "eidesoul_memories").strip()
            or "eidesoul_memories",
            extraction_model=str(memory_raw.get("extraction_model") or "").strip(),
            telemetry=_bool(memory_raw.get("telemetry"), False),
            privacy=privacy,
        ),
        skills=SkillsConfig(
            max_index_chars=int(_num(skills_raw.get("max_index_chars"), 3000))
        ),
        prompt=PromptConfig(
            salience=_bool(prompt_raw.get("salience"), True),
            max_chars=int(_num(prompt_raw.get("max_chars"), 9000)),
            thread_chars=int(_num(prompt_raw.get("thread_chars"), 4000)),
        ),
        humanize=HumanizeConfig(
            enabled=_bool(humanize_raw.get("enabled"), False),
            cpms=_num(humanize_raw.get("cpms"), 25.0),
            min_delay=_num(humanize_raw.get("min_delay"), 0.5),
            max_delay=_num(humanize_raw.get("max_delay"), 5.0),
            between_delay=_num(humanize_raw.get("between_delay"), 0.7),
            max_bubbles=int(_num(humanize_raw.get("max_bubbles"), 3)),
            reaction_stickers=_str_tuple(humanize_raw.get("reaction_stickers")),
        ),
        style=StyleConfig(
            enabled=_bool(style_raw.get("enabled"), True),
            max_chars=int(_num(style_raw.get("max_chars"), 800)),
        ),
        character=CharacterConfig(
            enabled=_bool(character_raw.get("enabled"), False),
            max_chars=int(_num(character_raw.get("max_chars"), 400)),
            max_tokens=int(_num(character_raw.get("max_tokens"), 500)),
            temperature=_num(character_raw.get("temperature"), 0.7),
            toxicity=_clamped(character_raw.get("toxicity"), 2, 0, 4),
            adult_memes=_clamped(character_raw.get("adult_memes"), 1, 0, 3),
            gender_gate=_bool(character_raw.get("gender_gate"), True),
            gender_confidence=_num_clamped(character_raw.get("gender_confidence"), 0.6, 0.0, 1.0),
            roast_jabs=_clamped(character_raw.get("roast_jabs"), 1, 1, 3),
            gender_avatar=_bool(character_raw.get("gender_avatar"), False),
            max_questions=_clamped(character_raw.get("max_questions"), 1, 1, 2),
        ),
        affect=AffectConfig(
            enabled=_bool(affect_raw.get("enabled"), True),
            volatility=str(affect_raw.get("volatility") or "expressive").strip() or "expressive",
            grudge_half_life_hours=_num(affect_raw.get("grudge_half_life_hours"), 1.5),
            vibe=VibeConfig(
                enabled=_bool(vibe_raw.get("enabled"), True),
                attack=_num(vibe_raw.get("attack"), 0.35),
                baseline_influence=_num(vibe_raw.get("baseline_influence"), 0.05),
                hold_seconds=_num(vibe_raw.get("hold_seconds"), 1.2),
                tick_seconds=_num(vibe_raw.get("tick_seconds"), 1.0),
            ),
        ),
        ambient=AmbientConfig(
            enabled=_bool(ambient_raw.get("enabled"), True),
            tick_seconds=int(_num(ambient_raw.get("tick_seconds"), 10)),
            digest_every=int(_num(ambient_raw.get("digest_every"), 12)),
            digest_cooldown=int(_num(ambient_raw.get("digest_cooldown"), 900)),
            history_window=int(_num(ambient_raw.get("history_window"), 30)),
            idle_minutes=int(_num(ambient_raw.get("idle_minutes"), 20)),
            research_cooldown=int(_num(ambient_raw.get("research_cooldown"), 21600)),
            research_per_day=int(_num(ambient_raw.get("research_per_day"), 6)),
            research_results=int(_num(ambient_raw.get("research_results"), 5)),
            context_enabled=_bool(ambient_raw.get("context_enabled"), True),
            context_hours=_clamped(ambient_raw.get("context_hours"), 6, 1, 168),
            context_topics=_clamped(ambient_raw.get("context_topics"), 2, 1, 9),
            context_results=_clamped(ambient_raw.get("context_results"), 4, 1, 8),
        ),
        guard=GuardConfig(
            enabled=_bool(guard_raw.get("enabled"), True),
            model=str(guard_raw.get("model") or "").strip(),
            timeout_seconds=_num_clamped(guard_raw.get("timeout_seconds"), 6.0, 0.5, 60.0),
            max_command_chars=_clamped(guard_raw.get("max_command_chars"), 4000, 200, 100_000),
        ),
        language=language,
        turns=_turn_config(_section(engine, "turns"), _section(engine, "turn")),
        worker=_worker_config(_section(engine, "worker")),
        io=IOConfig(workers=max(1, int(_num(_section(engine, "io").get("workers"), 8)))),
        concurrency=ConcurrencyConfig(
            max_chat_locks=max(
                1, int(_num(_section(engine, "concurrency").get("max_chat_locks"), 256))
            )
        ),
    )
