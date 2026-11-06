"""`load_host` and the per-section readers that build it."""

import logging
import os
from collections.abc import Mapping
from pathlib import Path

import yaml

from ...providers import resolve
from ...providers.speech import (
    STT_DEFAULTS,
    TTS_DEFAULTS,
    SpeechDefaults,
    provider_name,
)
from ..access.roles import RoleError, RoleRegistry, load_roles
from ..soul import Soul, load_soul
from .host import HostConfig, Paths
from .models import (
    JOB_RUN_TIMEOUT_HARD_LIMIT,
    MAX_OCCURRENCES_HARD_LIMIT,
    MIN_INTERVAL_HARD_LIMIT,
    QUIET_POLICIES,
    SPEECH_TIMEOUT_DEFAULT,
    SPEECH_TIMEOUT_HARD_LIMIT,
    AutomationConditionConfig,
    AutomationConfig,
    AutomationResilienceConfig,
    AutomationRetryConfig,
    AutomationSandboxConfig,
    LLMConfig,
    LoggingConfig,
    NoticeRetryConfig,
    ReaperConfig,
    ResumeNoticeConfig,
    RetryConfig,
    ShutdownConfig,
    SpeechConfig,
    STTSpeechConfig,
    TTSSpeechConfig,
    TurnConfig,
    WorkerConfig,
    WorkerRetryConfig,
)
from .schema import ConfigError
from .sections import (
    _bool,
    _clamped,
    _database,
    _expand,
    _mcp_servers,
    _num,
    _num_clamped,
    _plugin_config,
    _plugins_config,
    _sandbox,
    _section,
    _web_search_config,
    _yaml_file,
)
from .settings import (
    CaptureConfig,
    CognitionConfig,
    GatewaySettings,
    GroupCaptureConfig,
    ReflectionConfig,
    SkillsConfig,
    parse_quiet_hours,
)

log = logging.getLogger(__name__)

def load_host(path: str | Path | None = None) -> tuple[HostConfig, Paths]:
    """Read config.yaml without creating directories or touching the DB."""
    target = Path(path or os.environ.get("MONOAGENS_CONFIG") or "config.yaml")
    if not target.is_file():
        raise ConfigError(f"config not found: {target} (run `monoagens init`)")
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"config YAML is invalid: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config is empty or not a mapping: {target}")
    base = target.resolve().parent
    workspace = _expand(str(raw.get("workspace") or ".monoagens/workspace"), base)
    roles_file = _expand(str(raw.get("roles_file") or "roles.yaml"), base)
    soul_file = _expand(str(raw.get("soul_file") or "soul.yaml"), base)
    search_raw = _section(raw, "web_search")
    host = HostConfig(
        database=_database(raw),
        workspace=workspace,
        sandbox=_sandbox(raw, base),
        web_search=_web_search_config(search_raw),
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

def _engine(raw: Mapping, override: Mapping | None = None) -> dict:
    """ADR-0046 §4.1: the `engine` section of config.yaml is authoritative."""
    body = override if override is not None else _section(raw, "engine")
    if not isinstance(body, Mapping):
        raise ConfigError("config.yaml: 'engine' must be a mapping")
    return dict(body)

def _soul(raw: Mapping, paths: Paths, override: Mapping | None = None) -> Soul:
    """ADR-0046 §4.1: soul.yaml is authoritative; config.yaml carries no soul keys."""
    source: Path | Mapping[str, object] = override if override is not None else paths.soul_yaml
    try:
        return load_soul(source, source_path=paths.soul_yaml)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

def _roles(
    raw: Mapping, paths: Paths, default_role: str, override: Mapping | None = None
) -> RoleRegistry:
    """ADR-0046 §4.1: roles.yaml holds the definitions and the membership list."""
    source: Mapping[str, str | dict] = override if override is not None else _roles_file(paths)
    if not source:
        raise ConfigError(f"no roles found: {paths.roles} (run `monoagens init`)")
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
            raise ConfigError(
                "config.yaml is missing the 'gateways' section (run `monoagens init`)"
            )
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

def _strings(value: object) -> tuple[str, ...]:
    """A YAML list (or single string) of non-empty names, in declaration order."""
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in items if str(item).strip())


def _llm_config(
    llm_raw: Mapping, *, require_key: bool, require_model: bool
) -> LLMConfig:
    """The `engine.llm` block: a provider name is required, never defaulted in code."""
    provider = str(llm_raw.get("provider") or "").strip()
    if not provider:
        raise ConfigError(
            "engine.llm.provider is required; choose one with `monoagens init --provider <name>` "
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
            "engine.llm.model is required; list ids with `monoagens models` and set one with "
            "`monoagens init --model <id>` or by editing config.yaml"
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
        utility_temperature=_num(llm_raw.get("utility_temperature"), 0.2),
        wire=defaults.wire,
        native=defaults.wire != "openai",
        models_native=defaults.wire != "openai",
        sampling_supported=(
            _strings(llm_raw.get("sampling_supported"))
            if "sampling_supported" in llm_raw
            else defaults.sampling_supported
        ),
        safety_supported=defaults.safety_supported,
        run_budget_seconds=int(_num(llm_raw.get("run_budget_seconds"), 240)),
        max_calls_per_turn=max(1, int(_num(llm_raw.get("max_calls_per_turn"), 6))),
        prompt_cache=_bool(llm_raw.get("prompt_cache"), True),
        retry=_retry_config(_section(llm_raw, "retry")),
    )

BACKLOG_POLICIES = frozenset(
    {"per_message", "per_message_then_consolidate", "consolidate", "last_only"}
)
PARTIAL_DELIVERY = frozenset({"skip", "resend"})

def _turn_config(raw: Mapping) -> TurnConfig:
    """`engine.turns`: the one-turn deadline, resume policy, and the shutdown budget."""
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
        timeout_seconds=max(1.0, _num(raw.get("timeout_seconds"), 300.0)),
        min_call_seconds=max(0.0, _num(raw.get("min_call_seconds"), 5.0)),
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

def _speech_record(
    provider: str, defaults: Mapping[str, SpeechDefaults], kind: str
) -> SpeechDefaults | None:
    """A named provider must exist; its endpoint facts stay in the provider record."""
    if not provider:
        return None
    record = defaults.get(provider)
    if record is None:
        known = ", ".join(sorted(defaults))
        raise ConfigError(
            f"engine.speech.{kind}.provider '{provider}' is not a known provider; "
            f"available: {known}"
        )
    return record

def _speech_stt(raw: Mapping) -> STTSpeechConfig:
    provider = provider_name(raw.get("provider"))
    record = _speech_record(provider, STT_DEFAULTS, "stt")
    base_url = str(raw.get("base_url") or "").strip()
    if record is not None and not base_url and not record.base_url:
        raise ConfigError(
            f"engine.speech.stt.base_url is required for the local provider '{provider}'"
        )
    return STTSpeechConfig(
        provider=provider,
        base_url=base_url,
        api_key_env=str(raw.get("api_key_env") or "").strip()
        or (record.api_key_env if record is not None else ""),
        model=str(raw.get("model") or "").strip()
        or (record.model if record is not None else ""),
        language=str(raw.get("language") or "").strip(),
    )

def _speech_tts(raw: Mapping) -> TTSSpeechConfig:
    provider = provider_name(raw.get("provider"))
    record = _speech_record(provider, TTS_DEFAULTS, "tts")
    base_url = str(raw.get("base_url") or "").strip()
    if record is not None and not base_url and not record.base_url:
        raise ConfigError(
            f"engine.speech.tts.base_url is required for the local provider '{provider}'"
        )
    return TTSSpeechConfig(
        provider=provider,
        base_url=base_url,
        api_key_env=str(raw.get("api_key_env") or "").strip()
        or (record.api_key_env if record is not None else ""),
        model=str(raw.get("model") or "").strip()
        or (record.model if record is not None else ""),
        voice=str(raw.get("voice") or "").strip(),
    )

def _speech_config(raw: Mapping) -> SpeechConfig:
    """P3.4 §2: strict endpoint names, a hard timeout, and no silent provider fallback."""
    return SpeechConfig(
        stt=_speech_stt(_section(raw, "stt")),
        tts=_speech_tts(_section(raw, "tts")),
        timeout_seconds=_num_clamped(
            raw.get("timeout_seconds"), SPEECH_TIMEOUT_DEFAULT, 1.0, SPEECH_TIMEOUT_HARD_LIMIT
        ),
    )

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

