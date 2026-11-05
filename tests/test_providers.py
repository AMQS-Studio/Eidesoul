"""Provider contract: a name fills the endpoint, the model is the operator's, unknown names fail."""


import pytest
import yaml

from monoagens.admin import templates
from monoagens.engine.config import ConfigError, load_config
from monoagens.providers import PROVIDERS, resolve

SOUL = "name: MonoAgens\nlanguage: en\nlocale: en-US\ntimezone: UTC\npronouns: aku\n"
ROLES = {"roles": {"guest": {"name": "guest", "tools": ["read"], "commands": ["help"]}}}


def write_folder(tmp_path, llm: dict) -> None:
    body = {
        "database": {
        "url": "postgresql+psycopg://monoagens:monoagens@127.0.0.1:5433/monoagens_test",
        "url_env": "",
    },
        "workspace": ".monoagens/workspace",
        "roles_file": "roles.yaml",
        "soul_file": "soul.yaml",
        "engine": {"llm": llm, "default_role": "guest"},
        "gateways": {"whatsapp": {"enabled": True, "role": "guest"}},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    (tmp_path / "roles.yaml").write_text(yaml.safe_dump(ROLES, sort_keys=False), encoding="utf-8")
    (tmp_path / "soul.yaml").write_text(SOUL, encoding="utf-8")


def test_google_defaults_use_the_native_generate_content_wire():
    name, defaults = resolve("google", "", "")

    assert name == "google"
    assert defaults.base_url == "https://generativelanguage.googleapis.com/v1beta"
    assert defaults.api_key_env == "GEMINI_API_KEY"
    assert defaults.wire == "gemini"
    assert defaults.safety_supported is True
    assert defaults.session_header == ""
    assert defaults.api_key_optional is False


def test_explicit_values_win_over_the_provider_defaults():
    name, defaults = resolve("groq", "http://proxy/v1", "MY_KEY")

    assert name == "groq"
    assert defaults.base_url == "http://proxy/v1"
    assert defaults.api_key_env == "MY_KEY"


def test_partial_override_keeps_the_rest_from_the_provider():
    name, defaults = resolve("openai", "", "AZURE_KEY")

    assert name == "openai"
    assert defaults.base_url == PROVIDERS["openai"].base_url
    assert defaults.api_key_env == "AZURE_KEY"


def test_anthropic_uses_the_native_messages_wire():
    name, defaults = resolve("anthropic", "", "")

    assert name == "anthropic"
    assert defaults.base_url == "https://api.anthropic.com/v1/"
    assert defaults.api_key_env == "ANTHROPIC_API_KEY"
    assert defaults.wire == "anthropic"


def test_opencode_lists_models_outside_its_chat_base_url():
    name, defaults = resolve("opencode", "", "")

    assert name == "opencode"
    assert defaults.models_url == "https://opencode.ai/inference/v1/models"
    assert defaults.base_url not in defaults.models_url


def test_custom_requires_an_explicit_base_url():
    with pytest.raises(ValueError, match=r"requires llm\.base_url"):
        resolve("custom", "", "")


def test_unknown_provider_lists_the_available_names():
    with pytest.raises(
        ValueError,
        match=(
            "unknown provider 'nope'; available: anthropic, custom, deepseek, google, groq, "
            "ollama, openai, opencode, openrouter"
        ),
    ):
        resolve("nope", "", "")


def test_the_model_comes_from_config_only(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "key")
    write_folder(tmp_path, {"provider": "opencode", "model": "chosen-by-operator"})

    config = load_config(tmp_path / "config.yaml")

    assert config.llm.model == "chosen-by-operator"


def test_a_missing_model_is_a_config_error(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "key")
    write_folder(tmp_path, {"provider": "opencode"})

    with pytest.raises(ConfigError, match=r"engine\.llm\.model is required"):
        load_config(tmp_path / "config.yaml")


def test_reasoning_effort_is_empty_until_the_operator_sets_it(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "key")
    write_folder(tmp_path, {"provider": "opencode", "model": "m"})
    assert load_config(tmp_path / "config.yaml").llm.reasoning_effort == ""

    write_folder(tmp_path, {"provider": "opencode", "model": "m", "reasoning_effort": " low "})
    assert load_config(tmp_path / "config.yaml").llm.reasoning_effort == "low"


def test_session_header_follows_the_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "key")
    write_folder(tmp_path, {"provider": "opencode", "model": "m"})
    config = load_config(tmp_path / "config.yaml")
    assert config.llm.session_headers("chat-1") == {"x-opencode-session": "chat-1"}

    monkeypatch.setenv("GEMINI_API_KEY", "key")
    write_folder(tmp_path, {"provider": "google", "model": "m"})
    config = load_config(tmp_path / "config.yaml")
    assert config.llm.session_header == ""
    assert config.llm.session_headers("chat-1") == {}


def test_ollama_needs_no_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    write_folder(tmp_path, {"provider": "ollama", "model": "m"})
    config = load_config(tmp_path / "config.yaml")
    assert config.llm.base_url == PROVIDERS["ollama"].base_url
    assert config.api_key() == "local"


def test_missing_key_for_a_hosted_provider_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    write_folder(tmp_path, {"provider": "google", "model": "m"})
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        load_config(tmp_path / "config.yaml")


def test_unknown_provider_becomes_a_config_error(tmp_path):
    write_folder(tmp_path, {"provider": "mystery", "model": "m"})
    with pytest.raises(ConfigError, match="unknown provider 'mystery'"):
        load_config(tmp_path / "config.yaml")


def test_the_scaffold_writes_the_operator_choice():
    files = templates.files(
        provider="groq", model="operator-model", language="en", locale="en-US", timezone="UTC"
    )
    body = yaml.safe_load(files["config.yaml"])
    llm = body["engine"]["llm"]

    assert llm["provider"] == "groq"
    assert llm["base_url"] == PROVIDERS["groq"].base_url
    assert llm["api_key_env"] == "GROQ_API_KEY"
    assert llm["model"] == "operator-model"
    assert llm["reasoning_effort"] == ""


def test_the_scaffold_ships_no_model_of_its_own():
    body = yaml.safe_load(
        templates.files(
            provider="groq", language="en", locale="en-US", timezone="UTC"
        )["config.yaml"]
    )

    assert body["engine"]["llm"]["model"] == ""


def test_the_scaffold_env_only_names_the_chosen_key():
    env = templates.files(provider="groq", language="en", locale="en-US", timezone="UTC")[".env"]

    assert env.startswith("GROQ_API_KEY=")
    assert "OPENCODE" not in env


def test_a_missing_provider_is_a_config_error(tmp_path):
    write_folder(tmp_path, {"model": "m"})

    with pytest.raises(ConfigError, match=r"engine\.llm\.provider is required"):
        load_config(tmp_path / "config.yaml")


def test_custom_needs_a_key_variable(tmp_path):
    write_folder(tmp_path, {"provider": "custom", "base_url": "http://x", "model": "m"})

    with pytest.raises(ConfigError, match=r"engine\.llm\.api_key_env is required"):
        load_config(tmp_path / "config.yaml")


def test_unknown_provider_is_rejected_by_the_scaffold():
    with pytest.raises(ValueError, match="unknown provider 'nope'"):
        templates.files(provider="nope", language="en", locale="en-US", timezone="UTC")
