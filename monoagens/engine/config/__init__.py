"""Load .env plus the combined config.yaml (host, engine, gateways, soul) and roles.yaml.

Validation is strict and never touches the filesystem beyond reading. ADR-0002 §4.1: the
operator files are authoritative; the DB documents are a derived cache the loader never reads.

The dataclasses, the engine schema, the section readers, and the two entry points live in
sibling modules; this module is the public surface the rest of the engine imports.
"""

from .environment import drop_session as drop_session
from .environment import load_env as load_env
from .environment import resolve_database_url as resolve_database_url
from .environment import secure_db as secure_db
from .host import Config as Config
from .host import HostConfig as HostConfig
from .host import Paths as Paths
from .load import BACKLOG_POLICIES as BACKLOG_POLICIES
from .load import PARTIAL_DELIVERY as PARTIAL_DELIVERY
from .load import load_host as load_host
from .load_config import load_config as load_config
from .models import JOB_RUN_TIMEOUT_HARD_LIMIT as JOB_RUN_TIMEOUT_HARD_LIMIT
from .models import MAX_OCCURRENCES_HARD_LIMIT as MAX_OCCURRENCES_HARD_LIMIT
from .models import MIN_INTERVAL_HARD_LIMIT as MIN_INTERVAL_HARD_LIMIT
from .models import QUIET_POLICIES as QUIET_POLICIES
from .models import SPEECH_TIMEOUT_DEFAULT as SPEECH_TIMEOUT_DEFAULT
from .models import SPEECH_TIMEOUT_HARD_LIMIT as SPEECH_TIMEOUT_HARD_LIMIT
from .models import WEB_SEARCH_PROVIDERS as WEB_SEARCH_PROVIDERS
from .models import AlertsConfig as AlertsConfig
from .models import AlertSourcesConfig as AlertSourcesConfig
from .models import AutomationConditionConfig as AutomationConditionConfig
from .models import AutomationConfig as AutomationConfig
from .models import AutomationResilienceConfig as AutomationResilienceConfig
from .models import AutomationRetryConfig as AutomationRetryConfig
from .models import AutomationSandboxConfig as AutomationSandboxConfig
from .models import ConcurrencyConfig as ConcurrencyConfig
from .models import DatabaseConfig as DatabaseConfig
from .models import FetchConfig as FetchConfig
from .models import GrepConfig as GrepConfig
from .models import IOConfig as IOConfig
from .models import LLMConfig as LLMConfig
from .models import LLMTargetConfig as LLMTargetConfig
from .models import LoggingConfig as LoggingConfig
from .models import MetricsConfig as MetricsConfig
from .models import MigrationsConfig as MigrationsConfig
from .models import NoticeRetryConfig as NoticeRetryConfig
from .models import ReadConfig as ReadConfig
from .models import ReaperConfig as ReaperConfig
from .models import ResearchConfig as ResearchConfig
from .models import ResumeNoticeConfig as ResumeNoticeConfig
from .models import RetryConfig as RetryConfig
from .models import SandboxConfig as SandboxConfig
from .models import SearchConfig as SearchConfig
from .models import ShellConfig as ShellConfig
from .models import ShutdownConfig as ShutdownConfig
from .models import SpeechConfig as SpeechConfig
from .models import STTSpeechConfig as STTSpeechConfig
from .models import ToolsConfig as ToolsConfig
from .models import TTSSpeechConfig as TTSSpeechConfig
from .models import TurnConfig as TurnConfig
from .models import WebSearchConfig as WebSearchConfig
from .models import WorkerConfig as WorkerConfig
from .models import WorkerRetryConfig as WorkerRetryConfig
from .schema import DEFAULT_SENSITIVE_COMMANDS as DEFAULT_SENSITIVE_COMMANDS
from .schema import ENGINE_SCHEMA as ENGINE_SCHEMA
from .schema import ConfigError as ConfigError
from .sections import REDACT_MODES as REDACT_MODES
from .settings import APPROVAL_MODES as APPROVAL_MODES
from .settings import AmbientConfig as AmbientConfig
from .settings import ApprovalConfig as ApprovalConfig
from .settings import CaptureConfig as CaptureConfig
from .settings import CognitionConfig as CognitionConfig
from .settings import GatewaySettings as GatewaySettings
from .settings import GroupCaptureConfig as GroupCaptureConfig
from .settings import GuardConfig as GuardConfig
from .settings import IntentConfig as IntentConfig
from .settings import LanguageConfig as LanguageConfig
from .settings import MCPServerConfig as MCPServerConfig
from .settings import MemoryConfig as MemoryConfig
from .settings import PluginConfig as PluginConfig
from .settings import PluginSettings as PluginSettings
from .settings import PrivacyConfig as PrivacyConfig
from .settings import PromptConfig as PromptConfig
from .settings import ReflectionConfig as ReflectionConfig
from .settings import RetentionConfig as RetentionConfig
from .settings import RoomConfig as RoomConfig
from .settings import SafetyConfig as SafetyConfig
from .settings import SelfKnowledgeConfig as SelfKnowledgeConfig
from .settings import SkillsConfig as SkillsConfig
from .settings import parse_quiet_hours as parse_quiet_hours

__all__ = [
    "APPROVAL_MODES",
    "BACKLOG_POLICIES",
    "DEFAULT_SENSITIVE_COMMANDS",
    "ENGINE_SCHEMA",
    "JOB_RUN_TIMEOUT_HARD_LIMIT",
    "MAX_OCCURRENCES_HARD_LIMIT",
    "MIN_INTERVAL_HARD_LIMIT",
    "PARTIAL_DELIVERY",
    "QUIET_POLICIES",
    "REDACT_MODES",
    "SPEECH_TIMEOUT_DEFAULT",
    "SPEECH_TIMEOUT_HARD_LIMIT",
    "WEB_SEARCH_PROVIDERS",
    "AlertSourcesConfig",
    "AlertsConfig",
    "AmbientConfig",
    "ApprovalConfig",
    "AutomationConditionConfig",
    "AutomationConfig",
    "AutomationResilienceConfig",
    "AutomationRetryConfig",
    "AutomationSandboxConfig",
    "CaptureConfig",
    "CognitionConfig",
    "ConcurrencyConfig",
    "Config",
    "ConfigError",
    "DatabaseConfig",
    "FetchConfig",
    "GatewaySettings",
    "GrepConfig",
    "GroupCaptureConfig",
    "GuardConfig",
    "HostConfig",
    "IOConfig",
    "IntentConfig",
    "LLMConfig",
    "LLMTargetConfig",
    "LanguageConfig",
    "LoggingConfig",
    "MCPServerConfig",
    "MemoryConfig",
    "MetricsConfig",
    "MigrationsConfig",
    "NoticeRetryConfig",
    "Paths",
    "PluginConfig",
    "PluginSettings",
    "PrivacyConfig",
    "PromptConfig",
    "ReadConfig",
    "ReaperConfig",
    "ReflectionConfig",
    "ResearchConfig",
    "ResumeNoticeConfig",
    "RetentionConfig",
    "RetryConfig",
    "RoomConfig",
    "STTSpeechConfig",
    "SafetyConfig",
    "SandboxConfig",
    "SearchConfig",
    "SelfKnowledgeConfig",
    "ShellConfig",
    "ShutdownConfig",
    "SkillsConfig",
    "SpeechConfig",
    "TTSSpeechConfig",
    "ToolsConfig",
    "TurnConfig",
    "WebSearchConfig",
    "WorkerConfig",
    "WorkerRetryConfig",
    "drop_session",
    "load_config",
    "load_env",
    "load_host",
    "parse_quiet_hours",
    "resolve_database_url",
    "secure_db",
]
