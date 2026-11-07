"""ADR-0001/0045: one soul.voice section, master off, and capability-checked sampling."""

from pathlib import Path

import pytest
import yaml
from support import test_dsn as db_url

from monoagens.engine.config import ConfigError, load_config
from monoagens.engine.soul import load_soul

SOUL = """\
name: MonoAgens
language: en
locale: en-US
timezone: UTC
pronouns: I
persona: p
"""


def write_folder(
    tmp_path: Path, *, engine: dict, soul: str = SOUL, provider: str = "custom"
) -> None:
    body = {
        "database": {"url_env": "", "url": db_url()},
        "workspace": ".monoagens/workspace",
        "roles_file": "roles.yaml",
        "soul_file": "soul.yaml",
        "engine": {
            "default_role": "guest",
            "llm": {"provider": provider, "base_url": "http://x", "api_key_env": "K", "model": "m"},
            **engine,
        },
        "gateways": {"whatsapp": {"enabled": True}},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    (tmp_path / "roles.yaml").write_text(
        "roles:\n  guest:\n    name: guest\n    tools: []\n", encoding="utf-8"
    )
    (tmp_path / "soul.yaml").write_text(soul, encoding="utf-8")


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("K", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "x")


def test_missing_voice_uses_safe_defaults() -> None:
    soul = load_soul(yaml.safe_load(SOUL))

    assert soul.voice.character.enabled is False
    assert soul.voice.profanity == "avoid"
    assert soul.voice.safety == "inherit"
    assert soul.voice.sampling.top_p is None
    assert soul.voice.sampling.top_k is None
    assert soul.voice.sampling.min_p is None


def test_voice_parses_tuning_per_block() -> None:
    soul = load_soul(
        yaml.safe_load(
            SOUL
            + """
voice:
  sampling:
    daily_temperature: 1.1
    professional_temperature: 0.2
    top_p: 0.8
  character:
    enabled: true
    temperature: 0.5
  profanity: free
"""
        )
    )

    assert soul.voice.sampling.daily_temperature == 1.1
    assert soul.voice.character.enabled is True
    assert soul.voice.profanity == "free"


def test_removed_voice_master_switch_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"voice\.enabled"):
        load_soul(yaml.safe_load(SOUL + "\nvoice:\n  enabled: true\n"))


def test_invalid_voice_value_is_rejected() -> None:
    with pytest.raises(ValueError, match="profanity"):
        load_soul(yaml.safe_load(SOUL + "\nvoice:\n  profanity: loud\n"))


def test_engine_voice_section_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("K", "x")
    write_folder(tmp_path, engine={"character": {"enabled": True}})

    with pytest.raises(ConfigError, match=r"moved to soul\.voice"):
        load_config(tmp_path / "config.yaml", require_key=False)


def test_engine_temperature_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("K", "x")
    write_folder(tmp_path, engine={"llm": {
        "provider": "custom", "base_url": "http://x", "api_key_env": "K",
        "model": "m", "temperature": 0.9,
    }})

    with pytest.raises(ConfigError, match=r"moved to soul\.voice\.sampling"):
        load_config(tmp_path / "config.yaml", require_key=False)


def test_openai_wire_rejects_top_k(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("K", "x")
    write_folder(
        tmp_path,
        engine={},
        soul=SOUL + "\nvoice:\n  sampling:\n    top_k: 40\n",
    )

    with pytest.raises(ConfigError, match=r"top_k.*not supported"):
        load_config(tmp_path / "config.yaml", require_key=False)


def test_openai_wire_rejects_safety_off(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("K", "x")
    write_folder(tmp_path, engine={}, soul=SOUL + "\nvoice:\n  safety: off\n")

    with pytest.raises(ConfigError, match=r"safety: off.*not supported"):
        load_config(tmp_path / "config.yaml", require_key=False)


def test_native_wire_accepts_top_k_and_safety_off(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    write_folder(
        tmp_path,
        engine={},
        soul=SOUL + "\nvoice:\n  safety: off\n  sampling:\n    top_k: 40\n",
        provider="google",
    )

    config = load_config(tmp_path / "config.yaml")

    assert config.llm.wire == "gemini"
    assert config.llm.native is True
    assert config.soul.voice.safety == "off"
    assert config.soul.voice.sampling.top_k == 40


def test_voice_speech_selection_parses_and_unknown_keys_are_rejected() -> None:
    soul = load_soul(
        yaml.safe_load(
            SOUL + "\nvoice:\n  speech:\n    enabled: true\n    stt: openai\n    tts: elevenlabs\n"
        )
    )

    assert soul.voice.speech.enabled is True
    assert soul.voice.speech.stt == "openai"
    assert soul.voice.speech.tts == "elevenlabs"

    with pytest.raises(ValueError, match=r"voice\.speech\.bogus"):
        load_soul(yaml.safe_load(SOUL + "\nvoice:\n  speech:\n    enabled: true\n    bogus: x\n"))


def test_soul_speech_selection_requires_a_configured_endpoint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("K", "x")
    write_folder(
        tmp_path,
        engine={},
        soul=SOUL + "\nvoice:\n  speech:\n    enabled: true\n    stt: openai\n",
    )

    with pytest.raises(ConfigError, match=r"soul\.voice\.speech\.stt"):
        load_config(tmp_path / "config.yaml", require_key=False)
