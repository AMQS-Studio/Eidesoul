"""Default text for `monoagens init`; one source of truth, no separate example files."""


import copy
import re
from dataclasses import MISSING, asdict, fields
from pathlib import Path

import yaml

from ..engine.config import (
    DEFAULT_SENSITIVE_COMMANDS,
    DatabaseConfig,
    HostConfig,
    SandboxConfig,
    WebSearchConfig,
)
from ..engine.soul import DEFAULT_SOUL_YAML, VoiceConfig
from ..providers import PROVIDERS

# The operator surface of `config.yaml`: the order and the keys the file shows. The values come
# from the dataclass tree below, so a default can never drift between the code and the template
# (ADR-0031 §4.6); a nested section the file does not expose (`web_search.fetch`) is dropped here.
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
    "web_search": ("base_url", "region", "timeout_seconds"),
    "mcp": ("servers",),
    "plugin": ("root", "fetch", "source"),
    "plugins": (),
}


def host_defaults() -> dict:
    """The host block of `config.yaml`, derived from the dataclass tree (ADR-0031 §4.6)."""
    raw = asdict(
        HostConfig(
            database=DatabaseConfig(),
            workspace=Path(".monoagens/workspace"),
            # Compose ships the sandbox on its own network with the proxy sidecar (compose.yaml).
            sandbox=SandboxConfig(
                network="monoagens-sandbox", proxy="http://sandbox-proxy:3128"
            ),
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
        if names:
            value = {name: value[name] for name in names}
            value = {
                name: int(item)
                if isinstance(item, float) and item.is_integer() and name != "cpus"
                else item
                for name, item in value.items()
            }
        if isinstance(value, (str, int, float, bool)) or value is None or isinstance(value, dict):
            host[key] = value
        else:
            host[key] = str(value)
    for key in ("workspace", "roles_file", "soul_file"):
        host[key] = Path(host[key]).as_posix()
    return host


HOST: dict = host_defaults()

ENGINE: dict = {
    "llm": {
        "provider": "",
        "base_url": "",
        "model": "",
        "api_key_env": "",
        "reasoning_effort": "",
        "timeout_seconds": 120,
        "utility_temperature": 0.2,
        "run_budget_seconds": 240,
        "retry": {
            "turn_attempts": 1,
            "worker_attempts": 3,
            "base_seconds": 2,
            "cap_seconds": 60,
            "jitter": 0.2,
            "retry_after_max_seconds": 10,
        },
    },
    "logging": {"format": "text", "level": "INFO"},
    "metrics": {"enabled": True, "window_seconds": 300, "retention_days": 90},
    "automations": {
        "enabled": True,
        "kill_switch": False,
        "default_budget_per_day": 4,
        "run_timeout_seconds": 120,
        "max_steps": 10,
        "min_interval_seconds": 60,
        "max_occurrences_per_day": 24,
        "default_quiet_hours": "22:00-07:00",
        "default_quiet_policy": "skip",
        "max_active_per_person": 10,
        "sandbox": {"programs_root": "workspace"},
        "condition": {"nl_criteria": True, "nl_budget_tokens_per_day": 20000},
        "retry": {
            "max_attempts": 3,
            "backoff_seconds": 30,
            "max_backoff_seconds": 900,
            "jitter": True,
            "respect_retry_after": True,
        },
        "resilience": {
            "catchup_minutes": 15,
            "max_stale_days": 7,
            "stale_claim_seconds": 300,
            "missed_apology": True,
            "apology_digest": True,
            "apology_respect_quiet_hours": True,
        },
    },
    "default_role": "guest",
    "turn": {"timeout_seconds": 90, "min_call_seconds": 5},
    "turns": {
        "resume": True,
        "max_attempts": 2,
        "max_age_seconds": 900,
        "stale_after_seconds": 21600,
        "partial_delivery": "skip",
        "max_resume_per_chat": 5,
        "max_resume_per_minute": 10,
        "backlog_policy": "per_message_then_consolidate",
        "shutdown": {
            "grace_seconds": 25,
            "drain_seconds": 15,
            "notice_seconds": 5,
            "notice_per_chat": True,
            "notice_retry": {"attempts": 2, "backoff_seconds": 1},
            "resume_notice": {"after_seconds": 300},
            "stop_grace_period": 30,
        },
    },
    "worker": {
        "retry": {"attempts": 5, "backoff_seconds": 2, "dead_letter_after": 5},
        "reaper": {"every_seconds": 30, "batch": 100},
    },
    "io": {"workers": 8},
    "concurrency": {"max_chat_locks": 256},
    "memory": {
        "backend": "mem0",
        "auto_capture": True,
        "capture": {"mode": "balanced", "min_chars": 8, "novelty_threshold": 0.88},
        "worker": {
            "tick_seconds": 1,
            "concurrency": 2,
            "capture_max_items": 20,
            "consolidation_every_seconds": 60,
            "audit_every_seconds": 86400,
            "queue_cap": 5000,
            "retry": {"attempts": 5, "backoff_seconds": 2, "dead_letter_after": 5},
        },
        "max_items": 12,
        "max_chars": 3000,
        "collection": "monoagens_memories",
        "extraction_model": "",
        "privacy": {
            "enabled": True,
            "default_level": 2,
            "ask_level": 4,
            "group_max_level": 2,
            "dm_max_level": 4,
            "auditor": True,
            "group_capture": {"self_only": True, "third_party": "drop", "ask_in_group": False},
        },
        "intent": {
            "remember": {"enabled": True, "floor": 0.62, "margin": 0.06},
            "forget": {"enabled": True, "floor": 0.64, "margin": 0.06},
        },
        "retention": {"purge": {"grace_days": 1, "batch": 500}},
        "telemetry": False,
    },
    "reflection": {
        "enabled": True,
        "every_minutes": 60,
        "idle_minutes": 30,
        "min_new_rows": 25,
        "min_candidates": 1,
        "scopes_per_pass": 1,
        "items_per_pass": 40,
        "writes_per_pass": 20,
        "seconds_per_pass": 60,
        "passes_per_day": 24,
        "derive_depth": 1,
        "dispute_ttl_days": 30,
        "questions_per_day": 1,
    },
    "approval": {
        "mode": "yolo",
        "sensitive_tools": [],
        "sensitive_commands": list(DEFAULT_SENSITIVE_COMMANDS),
        "settings": True,
        "ttl_seconds": 900,
    },
    "skills": {
        "enabled": True,
        "task_idle_minutes": 10,
        "idle_after_send_minutes": 3,
        "per_episode": 1,
        "max_chars": 800,
        "max_body_chars": 18000,
        "use_renewal_days": 30,
        "efficiency": {"enabled": True, "min_evidence_episodes": 2, "window_days": 30},
        "ladder": {"enabled": True, "proposal_cooldown_days": 14},
    },
    "prompt": {"salience": True, "max_chars": 9000, "thread_chars": 4000},
    "research": {
        "mode": "auto",
        "require_citation": True,
        "freshness_hours": 24,
        "max_results": 5,
    },
    "guard": {
        "enabled": True,
        "model": "",
        "timeout_seconds": 6,
        "max_command_chars": 4000,
    },
    "ambient": {
        "enabled": True,
        "tick_seconds": 10,
        "digest_every": 12,
        "digest_cooldown": 900,
        "history_window": 30,
        "idle_minutes": 20,
        "research_cooldown": 21600,
        "research_per_day": 6,
        "research_results": 5,
    },
}

GATEWAY: dict = {
    "gateways": {
        "cli": {
            "enabled": True,
            "user": "local",
            "thread_id": "cli:local",
            "chat_name": "Terminal",
        },
    }
}

CONFIG: dict = {
    **HOST,
    "engine": ENGINE,
    "gateways": GATEWAY["gateways"],
}

ROLES: dict[str, dict] = {
    "administrator": {
        "name": "administrator",
        "label": "local",
        "protected": True,
        "description": "Administrator of MonoAgens",
        "priority": 100,
        "users": [],
        "tools": ["*"],
        "commands": ["*"],
        "memory_admin": True,
        "context": (
            "You are talking to the administrator of MonoAgens, who sets the direction, "
            "permissions, and soul.\n"
            "Allows corrections, config/soul changes, and full reports.\n"
        ),
    },
    "member": {
        "name": "member",
        "label": "",
        "description": "Member of this deployment",
        "priority": 10,
        "users": [],
        "tools": [
            "read",
            "grep",
            "fetch",
            "web_search",
            "chat_history",
            "memory",
            "info",
        ],
        "commands": [
            "help",
            "ping",
            "link",
            "unlink",
            "whoami",
            "status",
            "tools",
        ],
        "memory_admin": False,
        "context": (
            "You are talking to a member. Help with their work without opening new "
            "permissions.\n"
        ),
    },
    "guest": {
        "name": "guest",
        "label": "",
        "description": "Unknown sender",
        "priority": 0,
        "users": [],
        "tools": ["read", "grep", "fetch", "web_search", "chat_history", "info"],
        "commands": ["help", "ping", "link", "unlink", "whoami", "status", "tools"],
        "memory_admin": False,
        "context": (
            "You do not know this person yet. Stay polite and never open another chat's data.\n"
        ),
    },
}

ENV_TAIL = """# Values, never placeholders: `monoagens init` writes the names, you fill the values.
# Postgres for this soul. The password is the same one `compose.yaml` reads.
MONOAGENS_DATABASE_URL=
POSTGRES_PASSWORD=
"""


def env_file(provider: str) -> str:
    """`.env` content: only the chosen provider's key variable, then the shared tail."""
    key_env = PROVIDERS[provider].api_key_env
    head = f"{key_env}=\n" if key_env else ""
    return head + ENV_TAIL


def dump(body: object) -> str:
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
    "web_search": "Search endpoint and fetch policy.",
    "web_search.base_url": "SearXNG base URL; empty uses the ddgs backend.",
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
    "engine.llm": "Provider wire, model, and operational limits.",
    "engine.llm.provider": "Provider name selected at init; no code default.",
    "engine.llm.base_url": "Provider endpoint; empty uses the provider record.",
    "engine.llm.model": "Model id selected by the operator; no code default.",
    "engine.llm.api_key_env": "Environment variable that holds the provider key.",
    "engine.llm.reasoning_effort": "Reasoning effort; empty uses the provider default.",
    "engine.llm.timeout_seconds": "Per-call model timeout. Default: 120.",
    "engine.llm.utility_temperature": (
        "Temperature for decisions, summaries, and extraction. Default: 0.2."
    ),
    "engine.llm.run_budget_seconds": "Wall-clock budget for one turn. Default: 240.",
    "engine.llm.retry": "Retry policy for the turn and background paths.",
    "engine.llm.retry.turn_attempts": "Turn attempts. Default: 1.",
    "engine.llm.retry.worker_attempts": "Background attempts. Default: 3.",
    "engine.llm.retry.base_seconds": "Initial backoff. Default: 2.",
    "engine.llm.retry.cap_seconds": "Backoff cap. Default: 60.",
    "engine.llm.retry.jitter": "Backoff jitter ratio. Default: 0.2.",
    "engine.llm.retry.retry_after_max_seconds": "Cap on a provider Retry-After. Default: 10.",
    "engine.logging": "Log format and level.",
    "engine.logging.format": "text or json. Default: text.",
    "engine.logging.level": "Standard logging level. Default: INFO.",
    "engine.metrics": "Cost metrics retention and window.",
    "engine.metrics.enabled": "Record llm_calls. Default: true.",
    "engine.metrics.window_seconds": "Status window. Default: 300.",
    "engine.metrics.retention_days": "Metric retention. Default: 90.",
    "engine.automations": "Scheduled automations and their limits.",
    "engine.default_role": "Role for an unknown sender. Default: guest.",
    "engine.turn": "One-turn deadline.",
    "engine.turn.timeout_seconds": "Turn deadline. Default: 90.",
    "engine.turn.min_call_seconds": (
        "Do not start a model call below this remaining time. Default: 5."
    ),
    "engine.turns": "Turn lifecycle, resume, and shutdown.",
    "engine.worker": "Background worker retry and reaper.",
    "engine.io": "Blocking I/O worker threads.",
    "engine.io.workers": "Worker thread count. Default: 8.",
    "engine.concurrency": "Per-chat concurrency bounds.",
    "engine.concurrency.max_chat_locks": "Maximum chat locks. Default: 256.",
    "engine.memory": "Memory store, capture, privacy, and retention.",
    "engine.memory.collection": "Mem0 collection name.",
    "engine.memory.auto_capture": "Capture durable facts automatically. Default: true.",
    "engine.memory.max_items": "Memory items per prompt. Default: 12.",
    "engine.memory.max_chars": "Memory prompt budget. Default: 3000.",
    "engine.memory.extraction_model": "Extraction model; empty uses engine.llm.model.",
    "engine.memory.telemetry": "Send memory telemetry. Default: false.",
    "engine.prompt": "Prompt assembly and salience.",
    "engine.prompt.salience": "Select volatile blocks by salience. Default: true.",
    "engine.prompt.max_chars": "Volatile prompt budget. Default: 9000.",
    "engine.prompt.thread_chars": "Thread window budget. Default: 4000.",
    "engine.research": "Model prior, websearch triggers, and citation verification.",
    "engine.research.mode": "off, auto, or always. Default: auto.",
    "engine.research.require_citation": "Require a real [Rn] for current claims. Default: true.",
    "engine.research.freshness_hours": (
        "Maximum source age for a current claim; 0 disables. Default: 24."
    ),
    "engine.research.max_results": "Search result count, 1-10. Default: 5.",
    "engine.guard": "Deterministic and classifier guard limits.",
    "engine.guard.enabled": "Run the guard. Default: true.",
    "engine.guard.model": "Classifier model; empty uses engine.llm.model.",
    "engine.guard.timeout_seconds": "Classifier timeout. Default: 6.",
    "engine.guard.max_command_chars": "Maximum command length scanned. Default: 4000.",
    "engine.ambient": "Ambient background work.",
    "gateways": "Transport bodies; each is off until enabled.",
}


def comment_for(path: str, value: object) -> str:
    """One comment per key; the fallback still names the path and default."""
    found = COMMENTS.get(path)
    if found:
        return found
    if isinstance(value, dict):
        return f"{path} — operator section."
    return f"{path} — operator setting. Default: {yaml.safe_dump(value).strip()}"


def _scalar(value: object) -> str:
    rendered = yaml.safe_dump(value, sort_keys=False, allow_unicode=True).strip()
    return rendered.removesuffix("\n...")


def dump_config(body: dict) -> str:
    """Render the generated config with a comment above every key at every depth."""
    lines: list[str] = []

    def walk(node: dict, prefix: str, indent: int) -> None:
        pad = "  " * indent
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            for text in comment_for(path, value).splitlines():
                lines.append(f"{pad}# {text}")
            if isinstance(value, dict):
                lines.append(f"{pad}{key}:")
                if value:
                    walk(value, path, indent + 1)
                else:
                    lines.append(f"{pad}  {{}}")
            elif isinstance(value, list) and value:
                lines.append(f"{pad}{key}:")
                for item in value:
                    lines.append(f"{pad}  - {_scalar(item)}")
            else:
                lines.append(f"{pad}{key}: {_scalar(value)}")

    walk(body, "", 0)
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
    voice_enabled: bool = False,
    voice_parts: tuple[str, ...] | None = None,
) -> str:
    """The seed soul with required values; voice is a soul setting, not an engine section."""
    text = DEFAULT_SOUL_YAML.format(
        language=language,
        locale=locale,
        timezone=timezone,
        voice_enabled=str(bool(voice_enabled)).lower(),
    )
    if voice_parts is None:
        return text
    selected = set(voice_parts)
    unknown = selected.difference(VOICE_PARTS)
    if unknown:
        raise ValueError(f"unknown voice part(s): {', '.join(sorted(unknown))}")
    for part in VOICE_PARTS:
        flag = "true" if part in selected else "false"
        text = re.sub(
            rf"(?m)^(  {re.escape(part)}:\n    enabled: )\w+$",
            rf"\g<1>{flag}",
            text,
        )
    return text


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
    llm = config["engine"]["llm"]
    llm["provider"] = provider
    llm["base_url"] = defaults.base_url
    llm["api_key_env"] = defaults.api_key_env
    llm["model"] = model
    if websearch == "searxng":
        config["web_search"]["base_url"] = websearch_url.strip().rstrip("/")
    elif websearch == "off":
        config["web_search"]["base_url"] = ""
        config["engine"]["research"]["mode"] = "off"
    elif websearch == "ddgs":
        config["web_search"]["base_url"] = ""
    if gateways is not None:
        config["gateways"] = {
            name: copy.deepcopy(GATEWAY["gateways"].get(name, {"enabled": True}))
            for name in gateways
        }
    roles = copy.deepcopy(ROLES)
    if tools is not None:
        roles["member"]["tools"] = list(tools)
    return {
        "config.yaml": dump_config(config),
        "roles.yaml": dump({"roles": roles}),
        "soul.yaml": soul_yaml(
            language,
            locale,
            timezone,
            voice_enabled=voice_enabled,
            voice_parts=voice_parts,
        ),
        ".env": env_file(provider),
    }
