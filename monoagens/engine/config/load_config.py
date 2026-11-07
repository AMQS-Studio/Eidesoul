"""`load_config` and the voice/speech cross-checks it runs before boot."""

import logging
from collections.abc import Mapping
from pathlib import Path

import yaml

from ...providers.speech import (
    STT_PROVIDERS,
    TTS_PROVIDERS,
    provider_name,
)
from ..character import lexicon
from ..soul import Soul
from .environment import resolve_database_url
from .host import Config
from .load import (
    _alerts_config,
    _automation_config,
    _capture_config,
    _cognition_config,
    _engine,
    _gateways,
    _group_capture,
    _llm_config,
    _logging_config,
    _reflection_config,
    _roles,
    _skills_config,
    _soul,
    _speech_config,
    _turn_config,
    _worker_config,
    load_host,
)
from .models import (
    ConcurrencyConfig,
    DatabaseConfig,
    IOConfig,
    LLMConfig,
    MetricsConfig,
    ResearchConfig,
    SpeechConfig,
)
from .schema import DEFAULT_SENSITIVE_COMMANDS, ENGINE_SCHEMA, ConfigError
from .sections import (
    _bool,
    _clamped,
    _expand,
    _intent_config,
    _num,
    _num_clamped,
    _read_config_section,
    _redact_mode,
    _retention_config,
    _search_config,
    _section,
    _self_knowledge_config,
    _shell_config,
    _str_tuple,
    _tools_config,
    _unknown_keys,
)
from .settings import (
    APPROVAL_MODES,
    AmbientConfig,
    ApprovalConfig,
    GuardConfig,
    LanguageConfig,
    MemoryConfig,
    PrivacyConfig,
    PromptConfig,
    RoomConfig,
    SafetyConfig,
)

log = logging.getLogger(__name__)

def _validate_voice(llm: LLMConfig, soul: Soul) -> None:
    """Reject a behavior option the selected wire cannot carry (ADR-0003 §4.6)."""
    voice = soul.voice
    supported = set(llm.sampling_supported)
    for key in ("top_p", "top_k", "min_p"):
        value = getattr(voice.sampling, key)
        if value is not None and key not in supported:
            raise ConfigError(
                f"soul.voice.sampling.{key} is not supported by provider '{llm.provider}' "
                f"({llm.wire} wire); remove it or choose a provider that carries it"
            )
    if voice.safety == "off" and not llm.safety_supported:
        raise ConfigError(
            f"soul.voice.safety: off is not supported by provider '{llm.provider}' "
            f"({llm.wire} wire)"
        )

def _validate_speech(speech: SpeechConfig, soul: Soul) -> None:
    """`soul.voice.speech` may only name a provider the engine already configured (P3.4 §2)."""
    chosen = soul.voice.speech
    if not chosen.enabled:
        return
    for kind, wanted_name, configured, known in (
        ("stt", chosen.stt, speech.stt.provider, STT_PROVIDERS),
        ("tts", chosen.tts, speech.tts.provider, TTS_PROVIDERS),
    ):
        wanted = provider_name(wanted_name)
        if not wanted:
            continue
        if wanted not in known:
            raise ConfigError(
                f"soul.voice.speech.{kind} '{wanted}' is not a known provider; "
                f"available: {', '.join(sorted(known))}"
            )
        if not configured:
            raise ConfigError(
                f"soul.voice.speech.{kind} selects '{wanted}' but "
                f"engine.speech.{kind}.provider is not configured"
            )
        if wanted != configured.lower():
            raise ConfigError(
                f"soul.voice.speech.{kind}='{wanted}' does not match "
                f"engine.speech.{kind}.provider='{configured}'"
            )

def load_config(
    path: str | Path | None = None,
    *,
    require_key: bool = True,
    require_model: bool = True,
    require_gateway: bool = False,
    engine_override: Mapping | None = None,
    soul_override: Mapping | None = None,
    roles_override: Mapping | None = None,
) -> Config:
    host, paths = load_host(path)
    try:
        raw = yaml.safe_load(paths.config.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"config YAML is invalid: {exc}") from exc
    raw = raw if isinstance(raw, dict) else {}
    engine = _engine(raw, engine_override)
    legacy = sorted({"affect", "style", "character", "humanize"} & set(engine))
    if legacy:
        raise ConfigError(
            "engine." + legacy[0] + " moved to soul.voice; remove the engine voice section"
        )
    if "turn" in engine:
        raise ConfigError(
            f"engine.turn moved to engine.turns in {paths.config}; move timeout_seconds and "
            "min_call_seconds into the engine.turns section"
        )
    if "temperature" in _section(engine, "llm"):
        raise ConfigError(
            "engine.llm.temperature moved to soul.voice.sampling (daily/professional)"
        )
    _unknown_keys(engine, ENGINE_SCHEMA, "engine", paths.config)
    llm = _llm_config(
        _section(engine, "llm"), require_key=require_key, require_model=require_model
    )
    default_role = str(engine.get("default_role") or "").strip()
    if not default_role:
        raise ConfigError("engine.default_role is required")
    roles = _roles(raw, paths, default_role, roles_override)
    gateways = _gateways(raw, required=require_gateway)
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
        max_overflow=database.max_overflow,
        pool_timeout=database.pool_timeout,
        migrations=database.migrations,
    )

    memory_raw = _section(engine, "memory")
    approval_raw = _section(engine, "approval")
    skills_raw = _section(engine, "skills")
    prompt_raw = _section(engine, "prompt")
    ambient_raw = _section(engine, "ambient")
    guard_raw = _section(engine, "guard")
    language_dir = str(_section(engine, "language").get("lexicon_dir") or "").strip()
    language = LanguageConfig(
        lexicon_dir=_expand(language_dir, paths.config.parent) if language_dir else None
    )
    if language.lexicon_dir is not None:
        lexicon.configure(language.lexicon_dir)

    approval_mode = str(approval_raw.get("mode") or "default").strip().lower()
    if approval_mode not in APPROVAL_MODES:
        if approval_mode == "off":
            raise ConfigError(
                "approval.mode 'off' is gone: every action needs a tier; use 'yolo' to trust "
                "ordinary tools or 'default' to pause state changes"
            )
        raise ConfigError(
            "approval.mode must be one of: "
            f"{', '.join(APPROVAL_MODES)}; got '{approval_mode}'"
        )
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
    research_raw = _section(engine, "research")
    research_mode = str(research_raw.get("mode") or "auto").strip().lower()
    if research_mode not in {"off", "auto", "always"}:
        raise ConfigError("engine.research.mode must be 'off', 'auto', or 'always'")
    research = ResearchConfig(
        mode=research_mode,
        require_citation=_bool(research_raw.get("require_citation"), True),
        freshness_hours=_clamped(research_raw.get("freshness_hours"), 24, 0, 8760),
        max_results=_clamped(research_raw.get("max_results"), 5, 1, 10),
    )
    self_knowledge = _self_knowledge_config(_section(engine, "self_knowledge"))
    safety = SafetyConfig(
        allow_degraded=_bool(_section(engine, "safety").get("allow_degraded"), False)
    )
    soul = _soul(raw, paths, soul_override)
    _validate_voice(llm, soul)
    speech = _speech_config(_section(engine, "speech"))
    _validate_speech(speech, soul)
    two_level_at = int(_num(prompt_raw.get("capability_index_two_level_at"), 150))
    if two_level_at < 0:
        raise ConfigError("engine.prompt.capability_index_two_level_at cannot be negative")
    rooms_raw = _section(memory_raw, "rooms")
    rooms_source = str(rooms_raw.get("source") or "gateway_first").strip().lower()
    if rooms_source not in {"gateway_first", "recorded"}:
        raise ConfigError("memory.rooms.source must be 'gateway_first' or 'recorded'")
    turns = _turn_config(_section(engine, "turns"))
    if turns.timeout_seconds < llm.timeout_seconds:
        log.warning(
            "engine.turns.timeout_seconds (%.0fs) is below engine.llm.timeout_seconds "
            "(%.0fs); one slow model call can be cut off by the turn watchdog",
            turns.timeout_seconds,
            llm.timeout_seconds,
        )

    return Config(
        paths=paths,
        database=database,
        llm=llm,
        workspace=host.workspace,
        approval=ApprovalConfig(
            mode=approval_mode,
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
            natural_confirm=_bool(approval_raw.get("natural_confirm"), True),
            confirm_cross_gateway=_bool(approval_raw.get("confirm_cross_gateway"), True),
            confirm_tasks=_bool(approval_raw.get("confirm_tasks"), True),
        ),
        roles=roles,
        default_role=default_role,
        gateways=gateways,
        soul=soul,
        sandbox=host.sandbox,
        safety=safety,
        mcp=host.mcp,
        alerts=_alerts_config(_section(engine, "alerts")),
        web_search=host.web_search,
        research=research,
        memory=MemoryConfig(
            auto_capture=_bool(memory_raw.get("auto_capture"), True),
            capture=capture,
            max_items=int(_num(memory_raw.get("max_items"), 12)),
            max_chars=int(_num(memory_raw.get("max_chars"), 0)),
            collection=str(memory_raw.get("collection") or "monoagens_memories").strip()
            or "monoagens_memories",
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
            salience=_bool(prompt_raw.get("salience"), False),
            max_chars=int(_num(prompt_raw.get("max_chars"), 0)),
            thread_chars=int(_num(prompt_raw.get("thread_chars"), 0)),
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
        ),
        guard=GuardConfig(
            enabled=_bool(guard_raw.get("enabled"), True),
            model=str(guard_raw.get("model") or "").strip(),
            timeout_seconds=_num_clamped(guard_raw.get("timeout_seconds"), 6.0, 0.5, 60.0),
            max_command_chars=_clamped(guard_raw.get("max_command_chars"), 4000, 200, 100_000),
            credential_patterns=_str_tuple(guard_raw.get("credential_patterns")),
        ),
        language=language,
        turns=turns,
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
        speech=speech,
        automations=_automation_config(_section(engine, "automations")),
        self_knowledge=self_knowledge,
        plugin=host.plugin,
        plugins=host.plugins,
    )
