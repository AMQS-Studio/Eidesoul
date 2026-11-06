"""Policy and memory settings plus the host-level paths and combined config."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .models import WorkerRetryConfig
from .schema import DEFAULT_SENSITIVE_COMMANDS, ConfigError

log = logging.getLogger(__name__)

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
class SafetyConfig:
    """ADR-0033 §5: a language without a pack is refused unless the operator opts in."""

    allow_degraded: bool = False

@dataclass(frozen=True)
class SelfKnowledgeConfig:
    """ADR-0015 §4.8: generated-first self knowledge, on by default, disableable."""

    enabled: bool = True
    capability_index: bool = True
    topics: tuple[str, ...] = (
        "rooms",
        "memory",
        "permissions",
        "initiative",
        "failure",
        "limits",
    )
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
    collection: str = "monoagens_memories"
    extraction_model: str = ""  # empty = llm.model
    telemetry: bool = False
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    worker: CognitionConfig = field(default_factory=CognitionConfig)
    rooms: RoomConfig = field(default_factory=RoomConfig)
    intent: IntentConfig = field(default_factory=IntentConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)

# ADR-0006 §4.5: three tiers, and no way to turn the gate off.
APPROVAL_MODES = ("default", "yolo", "strict")

@dataclass(frozen=True)
class ApprovalConfig:
    """Tiered approval gate; `default` pauses state changes, `yolo` trusts ordinary tools.

    `natural_confirm` lets a plain chat reply ("oke, kirim") stand in for `/approve <id>`;
    `confirm_cross_gateway` and `confirm_tasks` keep the two wide-reaching actions staged even
    when the tier would otherwise run them.
    """

    mode: str = "default"  # default | yolo | strict
    sensitive_commands: tuple[str, ...] = DEFAULT_SENSITIVE_COMMANDS
    settings: bool = True
    ttl_seconds: int = 900
    consent_max_per_hour: int = 20  # self-confirmation anti-spam, not a permission grant
    natural_confirm: bool = True
    confirm_cross_gateway: bool = True
    confirm_tasks: bool = True

    def __post_init__(self) -> None:
        if self.mode in APPROVAL_MODES:
            return
        if self.mode == "off":
            raise ConfigError(
                "approval.mode 'off' is gone: every action needs a tier; use 'yolo' to trust "
                "ordinary tools or 'default' to pause state changes"
            )
        raise ConfigError(
            "approval.mode must be one of: "
            f"{', '.join(APPROVAL_MODES)}; got '{self.mode}'"
        )

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
class GuardConfig:
    """Sandbox guard limits; `model` empty means `engine.llm.model`."""

    enabled: bool = True
    model: str = ""
    timeout_seconds: float = 6.0
    max_command_chars: int = 4000
    credential_patterns: tuple[str, ...] = ()  # ADR-0007 §4.8 additions only

@dataclass(frozen=True)
class LanguageConfig:
    """Optional custom lexicon directory for tests; empty uses the built-in English baseline."""

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
    local: bool = False  # a development tree: the digest pin is not enforced

