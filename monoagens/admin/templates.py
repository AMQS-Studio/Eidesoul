"""Default text for `monoagens init`; one source of truth, no separate example files.

The values are derived from the dataclass tree in `monoagens.engine.config` and
`monoagens.engine.soul`, and gateway blocks from each gateway's own settings dataclass
(ADR-0002 §4.6, ADR-0002 §4.4). Only the operator surface — which key the file shows and in
which order — is declared here, so a default can never drift between code and template.
"""


import copy
import importlib
from collections.abc import Callable, Mapping
from dataclasses import MISSING, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from ..engine.access.roles import Role
from ..engine.config import (
    ENGINE_SCHEMA,
    WEB_SEARCH_PROVIDERS,
    AlertsConfig,
    AmbientConfig,
    ApprovalConfig,
    AutomationConfig,
    ConcurrencyConfig,
    DatabaseConfig,
    GuardConfig,
    HostConfig,
    IOConfig,
    LLMConfig,
    LoggingConfig,
    MemoryConfig,
    MetricsConfig,
    PromptConfig,
    ReflectionConfig,
    ResearchConfig,
    SafetyConfig,
    SandboxConfig,
    SearchConfig,
    SelfKnowledgeConfig,
    ShellConfig,
    SkillsConfig,
    SpeechConfig,
    ToolsConfig,
    TurnConfig,
    WebSearchConfig,
    WorkerConfig,
)
from ..engine.soul import DEFAULT_SOUL_YAML, VoiceConfig
from ..providers import PROVIDERS


def _tree(value: object) -> Any:
    """A dataclass instance as plain YAML values; tuples become lists, whole floats ints."""
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _tree(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        return {str(key): _tree(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_tree(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _prune(tree: object, schema: object) -> dict:
    """Keep only the keys the loader accepts, in the dataclass's own order."""
    if not isinstance(tree, Mapping) or not isinstance(schema, Mapping):
        raise TypeError("YAML defaults must be mappings")
    kept: dict = {}
    for key, value in tree.items():
        expected = schema.get(key, MISSING)
        if expected is MISSING:
            continue
        if isinstance(expected, Mapping) and isinstance(value, Mapping):
            kept[str(key)] = _prune(value, expected)
        else:
            kept[str(key)] = value
    return kept


def _section_defaults(instance: object, schema: object) -> dict:
    """One engine section: dataclass defaults filtered by the loader's own schema."""
    return _prune(_tree(instance), schema)


# The operator surface of `config.yaml`: the order and the keys the file shows. The values come
# from the dataclass tree below, so a default can never drift between the code and the template
# (ADR-0002 §4.6); a nested section the file does not expose (`web_search.fetch`) is dropped here.
HOST_KEYS: dict[str, tuple[str, ...]] = {
    "database": ("url_env", "url", "pool_size", "pool_timeout"),
    "workspace": (),
    "roles_file": (),
    "soul_file": (),
    "sync_documents": (),
    "sandbox": (
        "runtime",
        "image",
        "timeout_seconds",
        "network",
        "host_workspace",
        "env",
        "proxy",
        "pids_limit",
        "memory",
        "cpus",
    ),
    "web_search": (
        "provider",
        "fallbacks",
        "base_url",
        "api_key_env",
        "engine_id_env",
        "ddgs_backend",
        "region",
        "timeout_seconds",
    ),
    "mcp": ("servers",),
    "plugin": ("root", "fetch", "source"),
    "plugins": (),
}


# Only the compose stack provides this network and the proxy sidecar (compose.yaml); a native
# host has neither, so the sandbox stays unconfigured until the operator builds them.
COMPOSE_SANDBOX = {"network": "monoagens-sandbox", "proxy": "http://sandbox-proxy:3128"}


def host_defaults(*, compose: bool = False) -> dict:
    """The host block of `config.yaml`, derived from the dataclass tree (ADR-0002 §4.6).

    `compose=True` opts into the network and proxy sidecar only the compose stack ships; the
    native default keeps `sandbox.network`/`sandbox.proxy` empty so a host without that stack
    never advertises wiring it does not have.
    """
    sandbox = (
        SandboxConfig(network=COMPOSE_SANDBOX["network"], proxy=COMPOSE_SANDBOX["proxy"])
        if compose
        else SandboxConfig()
    )
    raw = _tree(
        HostConfig(
            database=DatabaseConfig(),
            workspace=Path(".monoagens/workspace"),
            sandbox=sandbox,
            web_search=WebSearchConfig(),
            mcp={},
            roles_file=Path("roles.yaml"),
            soul_file=Path("soul.yaml"),
            sync_documents=True,
        )
    )
    # The operator document wraps the server map, and an empty root means `<workspace>/plugins`.
    raw["mcp"] = {"servers": raw.get("mcp") or {}}
    raw["plugin"] = {**raw["plugin"], "root": ""}
    raw["plugins"] = {}
    host: dict = {}
    for key, names in HOST_KEYS.items():
        value = raw[key]
        if isinstance(value, dict):
            if names:
                value = {name: value[name] for name in names}
            if key == "sandbox" and "cpus" in value:
                value["cpus"] = float(value["cpus"])
        if isinstance(value, (str, int, float, bool)) or value is None or isinstance(value, dict):
            host[key] = value
        else:
            host[key] = str(value)
    for key in ("workspace", "roles_file", "soul_file"):
        host[key] = Path(host[key]).as_posix()
    return host


def _memory_defaults() -> dict:
    """`engine.memory`: dataclass values, with the intent/retention nesting the file shows."""
    memory = MemoryConfig()
    body = _section_defaults(memory, ENGINE_SCHEMA["memory"])
    body["intent"] = {
        "remember": {
            "enabled": memory.intent.remember_enabled,
            "floor": memory.intent.remember_floor,
            "margin": memory.intent.remember_margin,
        },
        "forget": {
            "enabled": memory.intent.forget_enabled,
            "floor": memory.intent.forget_floor,
            "margin": memory.intent.forget_margin,
        },
    }
    body["retention"] = {
        "purge": {
            "grace_days": memory.retention.purge_grace_days,
            "batch": memory.retention.purge_batch,
        }
    }
    return body


def _skills_defaults() -> dict:
    """`engine.skills`: the flat dataclass rendered as the file's efficiency/ladder groups."""
    skills = SkillsConfig()
    body = _section_defaults(skills, ENGINE_SCHEMA["skills"])
    body["efficiency"] = {
        "enabled": skills.efficiency_enabled,
        "min_evidence_episodes": skills.efficiency_min_evidence_episodes,
        "window_days": skills.efficiency_window_days,
    }
    body["ladder"] = {
        "enabled": skills.ladder_enabled,
        "proposal_cooldown_days": skills.ladder_proposal_cooldown_days,
    }
    return body


def _prompt_defaults() -> dict:
    """`engine.prompt`: `redact_infra` lives under the file's `self_block` group."""
    prompt = PromptConfig()
    body = _section_defaults(prompt, ENGINE_SCHEMA["prompt"])
    body["self_block"] = {"redact_infra": prompt.redact_infra}
    return body


def engine_defaults() -> dict:
    """The `engine` block of `config.yaml`, derived from the dataclass tree."""
    engine = {
        "default_role": default_role_name(),
        "llm": _section_defaults(LLMConfig(), ENGINE_SCHEMA["llm"]),
        "alerts": _section_defaults(AlertsConfig(), ENGINE_SCHEMA["alerts"]),
        "turns": _section_defaults(TurnConfig(), ENGINE_SCHEMA["turns"]),
        "worker": _section_defaults(WorkerConfig(), ENGINE_SCHEMA["worker"]),
        "io": _section_defaults(IOConfig(), ENGINE_SCHEMA["io"]),
        "concurrency": _section_defaults(ConcurrencyConfig(), ENGINE_SCHEMA["concurrency"]),
        "memory": _memory_defaults(),
        "approval": _section_defaults(ApprovalConfig(), ENGINE_SCHEMA["approval"]),
        "skills": _skills_defaults(),
        "prompt": _prompt_defaults(),
        "ambient": _section_defaults(AmbientConfig(), ENGINE_SCHEMA["ambient"]),
        "guard": _section_defaults(GuardConfig(), ENGINE_SCHEMA["guard"]),
        # Test-only override; empty keeps the packaged lexicon.
        "language": {"lexicon_dir": ""},
        "reflection": _section_defaults(ReflectionConfig(), ENGINE_SCHEMA["reflection"]),
        "research": _section_defaults(ResearchConfig(), ENGINE_SCHEMA["research"]),
        "self_knowledge": _section_defaults(
            SelfKnowledgeConfig(), ENGINE_SCHEMA["self_knowledge"]
        ),
        "safety": _section_defaults(SafetyConfig(), ENGINE_SCHEMA["safety"]),
        "tools": _section_defaults(ToolsConfig(), ENGINE_SCHEMA["tools"]),
        "shell": _section_defaults(ShellConfig(), ENGINE_SCHEMA["shell"]),
        "speech": _section_defaults(SpeechConfig(), ENGINE_SCHEMA["speech"]),
        "search": _section_defaults(SearchConfig(), ENGINE_SCHEMA["search"]),
        "metrics": _section_defaults(MetricsConfig(), ENGINE_SCHEMA["metrics"]),
        "logging": _section_defaults(LoggingConfig(), ENGINE_SCHEMA["logging"]),
        "automations": _section_defaults(AutomationConfig(), ENGINE_SCHEMA["automations"]),
    }
    return engine


# The seed role matrix: one `Role` per shipped block, so a field default lives in roles.py.
_SEED_ROLES: tuple[Role, ...] = (
    Role(
        name="administrator",
        label="local",
        protected=True,
        description="Administrator of MonoAgens",
        priority=100,
        tools=("*",),
        commands=("*",),
        memory_admin=True,
        context=(
            "You are talking to the administrator of MonoAgens, who sets the direction, "
            "permissions, and soul.\n"
            "Allows corrections, config/soul changes, and full reports.\n"
        ),
    ),
    Role(
        name="member",
        description="Member of this deployment",
        priority=10,
        tools=(
            "read",
            "grep",
            "fetch",
            "web_search",
            "thread_history",
            "memory",
            "info",
        ),
        commands=("help", "ping", "link", "unlink", "whoami", "status", "tools"),
        context=(
            "You are talking to a member. Help with their work without opening new "
            "permissions.\n"
        ),
    ),
    Role(
        name="guest",
        description="Unknown sender",
        tools=(),
        commands=("help", "ping", "link", "whoami"),
        context=(
            "You do not know this person yet. Stay polite and never open another chat's data.\n"
        ),
    ),
)


def default_role_name() -> str:
    """The least-privileged shipped role; a new sender never starts higher."""
    return min(_SEED_ROLES, key=lambda role: role.priority).name


def _role_block(role: Role) -> dict:
    """One roles.yaml block in the documented field order."""
    body: dict = {"name": role.name, "label": role.label}
    if role.protected:
        body["protected"] = True
    body.update(
        {
            "description": role.description,
            "priority": role.priority,
            "users": list(role.users),
            "tools": list(role.tools),
            "commands": list(role.commands),
            "memory_admin": role.memory_admin,
            "gateways": {
                name: dict(policy) for name, policy in role.gateways.items()
            },
            "context": role.context,
        }
    )
    return body


def role_defaults() -> dict[str, dict]:
    """The shipped roles, built from `Role` instances instead of hand-written mappings."""
    return {role.name: _role_block(role) for role in _SEED_ROLES}


HOST: dict = host_defaults()

ENGINE: dict = engine_defaults()

GATEWAY: dict = {
    # A gateway is opt-in: the console is core, not a gateway, and needs no block here.
    "gateways": {}
}

CONFIG: dict = {
    **HOST,
    "engine": ENGINE,
    "gateways": GATEWAY["gateways"],
}

# ADR-0002 §4.4: the seed matrix is data, not a runtime exception. A newer role gets the
# closed default; the owner may edit any block in roles.yaml.
GATEWAY_POLICY: dict[str, dict[str, object]] = {
    "administrator": {"dm": True, "groups": "all", "reply_to_bot": True},
    "member": {"dm": True, "groups": "mention", "reply_to_bot": True},
    "guest": {"dm": False, "groups": "none", "reply_to_bot": False},
}
CLOSED_GATEWAY_POLICY: dict[str, object] = {
    "dm": False,
    "groups": "none",
    "reply_to_bot": False,
}

ROLES: dict[str, dict] = role_defaults()


def gateway_index_snapshot() -> dict[str, object]:
    """The discovered gateway index, or empty when discovery itself cannot import."""
    try:
        from .gateway_index import gateway_index
    except Exception:
        return {}
    try:
        return dict(gateway_index())
    except Exception:
        return {}


def _gateway_class(info: object):
    """The gateway class, imported lazily; a missing transport extra is not an error here."""
    module_name = str(getattr(info, "module", "") or "")
    attribute = str(getattr(info, "attribute", "") or "")
    if not module_name or not attribute:
        return None
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return None
    return getattr(module, attribute, None)


def _call_gateway_hook(hook, owner, *args):
    """Call a gateway hook whether it is a classmethod, staticmethod, or instance method."""
    attempts: list[tuple] = [args]
    if getattr(hook, "__self__", None) is None and owner is not None:
        attempts.append((owner, *args))
    for attempt in attempts:
        try:
            return hook(*attempt)
        except TypeError:
            continue
        except Exception:
            return MISSING
    return MISSING


def _gateway_settings(name: str, info: object = None) -> dict:
    """A gateway's own settings defaults, from its hook or its settings dataclass."""
    info = info if info is not None else gateway_index_snapshot().get(name)
    klass = _gateway_class(info) if info is not None else None
    hook = getattr(klass, "default_settings", None) if klass is not None else None
    if callable(hook):
        found = _call_gateway_hook(hook, klass)
        if isinstance(found, Mapping):
            return _tree(dict(found))
    module_name = str(getattr(info, "module", "") or "")
    if not module_name:
        return {}
    try:
        module = importlib.import_module(f"{module_name.rsplit('.', 1)[0]}.config")
    except Exception:
        return {}
    for candidate in vars(module).values():
        if not (isinstance(candidate, type) and is_dataclass(candidate)):
            continue
        names = {item.name for item in fields(candidate)}
        if "enabled" not in names or not callable(getattr(candidate, "from_mapping", None)):
            continue
        try:
            return _tree(candidate())
        except Exception:
            return {}
    return {}


def _gateway_policy(name: str, role: str, info: object = None) -> dict:
    """One role's policy block for one gateway; the gateway owns the vocabulary."""
    info = info if info is not None else gateway_index_snapshot().get(name)
    klass = _gateway_class(info) if info is not None else None
    hook = getattr(klass, "default_role_policy", None) if klass is not None else None
    if callable(hook):
        found = _call_gateway_hook(hook, klass, role)
        if isinstance(found, Mapping):
            return _tree(dict(found))
        if found is None:
            return copy.deepcopy(CLOSED_GATEWAY_POLICY)
    return copy.deepcopy(GATEWAY_POLICY.get(role, CLOSED_GATEWAY_POLICY))


def gateway_block(name: str, *, enabled: bool = True, index: Mapping | None = None) -> dict:
    """The `gateways.<name>` block: its own settings defaults, with the switch set."""
    info = (index if index is not None else gateway_index_snapshot()).get(name)
    settings = _gateway_settings(name, info)
    block = {"enabled": enabled}
    for key, value in settings.items():
        if key != "enabled":
            block[str(key)] = value
    return block


def gateway_defaults() -> dict[str, dict]:
    """Every discovered gateway as an opt-in block, for setup screens and documentation."""
    index = gateway_index_snapshot()
    return {name: gateway_block(name, enabled=False, index=index) for name in index}


def seed_gateway_policies(roles: dict[str, dict], gateways) -> None:
    """Give every role an explicit block for every selected gateway (ADR-0002 §4.4)."""
    names = [str(name) for name in gateways]
    index = gateway_index_snapshot() if names else {}
    for role_name, body in roles.items():
        if not isinstance(body, dict):
            continue
        body["gateways"] = {
            name: _gateway_policy(name, str(role_name), index.get(name)) for name in names
        }


ENV_TAIL = """# Values, never placeholders: `monoagens init` writes the names, you fill the values.
# Postgres for this soul. The password is the same one `compose.yaml` reads.
MONOAGENS_DATABASE_URL=
POSTGRES_PASSWORD=
"""

# The env variable each search provider reads; the key itself never reaches a YAML file.
WEB_SEARCH_API_KEYS: dict[str, str] = {
    "tavily": "TAVILY_API_KEY",
    "brave": "BRAVE_API_KEY",
    "exa": "EXA_API_KEY",
    "serper": "SERPER_API_KEY",
    "google": "GOOGLE_API_KEY",
}
WEB_SEARCH_ENGINE_KEYS: dict[str, str] = {"google": "GOOGLE_CSE_ID"}


def env_file(provider: str, websearch: str = "", api_key_env: str = "") -> str:
    """`.env` content: the chosen model key plus the search keys, then the shared tail."""
    key_env = str(api_key_env or "").strip() or PROVIDERS[provider].api_key_env
    names = [key_env, WEB_SEARCH_API_KEYS.get(websearch), WEB_SEARCH_ENGINE_KEYS.get(websearch)]
    head: list[str] = []
    for name in names:
        if name and name not in head:
            head.append(name)
    return "".join(f"{name}=\n" for name in head) + ENV_TAIL


def dump(body: object) -> str:
    """A plain dump, or the commented roles document when the body is a `roles` wrapper."""
    if isinstance(body, dict) and set(body) == {"roles"} and isinstance(body["roles"], dict):
        return dump_roles(body["roles"])
    return yaml.safe_dump(body, sort_keys=False, allow_unicode=True)


COMMENTS: dict[str, str] = {
    "database": "Postgres store and pool; the URL comes from `.env` or `url`.",
    "database.url_env": (
        "Environment variable that holds the Postgres URL. "
        "Default: MONOAGENS_DATABASE_URL."
    ),
    "database.url": "Inline URL; keep it empty so the password stays in `.env`.",
    "database.pool_size": "Connection pool size. Default: 5.",
    "database.pool_timeout": "Seconds to wait for a pooled connection. Default: 30.",
    "workspace": "Writable root for public/ and scratch/ data. Default: .monoagens/workspace.",
    "roles_file": "Role mirror and seed. Default: roles.yaml.",
    "soul_file": "Soul mirror and seed. Default: soul.yaml.",
    "sync_documents": "Apply changed host files on start; DB wins conflicts. Default: true.",
    "sandbox": "Sandbox runtime and resource limits.",
    "sandbox.runtime": "Container runtime; empty autodetects podman then docker.",
    "sandbox.image": "Container image for the sandbox.",
    "sandbox.timeout_seconds": "Sandbox command timeout. Default: 120.",
    "sandbox.network": "Dedicated sandbox network; bridge, none, and host are rejected.",
    "sandbox.host_workspace": "Host path mounted into the sandbox; empty uses the workspace.",
    "sandbox.env": "Extra environment variables allowed into the container.",
    "sandbox.proxy": "HTTP proxy for the sandbox network.",
    "sandbox.pids_limit": "Process limit inside the sandbox.",
    "sandbox.memory": "Memory limit for the sandbox.",
    "sandbox.cpus": "CPU limit for the sandbox.",
    "web_search": "Search provider, fallback chain, and fetch policy.",
    "web_search.provider": (
        "Primary search provider: ddgs, searxng, tavily, brave, exa, serper, google, or off."
    ),
    "web_search.fallbacks": "Providers tried in order after the primary one fails.",
    "web_search.base_url": "SearXNG JSON endpoint; empty means that provider cannot run.",
    "web_search.api_key_env": "Environment variable that holds the search key; never the key.",
    "web_search.engine_id_env": "Environment variable holding the Google CSE engine id.",
    "web_search.ddgs_backend": "ddgs backend selection; auto lets the library choose.",
    "web_search.region": "Search region hint; empty means no region bias.",
    "web_search.timeout_seconds": "Search timeout. Default: 10.",
    "mcp": "MCP servers available to the engine.",
    "mcp.servers": "Server map; empty means no MCP server.",
    "plugin": "Plugin code location and fetch policy.",
    "plugin.root": "Plugin root; empty means `<workspace>/plugins`.",
    "plugin.fetch": "Fetch plugin code by digest before enabling. Default: true.",
    "plugin.source": "Source used when fetching a plugin.",
    "plugins": "Per-plugin enable flags and pinned digests.",
    "engine": "Engine mechanism and runtime settings.",
    "engine.default_role": "Role for an unknown sender. Default: guest.",
    "engine.llm": "Provider wire, model, and operational limits.",
    "engine.llm.provider": "Provider name selected at init; no code default.",
    "engine.llm.base_url": "Provider endpoint; empty uses the provider record.",
    "engine.llm.model": "Model id selected by the operator; no code default.",
    "engine.llm.api_key_env": "Environment variable that holds the provider key.",
    "engine.llm.reasoning_effort": "Reasoning effort; empty uses the provider default.",
    "engine.llm.service_tier": "Provider service tier; empty uses the provider default.",
    "engine.llm.timeout_seconds": "Per-call model timeout. Default: 120.",
    "engine.llm.utility_temperature": (
        "Temperature for decisions, summaries, and extraction. Default: 0.2."
    ),
    "engine.llm.run_budget_seconds": "Wall-clock budget for one turn. Default: 240.",
    "engine.llm.max_calls_per_turn": (
        "Model calls allowed in one turn; 0 = unlimited. Default: 0."
    ),
    "engine.llm.prompt_cache": (
        "Send a stable prefix first for provider prompt caching. Default: true."
    ),
    "engine.llm.retry": "Retry policy for the turn and background paths.",
    "engine.llm.retry.turn_attempts": "Turn attempts. Default: 1.",
    "engine.llm.retry.worker_attempts": "Background attempts. Default: 3.",
    "engine.llm.retry.base_seconds": "Initial backoff. Default: 2.",
    "engine.llm.retry.cap_seconds": "Backoff cap. Default: 60.",
    "engine.llm.retry.jitter": "Backoff jitter ratio. Default: 0.2.",
    "engine.llm.retry.retry_after_max_seconds": "Cap on a provider Retry-After. Default: 10.",
    "engine.llm.fallbacks": (
        "Ordered fallback targets tried after the primary fails; empty means none."
    ),
    "engine.alerts": "Consecutive failures become one admin message and one webhook POST.",
    "engine.alerts.enabled": "Enable alerting; disabled by default. Default: false.",
    "engine.alerts.sources": "Which failure sources may fire an alert.",
    "engine.alerts.sources.database": "Alert on database failures. Default: true.",
    "engine.alerts.sources.llm": "Alert on model-provider failures. Default: true.",
    "engine.alerts.sources.gateway": "Alert on gateway crashes. Default: true.",
    "engine.alerts.sources.plugin": "Alert on plugin load failures. Default: true.",
    "engine.alerts.sources.mcp": "Alert on MCP server failures. Default: true.",
    "engine.alerts.threshold": "Consecutive failures before one alert fires. Default: 3.",
    "engine.alerts.cooldown_seconds": (
        "Minimum seconds between duplicate alerts per source. Default: 900."
    ),
    "engine.alerts.notify_admin": (
        "Send the alert to the administrator through the active body. Default: true."
    ),
    "engine.alerts.webhook_url_env": (
        "Environment variable holding an optional Slack/Discord/HTTP webhook URL."
    ),
    "engine.turns": "Turn lifecycle, resume, and shutdown.",
    "engine.turns.timeout_seconds": "Hard one-turn deadline. Default: 300.",
    "engine.turns.min_call_seconds": (
        "Do not start a model call below this remaining time. Default: 5."
    ),
    "engine.turns.resume": "Resume an interrupted turn after a restart. Default: true.",
    "engine.turns.max_attempts": "Turn attempts before permanent failure. Default: 2.",
    "engine.turns.max_age_seconds": "Oldest resumable turn. Default: 900.",
    "engine.turns.stale_after_seconds": "Age before an orphaned turn is swept. Default: 21600.",
    "engine.turns.partial_delivery": "skip or resend a partially delivered reply. Default: skip.",
    "engine.turns.max_resume_per_chat": "Resume cap per chat. Default: 5.",
    "engine.turns.max_resume_per_minute": "Resume rate cap across the system. Default: 10.",
    "engine.turns.backlog_policy": "How a busy chat's queue is handled. Default: consolidate.",
    "engine.turns.shutdown": "Graceful shutdown budget.",
    "engine.turns.shutdown.grace_seconds": "Time a live turn may finish. Default: 25.",
    "engine.turns.shutdown.drain_seconds": "Time to drain outbound sends. Default: 15.",
    "engine.turns.shutdown.notice_seconds": "Restart notice send budget. Default: 5.",
    "engine.turns.shutdown.notice_per_chat": "At most one notice per chat. Default: true.",
    "engine.turns.shutdown.notice_retry": "Notice delivery retry.",
    "engine.turns.shutdown.notice_retry.attempts": "Notice attempts. Default: 2.",
    "engine.turns.shutdown.notice_retry.backoff_seconds": "Notice backoff. Default: 1.",
    "engine.turns.shutdown.resume_notice": "When to announce a resumed turn.",
    "engine.turns.shutdown.resume_notice.after_seconds": "Announce after this delay. Default: 300.",
    "engine.turns.shutdown.stop_grace_period": "Orchestrator stop deadline. Default: 30.",
    "engine.worker": "Background worker retry and reaper.",
    "engine.worker.retry": "Background job retry budget.",
    "engine.worker.retry.attempts": "Attempts before dead-letter. Default: 5.",
    "engine.worker.retry.backoff_seconds": "Initial backoff. Default: 2.",
    "engine.worker.retry.dead_letter_after": "Failures before dead-letter. Default: 5.",
    "engine.worker.reaper": "Stale claim sweeper.",
    "engine.worker.reaper.every_seconds": "Sweep interval. Default: 30.",
    "engine.worker.reaper.batch": "Rows recovered per sweep. Default: 100.",
    "engine.io": "Blocking I/O worker threads.",
    "engine.io.workers": "Worker thread count. Default: 8.",
    "engine.concurrency": "Per-chat concurrency bounds.",
    "engine.concurrency.max_chat_locks": "Maximum chat locks. Default: 256.",
    "engine.memory": "Memory store, capture, privacy, and retention.",
    "engine.memory.auto_capture": "Capture durable facts automatically. Default: true.",
    "engine.memory.max_items": "Memory items per prompt. Default: 12.",
    "engine.memory.max_chars": "Memory prompt budget; 0 = unlimited. Default: 0.",
    "engine.memory.collection": "Memory collection label.",
    "engine.memory.extraction_model": "Extraction model; empty uses engine.llm.model.",
    "engine.memory.telemetry": "Send memory telemetry. Default: false.",
    "engine.memory.capture": "Deterministic capture gate.",
    "engine.memory.capture.mode": "off, conservative, balanced, or eager. Default: balanced.",
    "engine.memory.capture.min_chars": "Shortest text worth capturing. Default: 8.",
    "engine.memory.capture.novelty_threshold": "Duplicate cutoff. Default: 0.88.",
    "engine.memory.privacy": "Disclosure limits and group capture policy.",
    "engine.memory.privacy.enabled": "Apply privacy levels. Default: true.",
    "engine.memory.privacy.default_level": "Default disclosure level, 0-4. Default: 2.",
    "engine.memory.privacy.ask_level": "Level that requires confirmation. Default: 4.",
    "engine.memory.privacy.group_max_level": "Ceiling in groups. Default: 2.",
    "engine.memory.privacy.dm_max_level": "Ceiling in direct messages. Default: 4.",
    "engine.memory.privacy.auditor": "Run the disclosure auditor. Default: true.",
    "engine.memory.privacy.group_capture": "What may be captured in a group.",
    "engine.memory.privacy.group_capture.self_only": "Only the speaker's own words. Default: true.",
    "engine.memory.privacy.group_capture.third_party": "drop or deny third-party facts.",
    "engine.memory.privacy.group_capture.ask_in_group": "Ask before group capture. Default: false.",
    "engine.memory.worker": "Cognition queue worker.",
    "engine.memory.worker.tick_seconds": "Queue poll interval. Default: 1.",
    "engine.memory.worker.concurrency": "Concurrent cognition jobs. Default: 2.",
    "engine.memory.worker.capture_max_items": "Capture batch size. Default: 20.",
    "engine.memory.worker.consolidation_every_seconds": "Consolidation interval. Default: 60.",
    "engine.memory.worker.audit_every_seconds": "Audit interval. Default: 86400.",
    "engine.memory.worker.queue_cap": "Queue capacity. Default: 5000.",
    "engine.memory.worker.retry": "Cognition retry budget.",
    "engine.memory.worker.retry.attempts": "Attempts before dead-letter. Default: 5.",
    "engine.memory.worker.retry.backoff_seconds": "Initial backoff. Default: 2.",
    "engine.memory.worker.retry.dead_letter_after": "Failures before dead-letter. Default: 5.",
    "engine.memory.rooms": "Cross-room recall limits.",
    "engine.memory.rooms.max_rooms_per_turn": "Rooms read per turn. Default: 2.",
    "engine.memory.rooms.max_messages": "Messages read per room. Default: 20.",
    "engine.memory.rooms.require_membership": "Fail closed on unknown membership. Default: true.",
    "engine.memory.rooms.source": "gateway_first or recorded. Default: gateway_first.",
    "engine.memory.intent": "Prototype intent thresholds; never a model call.",
    "engine.memory.intent.remember": "The remember intent.",
    "engine.memory.intent.remember.enabled": "Route remember hints. Default: true.",
    "engine.memory.intent.remember.floor": "Minimum score. Default: 0.62.",
    "engine.memory.intent.remember.margin": "Score margin over the runner-up. Default: 0.06.",
    "engine.memory.intent.forget": "The forget intent.",
    "engine.memory.intent.forget.enabled": "Route forget hints. Default: true.",
    "engine.memory.intent.forget.floor": "Minimum score. Default: 0.64.",
    "engine.memory.intent.forget.margin": "Score margin over the runner-up. Default: 0.06.",
    "engine.memory.retention": "How long memory rows live.",
    "engine.memory.retention.purge": "Daily purge limits.",
    "engine.memory.retention.purge.grace_days": "Grace window before purge. Default: 1.",
    "engine.memory.retention.purge.batch": "Rows removed per run. Default: 500.",
    "engine.approval": "Approval policy for sensitive commands and settings.",
    "engine.approval.mode": "default, yolo, or strict. Default: yolo.",
    "engine.approval.natural_confirm": "Ask in chat and accept a natural yes/no. Default: true.",
    "engine.approval.confirm_cross_gateway": (
        "Ask before a cross-room or cross-gateway send. Default: true."
    ),
    "engine.approval.confirm_tasks": "Ask before creating a durable task. Default: true.",
    "engine.approval.sensitive_commands": "Command patterns that always need approval.",
    "engine.approval.settings": "Ask before a settings change. Default: true.",
    "engine.approval.ttl_seconds": "How long an approval stays valid. Default: 900.",
    "engine.approval.consent_max_per_hour": "Self-confirmation anti-spam cap. Default: 20.",
    "engine.skills": "Procedures, efficiency evidence, and the automation ladder.",
    "engine.skills.enabled": "Use skills. Default: true.",
    "engine.skills.task_idle_minutes": "Task idle window. Default: 10.",
    "engine.skills.idle_after_send_minutes": "Idle after a send. Default: 3.",
    "engine.skills.per_episode": "Skills retrieved per episode. Default: 1.",
    "engine.skills.max_chars": "Skill prompt budget; 0 = unlimited. Default: 0.",
    "engine.skills.max_body_chars": "Stored skill body cap. Default: 18000.",
    "engine.skills.use_renewal_days": "Renew an unused skill after this window. Default: 30.",
    "engine.skills.efficiency": "Efficiency comparison evidence.",
    "engine.skills.efficiency.enabled": "Compare outcomes. Default: true.",
    "engine.skills.efficiency.min_evidence_episodes": "Minimum episodes. Default: 2.",
    "engine.skills.efficiency.window_days": "Evidence window. Default: 30.",
    "engine.skills.ladder": "Automation proposal ladder.",
    "engine.skills.ladder.enabled": "Propose automations. Default: true.",
    "engine.skills.ladder.proposal_cooldown_days": "Proposal cooldown. Default: 14.",
    "engine.prompt": "Prompt assembly and salience.",
    "engine.prompt.salience": "Select volatile blocks by salience. Default: false.",
    "engine.prompt.max_chars": "Volatile prompt budget; 0 = unlimited. Default: 0.",
    "engine.prompt.thread_chars": "Thread window budget; 0 = unlimited. Default: 0.",
    "engine.prompt.pinned_blocks": "Blocks that always stay in the prompt.",
    "engine.prompt.capability_index": "Render the capability index. Default: true.",
    "engine.prompt.capability_index_max_chars": "Capability index budget. Default: 1200.",
    "engine.prompt.capability_index_two_level_at": "Two-level index threshold. Default: 150.",
    "engine.prompt.prefix_hash": "Hash the stable prefix for caching. Default: true.",
    "engine.prompt.self_block": "The self-knowledge block.",
    "engine.prompt.self_block.redact_infra": (
        "non_administrator, never, or always. Default: non_administrator."
    ),
    "engine.ambient": "Ambient background work.",
    "engine.ambient.enabled": "Run ambient work. Default: true.",
    "engine.ambient.tick_seconds": "Ambient tick. Default: 10.",
    "engine.ambient.digest_every": "Messages between room digests. Default: 12.",
    "engine.ambient.digest_cooldown": "Digest cooldown. Default: 900.",
    "engine.ambient.history_window": "Messages read per digest. Default: 30.",
    "engine.ambient.idle_minutes": "Idle minutes before research. Default: 20.",
    "engine.ambient.research_cooldown": "Research cooldown. Default: 21600.",
    "engine.ambient.research_per_day": "Ambient research quota. Default: 6.",
    "engine.ambient.research_results": "Results per ambient research. Default: 5.",
    "engine.guard": "Deterministic and classifier guard limits.",
    "engine.guard.enabled": "Run the guard. Default: true.",
    "engine.guard.model": "Classifier model; empty uses engine.llm.model.",
    "engine.guard.timeout_seconds": "Classifier timeout. Default: 6.",
    "engine.guard.max_command_chars": "Maximum command length scanned. Default: 4000.",
    "engine.guard.credential_patterns": "Operator additions to the credential patterns.",
    "engine.language": "Language pack location.",
    "engine.language.lexicon_dir": "Lexicon override; empty uses the packaged catalog.",
    "engine.reflection": "Background reflection passes over memory.",
    "engine.reflection.enabled": "Run reflection. Default: true.",
    "engine.reflection.every_minutes": "Pass interval. Default: 60.",
    "engine.reflection.idle_minutes": "Idle minutes before a pass. Default: 30.",
    "engine.reflection.min_new_rows": "Rows needed before a pass. Default: 25.",
    "engine.reflection.min_candidates": "Minimum candidates. Default: 1.",
    "engine.reflection.scopes_per_pass": "Scopes per pass. Default: 1.",
    "engine.reflection.items_per_pass": "Items considered per pass. Default: 40.",
    "engine.reflection.writes_per_pass": "Writes per pass. Default: 20.",
    "engine.reflection.seconds_per_pass": "Pass time budget. Default: 60.",
    "engine.reflection.passes_per_day": "Daily pass cap. Default: 24.",
    "engine.reflection.derive_depth": "Derived-fact depth. Default: 1.",
    "engine.reflection.dispute_ttl_days": "Dispute lifetime. Default: 30.",
    "engine.reflection.questions_per_day": "Dispute questions per day. Default: 1.",
    "engine.research": "Model prior, websearch triggers, and citation verification.",
    "engine.research.mode": "off, auto, or always. Default: auto.",
    "engine.research.require_citation": "Require a real [Rn] for current claims. Default: true.",
    "engine.research.freshness_hours": (
        "Maximum source age for a current claim; 0 disables. Default: 24."
    ),
    "engine.research.max_results": "Search result count, 1-10. Default: 5.",
    "engine.self_knowledge": "Generated self knowledge and capability index.",
    "engine.self_knowledge.enabled": "Render self knowledge. Default: true.",
    "engine.self_knowledge.capability_index": "Include the capability index. Default: true.",
    "engine.self_knowledge.topics": "Topics that may be described.",
    "engine.self_knowledge.coarse_buckets": "Publish coarse counts. Default: true.",
    "engine.self_knowledge.topology": "Describe the topology. Default: true.",
    "engine.self_knowledge.max_chars_per_topic": "Topic budget. Default: 1200.",
    "engine.self_knowledge.forbidden_token_check": "Reject forbidden tokens. Default: true.",
    "engine.self_knowledge.docs": "Repo documents read for identity.",
    "engine.self_knowledge.docs_max_chars": "Document budget. Default: 4000.",
    "engine.safety": "Safety pack policy.",
    "engine.safety.allow_degraded": "Run without a language safety pack. Default: false.",
    "engine.tools": "Tool call validation and context budget.",
    "engine.tools.args": "Argument validation mode. Default: strict.",
    "engine.tools.max_errors": "Consecutive invalid calls allowed. Default: 5.",
    "engine.tools.hot": "Tools always indexed in the prompt.",
    "engine.tools.activate_on_demand": "Load other tools on demand. Default: true.",
    "engine.shell": "Environment inherited by shell commands.",
    "engine.shell.env": "Variable names passed through to the child.",
    "engine.speech": "Speech-to-text (STT) and text-to-speech (TTS) provider settings.",
    "engine.speech.stt": "Speech-to-text (STT) transcription adapter settings.",
    "engine.speech.stt.provider": "STT provider name; empty disables transcription. Default: ''.",
    "engine.speech.stt.base_url": "Custom STT endpoint URL; empty uses the provider default.",
    "engine.speech.stt.api_key_env": "Environment variable holding the STT API key.",
    "engine.speech.stt.model": "STT model id; empty uses the provider default.",
    "engine.speech.stt.language": "Default transcription language hint; empty autodetects.",
    "engine.speech.tts": "Text-to-speech (TTS) synthesis adapter settings.",
    "engine.speech.tts.provider": (
        "TTS provider name; empty disables speech synthesis. Default: ''."
    ),
    "engine.speech.tts.base_url": "Custom TTS endpoint URL; empty uses the provider default.",
    "engine.speech.tts.api_key_env": "Environment variable holding the TTS API key.",
    "engine.speech.tts.model": "TTS model id; empty uses the provider default.",
    "engine.speech.tts.voice": "Default voice id or preset when soul.yaml leaves it empty.",
    "engine.speech.timeout_seconds": "Per-call timeout for STT/TTS requests. Default: 30.",
    "engine.search": "Full-text and trigram search limits.",
    "engine.search.fts": "Use full-text search. Default: true.",
    "engine.search.lang": "Postgres text search language. Default: simple.",
    "engine.search.trgm": "Use trigram fallback. Default: true.",
    "engine.search.trgm_threshold": "Trigram similarity floor. Default: 0.3.",
    "engine.search.max_results": "Rows returned per search. Default: 50.",
    "engine.search.snippet_chars": "Snippet length. Default: 240.",
    "engine.metrics": "Cost metrics retention and window.",
    "engine.metrics.enabled": "Record llm_calls. Default: true.",
    "engine.metrics.window_seconds": "Status window. Default: 300.",
    "engine.metrics.retention_days": "Metric retention. Default: 90.",
    "engine.logging": "Log format and level.",
    "engine.logging.format": "text or json. Default: text.",
    "engine.logging.level": "Standard logging level. Default: INFO.",
    "engine.automations": "Scheduled automations and their limits.",
    "engine.automations.enabled": "Allow automations. Default: true.",
    "engine.automations.kill_switch": "Stop every automation at once. Default: false.",
    "engine.automations.default_budget_per_day": "Default daily budget. Default: 4.",
    "engine.automations.run_timeout_seconds": "One run's deadline. Default: 120.",
    "engine.automations.max_steps": "Steps per run. Default: 10.",
    "engine.automations.min_interval_seconds": "Shortest schedule interval. Default: 60.",
    "engine.automations.max_occurrences_per_day": "Occurrence cap. Default: 24.",
    "engine.automations.default_quiet_hours": "Default quiet window. Default: 22:00-07:00.",
    "engine.automations.default_quiet_policy": "skip or defer. Default: skip.",
    "engine.automations.max_active_per_person": "Active automations per person. Default: 10.",
    "engine.automations.sandbox": "Sandbox step limits.",
    "engine.automations.sandbox.programs_root": "Root for sandbox programs. Default: workspace.",
    "engine.automations.condition": "Natural-language conditions.",
    "engine.automations.condition.nl_criteria": "Allow NL criteria. Default: true.",
    "engine.automations.condition.nl_budget_tokens_per_day": "Daily token budget. Default: 20000.",
    "engine.automations.retry": "Transient failure retry.",
    "engine.automations.retry.max_attempts": "Attempts. Default: 3.",
    "engine.automations.retry.backoff_seconds": "Initial backoff. Default: 30.",
    "engine.automations.retry.max_backoff_seconds": "Backoff cap. Default: 900.",
    "engine.automations.retry.jitter": "Add jitter. Default: true.",
    "engine.automations.retry.respect_retry_after": "Honor Retry-After. Default: true.",
    "engine.automations.resilience": "Missed occurrences and stale claims.",
    "engine.automations.resilience.catchup_minutes": "Catch-up window. Default: 15.",
    "engine.automations.resilience.max_stale_days": "Stale claim age. Default: 7.",
    "engine.automations.resilience.stale_claim_seconds": "Claim lease. Default: 300.",
    "engine.automations.resilience.missed_apology": "Apologize once for a miss. Default: true.",
    "engine.automations.resilience.apology_digest": "Fold apologies into a digest. Default: true.",
    "engine.automations.resilience.apology_respect_quiet_hours": (
        "Keep apologies inside quiet hours. Default: true."
    ),
    "gateways": "Transport bodies; each is off until enabled.",
}


def _gateway_note(name: str) -> str:
    """Descriptor facts for the gateway comment: extra and declared capabilities."""
    info = gateway_index_snapshot().get(name)
    descriptor = getattr(info, "descriptor", None)
    detail: list[str] = []
    extra = str(getattr(descriptor, "extra", "") or "").strip()
    if extra:
        detail.append(f"extra {extra}")
    raw = getattr(descriptor, "capabilities", None)
    capabilities = (
        tuple(str(item) for item in raw)
        if isinstance(raw, (list, tuple, set, frozenset))
        else ()
    )
    if capabilities:
        detail.append("capabilities " + ", ".join(capabilities))
    return f" ({'; '.join(detail)})" if detail else ""


def comment_for(path: str, value: object) -> str:
    """One comment per key; the fallback still names the path and default."""
    found = COMMENTS.get(path)
    if found:
        return found
    parts = path.split(".")
    if parts[0] == "gateways" and len(parts) >= 2:
        name = parts[1]
        if len(parts) == 2:
            return (
                f"'{name}' gateway{_gateway_note(name)}; opt in with "
                f"`monoagens gateway enable {name}`."
            )
        if len(parts) == 3 and parts[2] == "enabled":
            return f"Turn the '{name}' gateway on or off."
        return f"'{name}' setting; that gateway's own settings dataclass owns its default."
    if isinstance(value, dict):
        return f"{path} — operator section."
    return f"{path} — operator setting. Default: {_scalar(value)}"


# Section banners in the order the file shows them (docs/note-post-adr0008.md).
HOST_TITLES: dict[str, str] = {
    "database": "DATABASE & POSTGRESQL CONNECTION",
    "workspace": "PATHS & COMPANION FILES",
    "roles_file": "",
    "soul_file": "",
    "sync_documents": "",
    "sandbox": "SANDBOX CONTAINER",
    "web_search": "WEB SEARCH (MULTI-PROVIDER)",
    "mcp": "MODEL CONTEXT PROTOCOL",
    "plugin": "PLUGIN SYSTEM",
    "plugins": "",
}
ENGINE_TITLES: dict[str, str] = {
    "default_role": "DEFAULT ROLE",
    "llm": "PROVIDER & MODEL",
    "alerts": "OUT-OF-BAND ALERTING",
    "turns": "TURN LIFECYCLE & SHUTDOWN",
    "worker": "BACKGROUND WORKER",
    "io": "BLOCKING I/O",
    "concurrency": "CONCURRENCY",
    "memory": "MEMORY, PRIVACY & RETENTION",
    "approval": "APPROVAL POLICY",
    "skills": "SKILLS",
    "prompt": "PROMPT ASSEMBLY",
    "ambient": "AMBIENT WORK",
    "guard": "GUARD",
    "language": "LANGUAGE PACK",
    "reflection": "REFLECTION",
    "research": "RESEARCH & CITATIONS",
    "self_knowledge": "SELF KNOWLEDGE",
    "safety": "SAFETY POLICY",
    "tools": "TOOL CONTRACT",
    "shell": "SHELL ENVIRONMENT",
    "search": "SEARCH",
    "metrics": "METRICS",
    "logging": "LOGGING",
    "automations": "AUTOMATIONS",
}

CONFIG_BANNER = """\
# ============================================================================
# MonoAgens host configuration (config.yaml)
# Authoritative source of truth for this deployment.
# Secrets (API keys, database passwords) live only in .env.
# ============================================================================"""

CONFIG_HEADERS: dict[str, tuple[str, str]] = {
    "engine": (
        "ENGINE",
        "Mechanism and runtime settings; soul and roles live in their own files.",
    ),
    "gateways": (
        "GATEWAYS",
        "Each gateway is opt-in; `monoagens gateway enable <name>` adds its block.",
    ),
}


def _config_banners(body: Mapping) -> dict[str, str]:
    banners: dict[str, str] = {}
    number = 0
    for key in body:
        title = HOST_TITLES.get(str(key), str(key).replace("_", " ").upper())
        if not title:
            continue
        number += 1
        if str(key) in CONFIG_HEADERS:
            continue
        banners[str(key)] = f"{number}. {title}"
    engine = body.get("engine")
    if isinstance(engine, Mapping):
        for name in engine:
            title = ENGINE_TITLES.get(str(name), str(name).replace("_", " ").upper())
            banners[f"engine.{name}"] = f"engine.{name}: {title}"
    return banners


def _scalar(value: object) -> str:
    rendered = yaml.safe_dump(value, sort_keys=False, allow_unicode=True).strip()
    return rendered.removesuffix("\n...")


def _comment_lines(pad: str, text: str) -> list[str]:
    return [f"{pad}# {line}" for line in text.splitlines()]


def _render_header(
    pad: str,
    path: str,
    banners: Mapping[str, str],
    headers: Mapping[str, tuple[str, str]],
    lines: list[str],
) -> None:
    """Open a section with its banner or its `# ===` group header."""
    header = headers.get(path)
    banner = banners.get(path)
    if not (header or banner):
        return
    if lines and lines[-1] != "":
        lines.append("")
    if header:
        title, detail = header
        rule = "# " + "=" * 76
        lines.extend([rule, f"# {title}", f"# {detail}", rule])
    if banner:
        lines.append(f"{pad}# --- {banner} ---")


def _render_value(pad: str, key: str, value: object, lines: list[str]) -> None:
    """One YAML entry: a nested mapping, a list, a block scalar, or a plain scalar."""
    if isinstance(value, dict):
        if not value:
            lines.append(f"{pad}{key}: {{}}")
            return
        lines.append(f"{pad}{key}:")
        return
    if isinstance(value, list):
        if not value:
            lines.append(f"{pad}{key}: []")
            return
        lines.append(f"{pad}{key}:")
        lines.extend(f"{pad}  - {_scalar(item)}" for item in value)
        return
    if isinstance(value, str) and "\n" in value:
        chomp = "|" if value.endswith("\n") else "|-"
        lines.append(f"{pad}{key}: {chomp}")
        for raw in value.rstrip("\n").split("\n"):
            lines.append(f"{pad}  {raw}" if raw else "")
        return
    lines.append(f"{pad}{key}: {_scalar(value)}")


def _render_node(
    node: Mapping,
    prefix: str,
    indent: int,
    comment: Callable[[str, object], str],
    banners: Mapping[str, str],
    headers: Mapping[str, tuple[str, str]],
    lines: list[str],
) -> None:
    """Render one mapping with a comment above every key; a banner opens each section."""
    pad = "  " * indent
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        _render_header(pad, path, banners, headers, lines)
        lines.extend(_comment_lines(pad, comment(path, value)))
        _render_value(pad, str(key), value, lines)
        if isinstance(value, dict) and value:
            _render_node(value, path, indent + 1, comment, banners, headers, lines)


def dump_config(body: dict) -> str:
    """Render the generated config with banners and a comment above every key at every depth."""
    lines = [str(line) for line in CONFIG_BANNER.splitlines()]
    _render_node(
        body,
        "",
        0,
        comment_for,
        _config_banners(body),
        CONFIG_HEADERS,
        lines,
    )
    return "\n".join(lines) + "\n"


ROLES_BANNER = """\
# ============================================================================
# MonoAgens role-based access control (roles.yaml)
# Authoritative source of permissions; a missing entry denies by default.
# ============================================================================"""

ROLE_FIELD_COMMENTS: dict[str, str] = {
    "name": "Canonical role name; it must match the block key.",
    "label": "Optional display label for status lines and logs.",
    "protected": "Locked role: it cannot be deleted, renamed, or merged by /link (ADR-0006).",
    "description": "One line the operator reads in `monoagens people list`.",
    "priority": "Higher wins when one person holds several roles; ties order by name.",
    "users": "per_… person ids holding this role; `monoagens people add` fills it.",
    "tools": "Tool name patterns this role may call; '*' means every tool.",
    "commands": "Chat command patterns this role may run; '*' means every command.",
    "memory_admin": "Allow memory maintenance across scopes, not only the holder's own.",
    "gateways": "Per-gateway access policy; each gateway owns its own keys (ADR-0002).",
    "context": "Extra instructions injected into the prompt when this role speaks.",
}


def _role_comment(path: str, value: object) -> str:
    parts = path.split(".")
    if path == "roles":
        return "Role definitions and membership; a missing role denies access."
    if len(parts) == 2:
        return f"Role block '{parts[1]}'; a missing role denies access."
    field = parts[2] if len(parts) > 2 else ""
    if field in ("gateways", "gateway"):
        if len(parts) == 3:
            return ROLE_FIELD_COMMENTS["gateways"]
        if len(parts) == 4:
            return f"'{parts[3]}' policy; keys and values are validated by that gateway."
        return f"Gateway option '{parts[-1]}'; the gateway owns its meaning and default."
    found = ROLE_FIELD_COMMENTS.get(field)
    if found:
        return found
    if isinstance(value, dict):
        return f"{path} — role section."
    return f"{path} — role setting."


def dump_roles(roles: dict[str, dict]) -> str:
    """Render roles.yaml with a banner per role and a comment above every field."""
    lines = [*ROLES_BANNER.splitlines(), ""]
    banners: dict[str, str] = {"roles": "ROLE DEFINITIONS"}
    for name in roles:
        banners[f"roles.{name}"] = f"ROLE: {name}"
    _render_node(
        {"roles": roles},
        "",
        0,
        _role_comment,
        banners,
        {},
        lines,
    )
    return "\n".join(lines) + "\n"


SOUL_BANNER = """\
# ============================================================================
# MonoAgens soul definition (soul.yaml)
# One continuous identity: modes, persona, rules, and voice tuning.
# ============================================================================"""

SOUL_COMMENTS: dict[str, str] = {
    "name": "Display name injected into the identity block of the prompt.",
    "description": "One sentence describing what this agent is.",
    "language": "Primary language code; selects the translation catalog and safety pack.",
    "locale": "Regional format hint for dates, numbers, and weekdays.",
    "timezone": "IANA timezone used by schedules, quiet hours, and clock awareness.",
    "pronouns": "First-person pronoun the agent uses for itself.",
    "mode": "Active persona mode; changing it never changes permissions.",
    "modes": "Persona modes this soul may switch between.",
    "background": "Standing context about the agent's existence and role.",
    "persona": "Character and communication guidance for the model.",
    "examples": "Optional example exchanges that demonstrate the voice.",
    "rules": "Principled rules synced as `kind=rule` memory at primary start.",
    "voice": "Voice tuning; each sub-feature governs itself.",
    "voice.sampling": "LLM sampling values for user-facing replies.",
    "voice.sampling.daily_temperature": "Casual conversation temperature. Default: 0.9.",
    "voice.sampling.professional_temperature": "Technical work temperature. Default: 0.3.",
    "voice.sampling.top_p": "Nucleus sampling ceiling; null uses the provider default.",
    "voice.sampling.top_k": "Top-token candidates; null uses the provider default.",
    "voice.sampling.min_p": "Minimum token probability; null leaves it unused.",
    "voice.affect": "PAD emotion state and per-person relations.",
    "voice.affect.enabled": "Run the affect engine. Default: true.",
    "voice.affect.volatility": "How quickly affect moves. Default: expressive.",
    "voice.affect.grudge_half_life_hours": "Half-life of a grudge. Default: 1.5.",
    "voice.affect.vibe": "Short-lived energy modulation.",
    "voice.affect.vibe.enabled": "Apply the vibe layer. Default: true.",
    "voice.affect.vibe.attack": "Rise rate. Default: 0.35.",
    "voice.affect.vibe.baseline_influence": "Baseline pull. Default: 0.05.",
    "voice.affect.vibe.hold_seconds": "Hold time. Default: 1.2.",
    "voice.affect.vibe.tick_seconds": "Decay tick. Default: 1.0.",
    "voice.style": "Per-room and per-person writing style adaptation.",
    "voice.style.enabled": "Run the style engine. Default: true.",
    "voice.style.max_chars": "Style prompt budget. Default: 800.",
    "voice.character": "Character polish pass over the final draft.",
    "voice.character.enabled": "Polish replies. Default: false.",
    "voice.character.max_chars": "Draft budget. Default: 400.",
    "voice.character.max_tokens": "Polish output cap. Default: 500.",
    "voice.character.temperature": "Polish temperature. Default: 0.7.",
    "voice.humanize": "Outgoing text normalization for chat delivery.",
    "voice.humanize.enabled": "Humanize outgoing text. Default: true.",
    "voice.humanize.cpms": "Typing speed estimate. Default: 25.",
    "voice.humanize.min_delay": "Shortest typing pause. Default: 0.5.",
    "voice.humanize.max_delay": "Longest typing pause. Default: 5.",
    "voice.humanize.between_delay": "Pause between bubbles. Default: 0.7.",
    "voice.humanize.max_bubbles": "Bubbles per reply. Default: 3.",
    "voice.humanize.reaction_stickers": "Sticker reactions allowed for this soul.",
    "voice.profanity": "avoid, mirror, or allow. Default: avoid.",
    "voice.safety": "inherit or relaxed. Default: inherit.",
    "voice.speech": "Spoken voice preferences (STT/TTS) for this soul.",
    "voice.speech.enabled": (
        "Allow spoken replies when a TTS provider is configured. Default: false."
    ),
    "voice.speech.stt": "Soul-level STT language or prompt hint; empty uses engine.speech.stt.",
    "voice.speech.tts": "Soul-level TTS voice id or preset; empty uses engine.speech.tts.voice.",
}


def _soul_comment(path: str, value: object) -> str:
    found = SOUL_COMMENTS.get(path)
    if found:
        return found
    if isinstance(value, dict):
        return f"{path} — soul section."
    return f"{path} — soul setting. Default: {_scalar(value)}"


def dump_soul(body: dict) -> str:
    """Render soul.yaml with a banner and a comment above every entry."""
    lines = [*SOUL_BANNER.splitlines(), ""]
    banners: dict[str, str] = {
        "voice": "VOICE: SAMPLING, AFFECT, STYLE, CHARACTER, HUMANIZE"
    }
    _render_node(body, "", 0, _soul_comment, banners, {}, lines)
    return "\n".join(lines) + "\n"


def _voice_parts() -> tuple[str, ...]:
    """Index the voice sub-sections that carry their own enabled switch."""
    found: list[str] = []
    for field in fields(VoiceConfig):
        factory = field.default_factory
        if factory is MISSING or not callable(factory):
            continue
        value = factory()
        if getattr(value, "enabled", None) is not None:
            found.append(field.name)
    return tuple(found)


VOICE_PARTS = _voice_parts()


def soul_yaml(
    language: str,
    locale: str,
    timezone: str,
    *,
    voice_parts: tuple[str, ...] | None = None,
) -> str:
    """The seed soul with required values; voice is a soul setting, not an engine section."""
    text = DEFAULT_SOUL_YAML.format(
        language=language,
        locale=locale,
        timezone=timezone,
    )
    body = yaml.safe_load(text) or {}
    # The voice values come from `VoiceConfig`, never from a second copy in this file.
    body["voice"] = _tree(VoiceConfig())
    if voice_parts is not None:
        selected = set(voice_parts)
        unknown = selected.difference(VOICE_PARTS)
        if unknown:
            raise ValueError(f"unknown voice part(s): {', '.join(sorted(unknown))}")
        voice = body["voice"]
        for part in VOICE_PARTS:
            voice[part]["enabled"] = part in selected
    return dump_soul(body)


def _search_provider(websearch: str) -> str:
    """The selected provider name; empty keeps the shipped default."""
    selected = str(websearch or "").strip().lower() or "ddgs"
    if selected not in WEB_SEARCH_PROVIDERS:
        known = ", ".join(sorted(WEB_SEARCH_PROVIDERS))
        raise ValueError(f"unknown web search provider '{selected}'; available: {known}")
    return selected


def _apply_websearch(config: dict, provider: str, url: str) -> None:
    """Point `web_search` at the operator's choice; keys stay as env variable names."""
    search = config["web_search"]
    search["provider"] = provider
    search["fallbacks"] = []
    search["base_url"] = url.strip().rstrip("/") if provider == "searxng" else ""
    search["api_key_env"] = WEB_SEARCH_API_KEYS.get(provider, "")
    search["engine_id_env"] = WEB_SEARCH_ENGINE_KEYS.get(provider, "")
    search["ddgs_backend"] = "auto"
    if provider == "off":
        config["engine"]["research"]["mode"] = "off"


def files(
    *,
    provider: str,
    model: str = "",
    language: str,
    locale: str,
    timezone: str,
    voice_enabled: bool = False,
    voice_parts: tuple[str, ...] | None = None,
    websearch: str = "",
    websearch_url: str = "",
    tools: tuple[str, ...] | None = None,
    gateways: tuple[str, ...] | None = None,
    compose: bool = False,
    base_url: str = "",
    api_key_env: str = "",
) -> dict[str, str]:
    """Every scaffold file: relative path -> content.

    `provider` and `model` are the operator's pick; the scaffold never ships a model id of
    its own, so a fresh config fails loudly until one is chosen.
    """
    if provider not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"unknown provider '{provider}'; available: {known}")
    defaults = PROVIDERS[provider]
    config = copy.deepcopy(CONFIG)
    if compose:
        config["sandbox"].update(COMPOSE_SANDBOX)
    llm = config["engine"]["llm"]
    llm["provider"] = provider
    llm["base_url"] = str(base_url or "").strip() or defaults.base_url
    llm["api_key_env"] = str(api_key_env or "").strip() or defaults.api_key_env
    llm["model"] = model
    selected = _search_provider(websearch)
    _apply_websearch(config, selected, websearch_url)
    if gateways is not None:
        index = gateway_index_snapshot()
        config["gateways"] = {
            name: gateway_block(name, index=index) for name in dict.fromkeys(gateways)
        }
    roles = copy.deepcopy(ROLES)
    seed_gateway_policies(roles, config["gateways"])
    if tools is not None:
        roles["member"]["tools"] = list(tools)
    parts = (
        voice_parts
        if voice_parts is not None
        else (VOICE_PARTS if voice_enabled else ())
    )
    return {
        "config.yaml": dump_config(config),
        "roles.yaml": dump_roles(roles),
        "soul.yaml": soul_yaml(
            language,
            locale,
            timezone,
            voice_parts=parts,
        ),
        ".env": env_file(provider, selected, llm["api_key_env"]),
    }
