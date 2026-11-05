"""Runtime knobs for turns, workers, the model, tools, speech, and automation."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from ...providers.speech import (
    SpeechTarget,
    STTProvider,
    TTSProvider,
    build_stt,
    build_tts,
)

log = logging.getLogger(__name__)

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
    # The outer deadline must outlast `llm.run_budget_seconds`; otherwise a slow provider is
    # cut off before the inner budget can end the turn with a controlled notice.
    timeout_seconds: float = 300.0
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
    utility_temperature: float = 0.2
    wire: str = "openai"
    native: bool = False
    models_native: bool = False
    sampling_supported: tuple[str, ...] = ("temperature", "top_p")
    safety_supported: bool = False
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
    url_env: str = "MONOAGENS_DATABASE_URL"
    pool_size: int = 5
    max_overflow: int = 0
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

# ADR-0004 §4.5: the providers the websearch adapters can speak; anything else is a boot typo.
WEB_SEARCH_PROVIDERS = frozenset(
    {"ddgs", "searxng", "tavily", "brave", "exa", "serper", "google", "off"}
)

@dataclass(frozen=True)
class WebSearchConfig:
    """ADR-0004 §4.5: one provider, an ordered fallback chain; keys stay in the environment."""

    provider: str = "ddgs"  # off disables search entirely; ddgs is the only keyless option
    fallbacks: tuple[str, ...] = ()  # tried in order after `provider`
    base_url: str = ""  # SearXNG JSON endpoint; empty means that provider cannot run
    api_key_env: str = ""  # env var holding the key, never the key itself
    engine_id_env: str = ""  # env var holding the Programmable Search engine id
    ddgs_backend: str = "auto"  # `auto` lets the library pick; never a hardcoded engine
    region: str = ""  # empty = no region bias; never a vendor or country default
    timeout_seconds: float = 10.0
    fetch: FetchConfig = field(default_factory=FetchConfig)

@dataclass(frozen=True)
class ResearchConfig:
    """ADR-0043: model prior for stable facts, websearch when evidence is required."""

    mode: str = "auto"  # off | auto | always
    require_citation: bool = True
    freshness_hours: int = 24
    max_results: int = 5

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
    hot: tuple[str, ...] = ("read", "thread_history", "memory", "info")
    activate_on_demand: bool = True

@dataclass(frozen=True)
class ShellConfig:
    """ADR-0024 §4.6: the child sees a computed environment, never the process one."""

    env: tuple[str, ...] = (
        "PATH",
        "HOME",
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

SPEECH_TIMEOUT_DEFAULT = 30.0
SPEECH_TIMEOUT_HARD_LIMIT = 600.0

@dataclass(frozen=True)
class STTSpeechConfig:
    """One speech-to-text choice; the provider record owns every brand fact."""

    provider: str = ""
    base_url: str = ""
    api_key_env: str = ""
    model: str = ""
    language: str = ""

    def target(self, *, timeout_seconds: float = SPEECH_TIMEOUT_DEFAULT) -> SpeechTarget:
        return SpeechTarget(
            provider=self.provider,
            base_url=self.base_url,
            api_key_env=self.api_key_env,
            model=self.model,
            language=self.language,
            timeout_seconds=timeout_seconds,
        )

@dataclass(frozen=True)
class TTSSpeechConfig:
    """One text-to-speech choice; the voice is the brand's own selector, never a default."""

    provider: str = ""
    base_url: str = ""
    api_key_env: str = ""
    model: str = ""
    voice: str = ""

    def target(self, *, timeout_seconds: float = SPEECH_TIMEOUT_DEFAULT) -> SpeechTarget:
        return SpeechTarget(
            provider=self.provider,
            base_url=self.base_url,
            api_key_env=self.api_key_env,
            model=self.model,
            voice=self.voice,
            timeout_seconds=timeout_seconds,
        )

@dataclass(frozen=True)
class SpeechConfig:
    """P3.4 §2: the provider-agnostic speech endpoints, selected by `soul.voice.speech`.

    Nothing in the turn loop consumes this yet; a body opens `stt_provider()`/`tts_provider()`
    and moves audio across the seam. An empty provider means that direction is not configured.
    """

    stt: STTSpeechConfig = field(default_factory=STTSpeechConfig)
    tts: TTSSpeechConfig = field(default_factory=TTSSpeechConfig)
    timeout_seconds: float = SPEECH_TIMEOUT_DEFAULT

    @property
    def enabled(self) -> bool:
        return bool(self.stt.provider or self.tts.provider)

    def stt_provider(self) -> STTProvider:
        """Build the STT adapter; a missing key or a local endpoint is a `ProviderError`."""
        return build_stt(self.stt.target(timeout_seconds=self.timeout_seconds))

    def tts_provider(self) -> TTSProvider:
        """Build the TTS adapter; a missing key or a local endpoint is a `ProviderError`."""
        return build_tts(self.tts.target(timeout_seconds=self.timeout_seconds))

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

