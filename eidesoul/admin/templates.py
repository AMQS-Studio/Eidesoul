"""Default text for `eidesoul init`; one source of truth, no separate example files."""


import copy
from dataclasses import asdict
from pathlib import Path

import yaml

from ..engine.config import (
    DEFAULT_SENSITIVE_COMMANDS,
    DatabaseConfig,
    HostConfig,
    PluginSettings,
    SandboxConfig,
    WebSearchConfig,
)
from ..engine.soul import DEFAULT_SOUL_YAML, load_soul, style_block
from ..providers import PROVIDERS
from . import library

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
            workspace=Path(".eidesoul/workspace"),
            # Compose ships the sandbox on its own network with the proxy sidecar (compose.yaml).
            sandbox=SandboxConfig(
                network="eidesoul-sandbox", proxy="http://sandbox-proxy:3128"
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
    raw["plugins"] = {"google": asdict(PluginSettings())}
    host: dict = {}
    for key, fields in HOST_KEYS.items():
        value = raw[key]
        if fields:
            value = {name: value[name] for name in fields}
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
        "temperature": 0.9,
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
        "collection": "eidesoul_memories",
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
    "humanize": {
        "enabled": True,
        "cpms": 25.0,
        "min_delay": 0.5,
        "max_delay": 5.0,
        "between_delay": 0.7,
        "max_bubbles": 3,
        "reaction_stickers": [],
    },
    "style": {"enabled": True, "max_chars": 800},
    "character": {
        "enabled": False,
        "max_chars": 400,
        "max_tokens": 500,
        "temperature": 0.7,
        "toxicity": 2,
        "adult_memes": 1,
        "gender_gate": True,
        "gender_confidence": 0.6,
        "roast_jabs": 1,
        "gender_avatar": False,
        "max_questions": 1,
    },
    "guard": {
        "enabled": True,
        "model": "",
        "timeout_seconds": 6,
        "max_command_chars": 4000,
    },
    "affect": {
        "enabled": True,
        "volatility": "expressive",
        "grudge_half_life_hours": 1.5,
        "vibe": {
            "enabled": True,
            "attack": 0.35,
            "baseline_influence": 0.05,
            "hold_seconds": 1.2,
            "tick_seconds": 1.0,
        },
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
        "context_enabled": True,
        "context_hours": 6,
        "context_topics": 2,
        "context_results": 4,
    },
}

GATEWAY: dict = {
    "gateways": {
        "cli": {
            "enabled": True,
            "role": "administrator",
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
        "label": "",
        "protected": True,
        "description": "Administrator of Eidesoul",
        "priority": 100,
        "users": [],
        "tools": ["*"],
        "commands": ["*"],
        "memory_admin": True,
        "context": (
            "You are talking to the administrator of Eidesoul, who sets the direction, "
            "permissions, and soul.\nAllows corrections, config/soul changes, and full reports.\n"
        ),
    },
    "member": {
        "name": "member",
        "label": "",
        "description": "Committee member",
        "priority": 10,
        "users": [],
        "tools": [
            "read",
            "grep",
            "fetch",
            "web_search",
            "chat_history",
            "memory",
            "recipe",
            "google",
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
            "cari",
            "memori",
            "kirim",
            "mood",
        ],
        "memory_admin": False,
        "context": (
            "You are talking to a committee member. Help with their work without opening new "
            "permissions.\n"
        ),
    },
    "guest": {
        "name": "guest",
        "label": "",
        "description": "Unknown sender",
        "priority": 0,
        "users": [],
        "tools": ["read", "grep", "fetch", "web_search", "chat_history", "memory", "info"],
        "commands": [
            "help",
            "ping",
            "link",
            "unlink",
            "whoami",
            "status",
            "tools",
            "cari",
        ],
        "memory_admin": False,
        "context": (
            "You do not know this person yet. Stay polite and never open another chat's data.\n"
        ),
    },
}

ENV_TAIL = """# Values, never placeholders: `eidesoul init` writes the names, you fill the values.
# Postgres for this soul. The password is the same one `compose.yaml` reads.
EIDESOL_DATABASE_URL=
POSTGRES_PASSWORD=
# Optional Google plugin credentials; leave empty while the plugin is off.
GOOGLE_SERVICE_ACCOUNT_PATH=
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
"""


def env_file(provider: str) -> str:
    """`.env` content: only the chosen provider's key variable, then the shared tail."""
    key_env = PROVIDERS[provider].api_key_env
    head = f"{key_env}=\n" if key_env else ""
    return head + ENV_TAIL


def dump(body: object) -> str:
    return yaml.safe_dump(body, sort_keys=False, allow_unicode=True)


def soul_yaml(language: str, locale: str, timezone: str) -> str:
    """The seed soul with the operator's three required values, plus the derived style."""
    text = DEFAULT_SOUL_YAML.format(language=language, locale=locale, timezone=timezone)
    soul = load_soul(yaml.safe_load(text) or {})
    style = library.seed_style(library.soul_text(soul), soul.language)
    return text + style_block(style)


def files(
    *,
    provider: str,
    model: str = "",
    language: str,
    locale: str,
    timezone: str,
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
    return {
        "config.yaml": dump(config),
        "roles.yaml": dump({"roles": ROLES}),
        "soul.yaml": soul_yaml(language, locale, timezone),
        ".env": env_file(provider),
    }
