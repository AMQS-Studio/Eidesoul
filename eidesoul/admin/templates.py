"""Default text for `eidesoul init`; one source of truth, no separate example files."""

from __future__ import annotations

import copy

import yaml

from ..engine.config import DEFAULT_SENSITIVE_COMMANDS
from ..engine.soul import DEFAULT_SOUL_YAML, load_soul, style_block
from ..providers import PROVIDERS
from . import library

HOST: dict = {
    "database": {
        "url_env": "EIDESOUL_DATABASE_URL",
        "url": "",
        "pool_size": 5,
        "pool_timeout": 30,
    },
    "workspace": ".eidesoul/workspace",
    "roles_file": "roles.yaml",
    "soul_file": "soul.yaml",
    "sync_documents": True,
    "sandbox": {
        "runtime": "",
        "image": "docker.io/library/python:3.12-slim",
        "timeout_seconds": 120,
        "network": "eidesoul-sandbox",
        "host_workspace": None,
        "env": [],
        "proxy": "http://sandbox-proxy:3128",
        "pids_limit": 256,
        "memory": "512m",
        "cpus": 1.0,
    },
    "web_search": {"base_url": "", "timeout_seconds": 10},
    "mcp": {"servers": {}},
}

ENGINE: dict = {
    "llm": {
        "provider": "opencode",
        "base_url": "https://opencode.ai/inference/go/openai/v1",
        "model": "",
        "api_key_env": "OPENCODE_GO_API_KEY",
        "reasoning_effort": "",
        "timeout_seconds": 120,
        "temperature": 0.9,
        "run_budget_seconds": 240,
    },
    "default_role": "guest",
    "reply": {"quiet_seconds": 600},
    "memory": {
        "backend": "mem0",
        "auto_capture": True,
        "capture_min_chars": 8,
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
        },
        "telemetry": False,
    },
    "approval": {
        "mode": "yolo",
        "sensitive_tools": [],
        "sensitive_commands": list(DEFAULT_SENSITIVE_COMMANDS),
        "settings": True,
        "ttl_seconds": 900,
    },
    "skills": {"max_index_chars": 3000},
    "prompt": {"salience": True, "max_chars": 9000},
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
            "role": "owner",
            "user": "local",
            "chat_id": "cli:local",
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
    "owner": {
        "name": "owner",
        "description": "Owner and admin of Eidesoul",
        "priority": 100,
        "users": [],
        "tools": ["*"],
        "commands": ["*"],
        "memory_admin": True,
        "context": (
            "You are talking to the owner of Eidesoul, who sets the direction, permissions, and "
            "soul.\nAllows corrections, config/soul changes, and full reports.\n"
        ),
    },
    "member": {
        "name": "member",
        "description": "Committee member",
        "priority": 10,
        "users": [],
        "tools": [
            "read",
            "web_search",
            "chat_history",
            "memory",
            "skill",
            "recipe",
            "google",
        ],
        "commands": [
            "help",
            "ping",
            "reset",
            "whoami",
            "status",
            "tools",
            "cari",
            "memori",
            "skill",
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
        "description": "Unknown sender",
        "priority": 0,
        "users": [],
        "tools": ["read", "web_search", "chat_history", "memory"],
        "commands": ["help", "ping", "reset", "whoami", "status", "tools", "cari"],
        "memory_admin": False,
        "context": (
            "You do not know this person yet. Stay polite and never open another chat's data.\n"
        ),
    },
}

ENV = """OPENCODE_GO_API_KEY=
EIDESOUL_DATABASE_URL=postgresql+psycopg://eidesoul:CHANGE_ME@127.0.0.1:5432/eidesoul
GOOGLE_SERVICE_ACCOUNT_PATH=
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
"""


def dump(body: object) -> str:
    return yaml.safe_dump(body, sort_keys=False, allow_unicode=True)


def soul_yaml() -> str:
    """The seed soul plus the style its own text resolves to; never loads the embedder."""
    soul = load_soul(yaml.safe_load(DEFAULT_SOUL_YAML) or {})
    style = library.seed_style(library.soul_text(soul), soul.language)
    return DEFAULT_SOUL_YAML + style_block(style)


def files(*, provider: str = "opencode", model: str = "") -> dict[str, str]:
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
        "soul.yaml": soul_yaml(),
        ".env": ENV,
    }
