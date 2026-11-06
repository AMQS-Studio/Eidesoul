"""Language selector contract: YAML `soul.language`, /language command, prompt line.

Also the language-data contract (ADR-0000 §4.2): the packs under `monoagens/library/lexicon/`
own every language-specific list, the engine holds no Indonesian literal from those lists, the
pronoun resolves from the catalog, and a missing pack degrades without borrowing.
"""


import asyncio
import json
from pathlib import Path

import pytest
import yaml
from support import inbound_message, make_agent, make_runtime

from monoagens.engine import guard
from monoagens.engine.access.roles import Role
from monoagens.engine.character import lexicon
from monoagens.engine.soul import Soul, normalize_language
from monoagens.engine.turn.prompts import system_prompt

CHAT = "console:local"
ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "monoagens" / "engine"
LEXICON_DIR = ROOT / "monoagens" / "library" / "lexicon"
WORD_LISTS = ("negations", "stopwords", "assistant_tells", "injection")

# Language data the pack schema does not model (register particles, style markers) plus the
# ".ini" file-extension literal that a word-boundary scan reads as the Indonesian demonstrative.
ALLOWED_LITERALS = {
    "turn/context.py": {"ini"},
    "stores/style.py": {"aja", "deh", "kan", "kok", "loh", "sih"},
    "access/target.py": {"deh"},
}


def run_command(env, name: str, args: str = "") -> str:
    roles = (env.config.roles.default_role(),)
    return asyncio.run(
        env.agent._command(
            name, args, CHAT, "local", roles, env.gateway, inbound_message(f"/{name}")
        )
    )


def run_language(env, args: str) -> str:
    return run_command(env, "language", args)


def test_language_command_writes_soul_document_and_mirror(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    assert env.agent.soul.language == "id"

    result = run_language(env, "en")

    assert result == "🗣️ Reply language set to en."
    assert env.agent.soul.language == "en"
    assert "language: en" in env.documents.get("soul")
    mirror = yaml.safe_load(env.paths.soul_yaml.read_text(encoding="utf-8"))
    assert mirror["language"] == "en"
    assert run_language(env, "") == "🗣️ Reply language: en. Available: en, id."
    assert run_command(env, "ping") == "🏓 Pong! MonoAgens is alive and ready."

    assert run_language(env, "id") == "🗣️ Bahasa balasan diatur ke id."
    assert env.agent.soul.language == "id"
    assert run_command(env, "ping") == "🏓 Pong! MonoAgens hidup dan siap."


def test_language_command_rejects_unknown_language(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    result = run_language(env, "xx")
    assert result == "error: bahasa 'xx' tidak tersedia. Pilih: en, id."
    assert env.agent.soul.language == "id"


def test_member_without_permission_cannot_switch(tmp_path) -> None:
    env = make_agent(
        tmp_path,
        roles={"member": Role("member", tools=("read",), commands=("help",))},
    )
    result = run_language(env, "en")
    assert "tidak diizinkan" in result
    assert env.agent.soul.language == "id"


def test_normalize_language_aliases(monkeypatch) -> None:
    assert normalize_language("en") == "en"
    assert normalize_language("English") == "en"
    assert normalize_language("id") == "id"
    monkeypatch.setenv("LC_ALL", "")
    monkeypatch.setenv("LANG", "id_ID.UTF-8")
    assert normalize_language("auto") == "id"
    with pytest.raises(ValueError, match="required"):
        normalize_language("")


def test_system_prompt_language_follows_the_yaml_value(tmp_path) -> None:
    env = make_agent(
        tmp_path, soul=Soul(persona="p", language="en", locale="en-US", timezone="UTC")
    )
    prompt = system_prompt(env.config, (Role("member"),))
    assert "Reply in English." in prompt
    assert "Indonesian" not in prompt


# --------------------------------------------------------------- language data (ADR-0009)

def test_library_directory_is_gone() -> None:
    assert not (ROOT / "monoagens" / "library").exists()


def test_one_english_baseline_serves_every_language() -> None:
    english = lexicon.load("en")
    assert english.ok
    for code in ("id", "ja", "ar", "es", "zh", "fr", "de"):
        pack = lexicon.load(code)
        assert pack.ok, code
        assert pack.language == code
        assert pack.negations == english.negations
        assert pack.stopwords == english.stopwords
        assert pack.assistant_tells == english.assistant_tells
        assert pack.injection == english.injection
        assert pack.research_time == english.research_time
        assert pack.privacy_levels == english.privacy_levels
        assert pack.possessives == frozenset()
    assert lexicon.load("ja") is lexicon.load("ja")


def test_empty_language_code_degrades() -> None:
    pack = lexicon.load("")
    assert pack.ok is False
    assert pack.negations == frozenset()
    assert pack.stopwords == frozenset()


def test_guard_has_a_baseline_for_every_language() -> None:
    assert guard.has_pack("en")
    assert guard.has_pack("ja")
    assert guard.has_pack("") is False
    assert guard.is_sensitive("api_key=abc123", "ja")
    assert guard.is_crisis("kill myself", "ja") is True
    assert guard.is_crisis("dead battery", "ja") is False


def test_custom_lexicon_directory_is_still_supported(tmp_path) -> None:
    body = {
        "version": 1,
        "language": "en",
        "negations": ["never"],
        "stopwords": ["the"],
        "assistant_tells": ["as an ai"],
        "injection": ["ignore previous instructions"],
        "affirmations": ["yes"],
        "laughter": ["haha"],
        "particles": [],
        "serious_registers": ["formal"],
        "research_time": ["today"],
        "research_source": ["source"],
        "research_high_stakes": ["medical"],
        "research_challenge": ["prove it"],
        "privacy_levels": {"secret": 4},
    }
    (tmp_path / "en.json").write_text(json.dumps(body), encoding="utf-8")
    lexicon.configure(tmp_path)
    try:
        pack = lexicon.load("en")
        assert pack.ok is True
        assert pack.negations == frozenset({"never"})
        assert pack.privacy_levels == {"secret": 4}
        assert lexicon.load("ja").ok is True
    finally:
        lexicon.configure(None)


def test_invalid_custom_pack_falls_back_with_one_warning(tmp_path) -> None:
    (tmp_path / "en.json").write_text('{"version": 2}', encoding="utf-8")
    lexicon.configure(tmp_path)
    try:
        pack = lexicon.load("en")
        assert pack.ok is True
        assert len(lexicon.warnings()) == 1
        assert "invalid" in lexicon.warnings()[0]
    finally:
        lexicon.configure(None)


def _write_host(tmp_path: Path, code: str) -> Path:
    base = tmp_path / code
    base.mkdir()
    (base / "config.yaml").write_text(
        "database:\n  url_env: MONOAGENS_DATABASE_URL\n"
        "workspace: .monoagens/workspace\n"
        "roles_file: roles.yaml\nsoul_file: soul.yaml\n"
        "engine:\n  llm:\n    provider: custom\n    base_url: http://x\n"
        "    model: m\n    api_key_env: K\n"
        "  default_role: member\n  approval:\n    mode: yolo\n"
        "gateways: {}\n",
        encoding="utf-8",
    )
    (base / "roles.yaml").write_text(
        "roles:\n  member:\n    name: member\n    tools: [\"*\"]\n    commands: [\"*\"]\n",
        encoding="utf-8",
    )
    (base / "soul.yaml").write_text(
        f"name: MonoAgens\nlanguage: {code}\nlocale: {code}-{code.upper()}\n"
        "timezone: UTC\npersona: test\n",
        encoding="utf-8",
    )
    return base / "config.yaml"


def test_every_language_boots_without_a_safety_pack(tmp_path, monkeypatch) -> None:
    from support import test_dsn

    from monoagens.engine.config import load_config

    monkeypatch.setenv("MONOAGENS_DATABASE_URL", test_dsn())
    for code in ("ja", "ar", "es", "zh", "fr", "de", "id", "en"):
        config = load_config(
            _write_host(tmp_path, code), require_key=False, require_model=False
        )
        assert config.soul.language == code
        assert config.safety.allow_degraded is False


def test_language_config_applies_the_lexicon_dir_override(tmp_path, monkeypatch) -> None:
    packs = tmp_path / "packs"
    packs.mkdir()
    try:
        make_runtime(
            tmp_path,
            monkeypatch,
            engine_extra=f"  language:\n    lexicon_dir: {packs}\n",
        )
        assert lexicon.directory() == packs
        assert lexicon.load("en").ok is True
    finally:
        lexicon.configure(None)
