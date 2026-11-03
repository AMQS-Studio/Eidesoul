"""Load .env plus the combined config.yaml (host, engine, gateways, soul) and roles.yaml.

Validation is strict and never touches the filesystem beyond reading; one file is the
source of truth for the host, and DB documents override its engine/soul/role seeds.
"""


import contextlib
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml

from ..providers import resolve
from .access.roles import RoleError, RoleRegistry, load_roles
from .character import lexicon
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
    max_calls_per_turn: int = 6  # decision + reply loop + polish (ADR-0025 §4.4)
    prompt_cache: bool = True  # two system messages: stable prefix then volatile (ADR-0025 §4.1)
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
class FetchConfig:
    """ADR-0021 §4.3: `fetch` reads the public internet; the SSRF rule lives in code."""

    enabled: bool = True
    max_output_chars: int = 12_000
    timeout_seconds: float = 8.0
    max_redirects: int = 3
    allow_ports: tuple[int, ...] = (80, 443)
    deny_hosts: tuple[str, ...] = ()  # operator additions; never a way to loosen the rule


@dataclass(frozen=True)
class WebSearchConfig:
    base_url: str = ""  # empty = only the ddgs backend
    region: str = ""  # empty = no region bias; never a vendor or country default
    timeout_seconds: float = 10.0
    fetch: FetchConfig = field(default_factory=FetchConfig)


@dataclass(frozen=True)
class GrepConfig:
    """ADR-0021 §4.2: file-content search under the same roots as `read`."""

    max_matches: int = 60
    max_output_chars: int = 4_000
    max_file_bytes: int = 2_000_000


@dataclass(frozen=True)
class ReadConfig:
    grep: GrepConfig = field(default_factory=GrepConfig)


@dataclass(frozen=True)
class ToolsConfig:
    """ADR-0024 §5: validation is not optional; only the error budget is tunable."""

    args: str = "strict"
    max_errors: int = 5
    hot: tuple[str, ...] = ("read", "chat_history", "memory", "info")
    activate_on_demand: bool = True


@dataclass(frozen=True)
class ShellConfig:
    """ADR-0024 §4.6: the child sees a computed environment, never the process one."""

    env: tuple[str, ...] = (
        "PATH",
        "HOME",
        "LANG",
        "TZ",
        "XDG_RUNTIME_DIR",
        "TMPDIR",
        "TERM",
    )


@dataclass(frozen=True)
class SearchConfig:
    """ADR-0026 §5: FTS first, trigram as the second net; scope is unchanged."""

    fts: bool = True
    lang: str = "simple"
    trgm: bool = True
    trgm_threshold: float = 0.3
    max_results: int = 50
    snippet_chars: int = 240


@dataclass(frozen=True)
class LoggingConfig:
    """ADR-0019 §4.1/§5: `text` stays the default, `json` is one object per line."""

    format: str = "text"
    level: str = "INFO"


@dataclass(frozen=True)
class MetricsConfig:
    """ADR-0016 §4.6: `llm_calls` retention, removed by the ADR-0005 job."""

    retention_days: int = 90
    enabled: bool = True
    window_seconds: int = 300


@dataclass(frozen=True)
class AutomationSandboxConfig:
    """ADR-0014 §5: a sandbox step only runs a program below this root."""

    programs_root: str = "workspace"


@dataclass(frozen=True)
class AutomationConditionConfig:
    """ADR-0014 §4.3: natural-language criteria are opt-in per automation."""

    nl_criteria: bool = True
    nl_budget_tokens_per_day: int = 20000


@dataclass(frozen=True)
class AutomationRetryConfig:
    """ADR-0014 §4.13: transient failures only; `permanent` is never retried."""

    max_attempts: int = 3
    backoff_seconds: float = 30.0
    max_backoff_seconds: float = 900.0
    jitter: bool = True
    respect_retry_after: bool = True


@dataclass(frozen=True)
class AutomationResilienceConfig:
    """ADR-0014 §4.10: a missed occurrence is recorded, apologized once, never fired stale."""

    catchup_minutes: int = 15
    max_stale_days: int = 7
    stale_claim_seconds: int = 300
    missed_apology: bool = True
    apology_digest: bool = True
    apology_respect_quiet_hours: bool = True


@dataclass(frozen=True)
class AutomationConfig:
    """ADR-0014 §5, one block: schedule, budget, quiet hours, kill switch, retries."""

    enabled: bool = True
    kill_switch: bool = False
    default_budget_per_day: int = 4
    run_timeout_seconds: int = 120
    max_steps: int = 10
    min_interval_seconds: int = 60
    max_occurrences_per_day: int = 24
    default_quiet_hours: str = "22:00-07:00"
    default_quiet_policy: str = "skip"
    max_active_per_person: int = 10
    sandbox: AutomationSandboxConfig = field(default_factory=AutomationSandboxConfig)
    condition: AutomationConditionConfig = field(
        default_factory=AutomationConditionConfig
    )
    retry: AutomationRetryConfig = field(default_factory=AutomationRetryConfig)
    resilience: AutomationResilienceConfig = field(
        default_factory=AutomationResilienceConfig
    )


JOB_RUN_TIMEOUT_HARD_LIMIT = 300
MIN_INTERVAL_HARD_LIMIT = 60
MAX_OCCURRENCES_HARD_LIMIT = 24
QUIET_POLICIES = ("skip", "defer")


def parse_quiet_hours(value: object) -> tuple[int, int] | None:
    """`"22:00-07:00"` → `(1320, 420)` minutes; `None`/empty disables quiet hours."""
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split("-")
    if len(parts) != 2:
        raise ConfigError("quiet_hours must be 'HH:MM-HH:MM'")
    minutes: list[int] = []
    for part in parts:
        clock = part.strip()
        if len(clock) != 5 or clock[2] != ":":
            raise ConfigError("quiet_hours must be 'HH:MM-HH:MM'")
        try:
            hour, minute = int(clock[:2]), int(clock[3:])
        except ValueError as exc:
            raise ConfigError("quiet_hours must be 'HH:MM-HH:MM'") from exc
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ConfigError("quiet_hours must be a valid clock time")
        minutes.append(hour * 60 + minute)
    if minutes[0] == minutes[1]:
        raise ConfigError("quiet_hours start and end must differ")
    return minutes[0], minutes[1]


@dataclass(frozen=True)
class SelfKnowledgeConfig:
    """ADR-0015 §4.8: generated-first self knowledge, on by default, disableable."""

    enabled: bool = True
    capability_index: bool = True
    topics: tuple[str, ...] = ("ruangan", "ingatan", "izin", "inisiatif", "gagal", "batas")
    coarse_buckets: bool = True
    topology: bool = True
    max_chars_per_topic: int = 1200
    forbidden_token_check: bool = True
    docs: tuple[str, ...] = ("README.md",)
    docs_max_chars: int = 4000


@dataclass(frozen=True)
class GroupCaptureConfig:
    """ADR-0006 §4.2: in a group only the speaker's own statements are captured."""

    self_only: bool = True
    third_party: str = "drop"  # drop | deny
    ask_in_group: bool = False


@dataclass(frozen=True)
class PrivacyConfig:
    """Disclosure policy limits: level 0-4, group vs DM ceilings, optional auditor."""

    enabled: bool = True
    default_level: int = 2
    ask_level: int = 4
    group_max_level: int = 2
    dm_max_level: int = 4
    auditor: bool = True
    group_capture: GroupCaptureConfig = field(default_factory=GroupCaptureConfig)


@dataclass(frozen=True)
class CaptureConfig:
    """ADR-0002 §5: the deterministic gate knobs; no per-language vocabulary."""

    mode: str = "balanced"  # off | conservative | balanced | eager
    min_chars: int = 8
    novelty_threshold: float = 0.88


@dataclass(frozen=True)
class CognitionConfig:
    """ADR-0003 §6 worker C: one queue, retry/backoff, dead-letter, queue cap."""

    tick_seconds: float = 1.0
    concurrency: int = 2
    capture_max_items: int = 20
    consolidation_every_seconds: float = 60.0
    audit_every_seconds: float = 86400.0
    queue_cap: int = 5000
    retry: WorkerRetryConfig = field(default_factory=WorkerRetryConfig)


@dataclass(frozen=True)
class ReflectionConfig:
    """ADR-0010 §5: one scope per pass, bounded writes, hourly by default."""

    enabled: bool = True
    every_minutes: int = 60
    idle_minutes: int = 30
    min_new_rows: int = 25
    min_candidates: int = 1
    scopes_per_pass: int = 1
    items_per_pass: int = 40
    writes_per_pass: int = 20
    seconds_per_pass: float = 60.0
    passes_per_day: int = 24
    derive_depth: int = 1
    dispute_ttl_days: float = 30.0
    questions_per_day: int = 1  # ADR-0010 §4.7: one dispute question per subject per day


@dataclass(frozen=True)
class RoomConfig:
    """ADR-0007 §4.5: the recall ladder's deterministic limits; membership is fail-closed."""

    max_rooms_per_turn: int = 2
    max_messages: int = 20
    require_membership: bool = True
    source: str = "gateway_first"  # gateway_first | recorded


@dataclass(frozen=True)
class IntentConfig:
    """ADR-0005 §4.6: prototype intent hints; a floor and a margin per intent, never a model."""

    remember_enabled: bool = True
    remember_floor: float = 0.62
    remember_margin: float = 0.06
    forget_enabled: bool = True
    forget_floor: float = 0.64
    forget_margin: float = 0.06


@dataclass(frozen=True)
class RetentionConfig:
    """ADR-0005 §4.7: the daily purge runs with a grace window and a bounded batch."""

    purge_grace_days: float = 1.0
    purge_batch: int = 500


@dataclass(frozen=True)
class MemoryConfig:
    auto_capture: bool = True
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    max_items: int = 12
    max_chars: int = 3000
    collection: str = "eidesoul_memories"
    extraction_model: str = ""  # empty = llm.model
    telemetry: bool = False
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    worker: CognitionConfig = field(default_factory=CognitionConfig)
    rooms: RoomConfig = field(default_factory=RoomConfig)
    intent: IntentConfig = field(default_factory=IntentConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)


@dataclass(frozen=True)
class ApprovalConfig:
    mode: str = "yolo"  # yolo | off | strict
    sensitive_tools: tuple[str, ...] = ()
    sensitive_commands: tuple[str, ...] = DEFAULT_SENSITIVE_COMMANDS
    settings: bool = True
    ttl_seconds: int = 900
    consent_max_per_hour: int = 20  # self-confirmation anti-spam, not a permission grant


@dataclass(frozen=True)
class SkillsConfig:
    """ADR-0020 §5: procedures are retrieved, never indexed; unused ones decay."""

    enabled: bool = True
    task_idle_minutes: float = 10.0
    idle_after_send_minutes: float = 3.0
    per_episode: int = 1
    max_chars: int = 800
    max_body_chars: int = 18000
    use_renewal_days: float = 30.0
    # ADR-0020 §4.6/§4.7: the efficiency comparison and the automation ladder.
    efficiency_enabled: bool = True
    efficiency_min_evidence_episodes: int = 2
    efficiency_window_days: float = 30.0
    ladder_enabled: bool = True
    ladder_proposal_cooldown_days: float = 14.0


@dataclass(frozen=True)
class PromptConfig:
    salience: bool = True
    max_chars: int = 9000
    thread_chars: int = 4000
    pinned_blocks: tuple[str, ...] = ("base", "self", "roles", "away")
    capability_index: bool = True
    capability_index_max_chars: int = 1200
    capability_index_two_level_at: int = 150
    prefix_hash: bool = True
    redact_infra: str = "non_administrator"  # non_administrator | never | always


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
    credential_patterns: tuple[str, ...] = ()  # ADR-0007 §4.8 additions only


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
class PluginSettings:
    """ADR-0028 §5: a plugin is off and unpinned until an operator enables it."""

    enabled: bool = False
    digest: str = ""


@dataclass(frozen=True)
class PluginConfig:
    """ADR-0027 §5 / ADR-0028 §5: where fetched plugin code lives and whether we may fetch."""

    root: Path | None = None  # unset = <workspace>/plugins
    fetch: bool = True
    source: str = ""


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
    plugin: PluginConfig = field(default_factory=PluginConfig)
    plugins: dict[str, PluginSettings] = field(default_factory=dict)


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
    reflection: ReflectionConfig = field(default_factory=ReflectionConfig)
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
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    shell: ShellConfig = field(default_factory=ShellConfig)
    read: ReadConfig = field(default_factory=ReadConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    automations: AutomationConfig = field(default_factory=AutomationConfig)
    self_knowledge: SelfKnowledgeConfig = field(default_factory=SelfKnowledgeConfig)
    plugin: PluginConfig = field(default_factory=PluginConfig)
    plugins: dict[str, PluginSettings] = field(default_factory=dict)

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
    with contextlib.suppress(OSError):
        path.parent.chmod(0o700)
    if path.exists():
        with contextlib.suppress(OSError):
            path.chmod(0o600)


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
            str(body.get("url_env") or "EIDESOUL_DATABASE_URL").strip()
            or "EIDESOUL_DATABASE_URL"
        ),
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
            region=str(search_raw.get("region") or "").strip(),
            timeout_seconds=_num(search_raw.get("timeout_seconds"), 10.0),
            fetch=_fetch_config(_section(search_raw, "fetch")),
        ),
        mcp=_mcp_servers(raw.get("mcp")),
        roles_file=roles_file,
        soul_file=soul_file,
        sync_documents=_bool(raw.get("sync_documents"), True),
        plugin=_plugin_config(raw, base, workspace),
        plugins=_plugins_config(raw),
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
    if names and documents is not None:
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
        max_calls_per_turn=max(1, int(_num(llm_raw.get("max_calls_per_turn"), 6))),
        prompt_cache=_bool(llm_raw.get("prompt_cache"), True),
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


def _capture_config(raw: Mapping) -> CaptureConfig:
    mode = str(raw.get("mode") or "balanced").strip().lower()
    if mode not in {"off", "conservative", "balanced", "eager"}:
        mode = "balanced"
    return CaptureConfig(
        mode=mode,
        min_chars=max(1, int(_num(raw.get("min_chars"), 8))),
        novelty_threshold=_num_clamped(raw.get("novelty_threshold"), 0.88, 0.0, 1.0),
    )


def _cognition_config(raw: Mapping) -> CognitionConfig:
    retry = _section(raw, "retry")
    return CognitionConfig(
        tick_seconds=max(0.1, _num(raw.get("tick_seconds"), 1.0)),
        concurrency=_clamped(raw.get("concurrency"), 2, 1, 16),
        capture_max_items=_clamped(raw.get("capture_max_items"), 20, 1, 200),
        consolidation_every_seconds=max(
            1.0, _num(raw.get("consolidation_every_seconds"), 60.0)
        ),
        audit_every_seconds=max(60.0, _num(raw.get("audit_every_seconds"), 86400.0)),
        queue_cap=_clamped(raw.get("queue_cap"), 5000, 1, 1_000_000),
        retry=WorkerRetryConfig(
            attempts=max(1, int(_num(retry.get("attempts"), 5))),
            backoff_seconds=max(0.0, _num(retry.get("backoff_seconds"), 2.0)),
            dead_letter_after=max(1, int(_num(retry.get("dead_letter_after"), 5))),
        ),
    )


def _reflection_config(raw: Mapping) -> ReflectionConfig:
    return ReflectionConfig(
        enabled=_bool(raw.get("enabled"), True),
        every_minutes=_clamped(raw.get("every_minutes"), 60, 1, 1440),
        idle_minutes=_clamped(raw.get("idle_minutes"), 30, 1, 1440),
        min_new_rows=_clamped(raw.get("min_new_rows"), 25, 0, 10000),
        min_candidates=_clamped(raw.get("min_candidates"), 1, 0, 1000),
        scopes_per_pass=_clamped(raw.get("scopes_per_pass"), 1, 1, 32),
        items_per_pass=_clamped(raw.get("items_per_pass"), 40, 1, 1000),
        writes_per_pass=_clamped(raw.get("writes_per_pass"), 20, 0, 1000),
        seconds_per_pass=_num_clamped(raw.get("seconds_per_pass"), 60.0, 1.0, 3600.0),
        passes_per_day=_clamped(raw.get("passes_per_day"), 24, 0, 1440),
        derive_depth=_clamped(raw.get("derive_depth"), 1, 1, 1),
        dispute_ttl_days=_num_clamped(raw.get("dispute_ttl_days"), 30.0, 0.0, 3650.0),
        questions_per_day=_clamped(raw.get("questions_per_day"), 1, 0, 10),
    )


def _skills_config(raw: Mapping) -> SkillsConfig:
    return SkillsConfig(
        enabled=_bool(raw.get("enabled"), True),
        task_idle_minutes=_num_clamped(raw.get("task_idle_minutes"), 10.0, 1.0, 1440.0),
        idle_after_send_minutes=_num_clamped(
            raw.get("idle_after_send_minutes"), 3.0, 0.0, 1440.0
        ),
        per_episode=_clamped(raw.get("per_episode"), 1, 0, 10),
        max_chars=_clamped(raw.get("max_chars"), 800, 0, 20000),
        max_body_chars=_clamped(raw.get("max_body_chars"), 18000, 100, 100000),
        use_renewal_days=_num_clamped(raw.get("use_renewal_days"), 30.0, 0.0, 3650.0),
        efficiency_enabled=_bool(_section(raw, "efficiency").get("enabled"), True),
        efficiency_min_evidence_episodes=_clamped(
            _section(raw, "efficiency").get("min_evidence_episodes"), 2, 1, 50
        ),
        efficiency_window_days=_num_clamped(
            _section(raw, "efficiency").get("window_days"), 30.0, 1.0, 3650.0
        ),
        ladder_enabled=_bool(_section(raw, "ladder").get("enabled"), True),
        ladder_proposal_cooldown_days=_num_clamped(
            _section(raw, "ladder").get("proposal_cooldown_days"), 14.0, 0.0, 3650.0
        ),
    )


def _group_capture(raw: Mapping) -> GroupCaptureConfig:
    third = str(raw.get("third_party") or "drop").strip().lower()
    if third not in {"drop", "deny"}:
        third = "drop"
    return GroupCaptureConfig(
        self_only=_bool(raw.get("self_only"), True),
        third_party=third,
        ask_in_group=_bool(raw.get("ask_in_group"), False),
    )


def _logging_config(raw: Mapping) -> LoggingConfig:
    fmt = str(raw.get("format") or "text").strip().lower()
    if fmt not in ("text", "json"):
        raise ConfigError("engine.logging.format must be 'text' or 'json'")
    level = str(raw.get("level") or "INFO").strip().upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ConfigError("engine.logging.level must be a standard logging level")
    return LoggingConfig(format=fmt, level=level)


def _automation_config(raw: Mapping) -> AutomationConfig:
    """ADR-0014 §5; a hard limit is a boot failure, never a silent truncation."""
    sandbox_raw = _section(raw, "sandbox")
    condition_raw = _section(raw, "condition")
    retry_raw = _section(raw, "retry")
    resilience_raw = _section(raw, "resilience")
    timeout = _clamped(raw.get("run_timeout_seconds"), 120, 1, JOB_RUN_TIMEOUT_HARD_LIMIT)
    provided_timeout = raw.get("run_timeout_seconds")
    if (
        provided_timeout is not None
        and _num(provided_timeout, timeout) > JOB_RUN_TIMEOUT_HARD_LIMIT
    ):
        raise ConfigError(
            f"engine.automations.run_timeout_seconds must be <= {JOB_RUN_TIMEOUT_HARD_LIMIT}"
        )
    interval = _clamped(raw.get("min_interval_seconds"), 60, 1, 86400)
    provided_interval = raw.get("min_interval_seconds")
    if (
        provided_interval is not None
        and _num(provided_interval, interval) < MIN_INTERVAL_HARD_LIMIT
    ):
        raise ConfigError(
            f"engine.automations.min_interval_seconds must be >= {MIN_INTERVAL_HARD_LIMIT}"
        )
    occurrences = _clamped(
        raw.get("max_occurrences_per_day"), 24, 1, MAX_OCCURRENCES_HARD_LIMIT
    )
    provided_occurrences = raw.get("max_occurrences_per_day")
    if (
        provided_occurrences is not None
        and _num(provided_occurrences, occurrences) > MAX_OCCURRENCES_HARD_LIMIT
    ):
        raise ConfigError(
            "engine.automations.max_occurrences_per_day must be <= "
            f"{MAX_OCCURRENCES_HARD_LIMIT}"
        )
    quiet = str(raw.get("default_quiet_hours") or "22:00-07:00").strip()
    parse_quiet_hours(quiet)
    policy = str(raw.get("default_quiet_policy") or "skip").strip().lower()
    if policy not in QUIET_POLICIES:
        raise ConfigError("engine.automations.default_quiet_policy must be 'skip' or 'defer'")
    programs_root = str(sandbox_raw.get("programs_root") or "workspace").strip() or "workspace"
    return AutomationConfig(
        enabled=_bool(raw.get("enabled"), True),
        kill_switch=_bool(raw.get("kill_switch"), False),
        default_budget_per_day=_clamped(raw.get("default_budget_per_day"), 4, 1, 24),
        run_timeout_seconds=timeout,
        max_steps=_clamped(raw.get("max_steps"), 10, 1, 50),
        min_interval_seconds=interval,
        max_occurrences_per_day=occurrences,
        default_quiet_hours=quiet,
        default_quiet_policy=policy,
        max_active_per_person=_clamped(raw.get("max_active_per_person"), 10, 0, 1000),
        sandbox=AutomationSandboxConfig(programs_root=programs_root),
        condition=AutomationConditionConfig(
            nl_criteria=_bool(condition_raw.get("nl_criteria"), True),
            nl_budget_tokens_per_day=_clamped(
                condition_raw.get("nl_budget_tokens_per_day"), 20000, 0, 1_000_000
            ),
        ),
        retry=AutomationRetryConfig(
            max_attempts=_clamped(retry_raw.get("max_attempts"), 3, 1, 20),
            backoff_seconds=_num_clamped(retry_raw.get("backoff_seconds"), 30.0, 0.0, 86400.0),
            max_backoff_seconds=_num_clamped(
                retry_raw.get("max_backoff_seconds"), 900.0, 0.0, 86400.0
            ),
            jitter=_bool(retry_raw.get("jitter"), True),
            respect_retry_after=_bool(retry_raw.get("respect_retry_after"), True),
        ),
        resilience=AutomationResilienceConfig(
            catchup_minutes=_clamped(resilience_raw.get("catchup_minutes"), 15, 0, 1440),
            max_stale_days=_clamped(resilience_raw.get("max_stale_days"), 7, 0, 3650),
            stale_claim_seconds=_clamped(
                resilience_raw.get("stale_claim_seconds"), 300, 10, 86400
            ),
            missed_apology=_bool(resilience_raw.get("missed_apology"), True),
            apology_digest=_bool(resilience_raw.get("apology_digest"), True),
            apology_respect_quiet_hours=_bool(
                resilience_raw.get("apology_respect_quiet_hours"), True
            ),
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
        group_capture=_group_capture(_section(privacy_raw, "group_capture")),
    )
    capture = _capture_config(_section(memory_raw, "capture"))
    cognition = _cognition_config(_section(memory_raw, "worker"))
    reflection = _reflection_config(_section(engine, "reflection"))
    skills = _skills_config(skills_raw)
    tools_config = _tools_config(engine)
    shell_config = _shell_config(engine)
    search_config = _search_config(_section(engine, "search"))
    self_knowledge = _self_knowledge_config(_section(engine, "self_knowledge"))
    two_level_at = int(_num(prompt_raw.get("capability_index_two_level_at"), 150))
    if two_level_at < 0:
        raise ConfigError("engine.prompt.capability_index_two_level_at cannot be negative")
    rooms_raw = _section(memory_raw, "rooms")
    rooms_source = str(rooms_raw.get("source") or "gateway_first").strip().lower()
    if rooms_source not in {"gateway_first", "recorded"}:
        raise ConfigError("memory.rooms.source must be 'gateway_first' or 'recorded'")

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
            consent_max_per_hour=_clamped(
                approval_raw.get("consent_max_per_hour"), 20, 0, 1000
            ),
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
            capture=capture,
            max_items=int(_num(memory_raw.get("max_items"), 12)),
            max_chars=int(_num(memory_raw.get("max_chars"), 3000)),
            collection=str(memory_raw.get("collection") or "eidesoul_memories").strip()
            or "eidesoul_memories",
            extraction_model=str(memory_raw.get("extraction_model") or "").strip(),
            telemetry=_bool(memory_raw.get("telemetry"), False),
            privacy=privacy,
            worker=cognition,
            rooms=RoomConfig(
                max_rooms_per_turn=_clamped(
                    rooms_raw.get("max_rooms_per_turn"), 2, 0, 20
                ),
                max_messages=_clamped(rooms_raw.get("max_messages"), 20, 1, 200),
                require_membership=_bool(rooms_raw.get("require_membership"), True),
                source=rooms_source,
            ),
            intent=_intent_config(_section(memory_raw, "intent")),
            retention=_retention_config(_section(memory_raw, "retention")),
        ),
        skills=skills,
        reflection=reflection,
        prompt=PromptConfig(
            salience=_bool(prompt_raw.get("salience"), True),
            max_chars=int(_num(prompt_raw.get("max_chars"), 9000)),
            thread_chars=int(_num(prompt_raw.get("thread_chars"), 4000)),
            pinned_blocks=_str_tuple(prompt_raw.get("pinned_blocks"))
            or PromptConfig().pinned_blocks,
            capability_index=_bool(prompt_raw.get("capability_index"), True),
            capability_index_max_chars=int(
                _num(prompt_raw.get("capability_index_max_chars"), 1200)
            ),
            capability_index_two_level_at=two_level_at,
            prefix_hash=_bool(prompt_raw.get("prefix_hash"), True),
            redact_infra=_redact_mode(_section(prompt_raw, "self_block")),
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
            credential_patterns=_str_tuple(guard_raw.get("credential_patterns")),
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
        tools=tools_config,
        shell=shell_config,
        read=_read_config_section(_section(raw, "read")),
        search=search_config,
        metrics=MetricsConfig(
            retention_days=_clamped(
                _section(engine, "metrics").get("retention_days"), 90, 1, 3650
            ),
            enabled=_bool(_section(engine, "metrics").get("enabled"), True),
            window_seconds=_clamped(
                _section(engine, "metrics").get("window_seconds"), 300, 5, 86400
            ),
        ),
        logging=_logging_config(_section(engine, "logging")),
        automations=_automation_config(_section(engine, "automations")),
        self_knowledge=self_knowledge,
        plugin=host.plugin,
        plugins=host.plugins,
    )
