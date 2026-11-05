"""`Paths`, `HostConfig`, and the combined `Config` the engine boots with."""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..access.roles import RoleRegistry
from ..soul import Soul
from .environment import resolve_database_url
from .models import (
    AutomationConfig,
    ConcurrencyConfig,
    DatabaseConfig,
    IOConfig,
    LLMConfig,
    LoggingConfig,
    MetricsConfig,
    ReadConfig,
    ResearchConfig,
    SandboxConfig,
    SearchConfig,
    ShellConfig,
    SpeechConfig,
    ToolsConfig,
    TurnConfig,
    WebSearchConfig,
    WorkerConfig,
)
from .settings import (
    AmbientConfig,
    ApprovalConfig,
    GatewaySettings,
    GuardConfig,
    LanguageConfig,
    MCPServerConfig,
    MemoryConfig,
    PluginConfig,
    PluginSettings,
    PromptConfig,
    ReflectionConfig,
    SafetyConfig,
    SelfKnowledgeConfig,
    SkillsConfig,
)

log = logging.getLogger(__name__)

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
    research: ResearchConfig = field(default_factory=ResearchConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    reflection: ReflectionConfig = field(default_factory=ReflectionConfig)
    prompt: PromptConfig = field(default_factory=PromptConfig)
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
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    speech: SpeechConfig = field(default_factory=SpeechConfig)
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

