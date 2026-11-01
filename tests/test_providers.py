"""Provider contract: a name fills client defaults, explicit values win, unknown names fail."""

from __future__ import annotations

import pytest
import yaml

from eidesoul.engine.config import ConfigError, load_config
from eidesoul.providers import PROVIDERS, resolve

SOUL = "name: Eidesoul\nlanguage: en\npronouns: aku\n"
ROLES = {"roles": {"guest": {"name": "guest", "tools": ["read"], "commands": ["help"]}}}


def write_folder(tmp_path, llm: dict) -> None:
    body = {
        "database": {"url": "sqlite+pysqlite://", "url_env": ""},
        "workspace": ".eidesoul/workspace",
        "roles_file": "roles.yaml",
        "soul_file": "soul.yaml",
        "engine": {"llm": llm, "default_role": "guest"},
        "gateways": {"cli": {"enabled": True, "role": "guest"}},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    (tmp_path / "roles.yaml").write_text(yaml.safe_dump(ROLES, sort_keys=False), encoding="utf-8")
    (tmp_path / "soul.yaml").write_text(SOUL, encoding="utf-8")


def test_google_defaults_use_the_openai_compatible_endpoint():
    assert resolve("google", "", "", "") == (
        "google",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY",
        "gemini-2.0-flash",
    )


def test_explicit_values_win_over_the_provider_defaults():
    assert resolve("groq", "http://proxy/v1", "MY_KEY", "my-model") == (
        "groq",
        "http://proxy/v1",
        "MY_KEY",
        "my-model",
    )


def test_partial_override_keeps_the_rest_from_the_provider():
    assert resolve("openai", "", "AZURE_KEY", "") == (
        "openai",
        PROVIDERS["openai"].base_url,
        "AZURE_KEY",
        PROVIDERS["openai"].model,
    )


def test_custom_requires_an_explicit_base_url():
    with pytest.raises(ValueError, match="requires llm.base_url"):
        resolve("custom", "", "", "")


def test_unknown_provider_lists_the_available_names():
    with pytest.raises(ValueError, match="unknown provider 'nope'; available: custom, deepseek"):
        resolve("nope", "", "", "")


def test_ollama_needs_no_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    write_folder(tmp_path, {"provider": "ollama"})
    config = load_config(tmp_path / "config.yaml")
    assert config.llm.base_url == PROVIDERS["ollama"].base_url
    assert config.api_key() == "ollama"


def test_missing_key_for_a_hosted_provider_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    write_folder(tmp_path, {"provider": "google"})
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        load_config(tmp_path / "config.yaml")


def test_unknown_provider_becomes_a_config_error(tmp_path):
    write_folder(tmp_path, {"provider": "mystery"})
    with pytest.raises(ConfigError, match="unknown provider 'mystery'"):
        load_config(tmp_path / "config.yaml")
