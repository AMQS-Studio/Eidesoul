"""Generated host files: dataclass-derived defaults, comments, and round-trip loading."""


import errno
from types import SimpleNamespace

import pytest
import yaml

from monoagens.admin import templates, yaml_io
from monoagens.engine.access.roles import load_roles
from monoagens.engine.config import (
    ENGINE_SCHEMA,
    ApprovalConfig,
    MemoryConfig,
    PromptConfig,
    SkillsConfig,
    load_config,
)
from monoagens.engine.soul import load_soul

PROVIDERS = ("openai", "groq")
DSN = "postgresql+psycopg://monoagens:monoagens@127.0.0.1:5434/omp_j2"


def build(tmp_path, *, provider: str = "openai", gateways=(), **overrides):
    files = templates.files(
        provider=provider,
        model="operator-model",
        language="en",
        locale="en-US",
        timezone="UTC",
        gateways=gateways,
        **overrides,
    )
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return files


def test_engine_defaults_match_the_dataclasses() -> None:
    assert templates.ENGINE["approval"]["mode"] == ApprovalConfig().mode
    assert templates.ENGINE["memory"]["max_items"] == MemoryConfig().max_items
    assert templates.ENGINE["memory"]["capture"]["novelty_threshold"] == 0.88
    assert templates.ENGINE["prompt"]["salience"] == PromptConfig().salience
    assert templates.ENGINE["prompt"]["self_block"]["redact_infra"] == "non_administrator"
    assert templates.ENGINE["skills"]["max_chars"] == SkillsConfig().max_chars
    assert templates.ENGINE["skills"]["efficiency"]["min_evidence_episodes"] == 2


def test_engine_surface_is_exactly_the_loader_schema() -> None:
    def compare(tree: dict, schema: dict, path: str) -> None:
        assert set(tree) == set(schema), path
        for key, nested in schema.items():
            if isinstance(nested, dict):
                compare(tree[key], nested, f"{path}.{key}")

    compare(templates.ENGINE, ENGINE_SCHEMA, "engine")


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("gateways", [(), ("whatsapp",)])
def test_generated_files_load(tmp_path, monkeypatch, provider, gateways) -> None:
    monkeypatch.setenv("MONOAGENS_DATABASE_URL", DSN)
    files = build(tmp_path, provider=provider, gateways=gateways)

    config = load_config(tmp_path / "config.yaml", require_key=False)
    soul = load_soul(tmp_path / "soul.yaml")
    roles = load_roles(
        yaml.safe_load(files["roles.yaml"])["roles"], config.default_role
    )

    assert config.llm.provider == provider
    assert config.llm.model == "operator-model"
    assert config.default_role == "guest"
    assert soul.language == "en"
    assert set(roles.names()) == {"administrator", "guest", "member"}
    guest = roles.by_name["guest"]
    administrator = roles.by_name["administrator"]
    if gateways:
        assert config.gateways["whatsapp"].enabled is True
        assert guest.gateways["whatsapp"]["dm"] is False
        assert administrator.gateways["whatsapp"]["groups"] == "all"
    else:
        assert config.gateways == {}
        assert guest.gateways == {}


@pytest.mark.parametrize(
    ("websearch", "api_key_env", "engine_id_env", "base_url"),
    [
        ("ddgs", "", "", ""),
        ("searxng", "", "", "https://search.example/json"),
        ("tavily", "TAVILY_API_KEY", "", ""),
        ("brave", "BRAVE_API_KEY", "", ""),
        ("exa", "EXA_API_KEY", "", ""),
        ("serper", "SERPER_API_KEY", "", ""),
        ("google", "GOOGLE_API_KEY", "GOOGLE_CSE_ID", ""),
        ("off", "", "", ""),
    ],
)
def test_web_search_provider_is_emitted(
    tmp_path, monkeypatch, websearch, api_key_env, engine_id_env, base_url
) -> None:
    monkeypatch.setenv("MONOAGENS_DATABASE_URL", DSN)
    files = build(
        tmp_path, websearch=websearch, websearch_url="https://search.example/json"
    )
    body = yaml.safe_load(files["config.yaml"])
    search = body["web_search"]
    assert search["provider"] == websearch
    assert search["api_key_env"] == api_key_env
    assert search["engine_id_env"] == engine_id_env
    assert search["base_url"] == base_url
    if websearch == "off":
        assert body["engine"]["research"]["mode"] == "off"

    config = load_config(tmp_path / "config.yaml", require_key=False)
    assert config.web_search.provider == websearch
    assert config.web_search.api_key_env == api_key_env


def test_an_unknown_search_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="web search provider"):
        templates.files(
            provider="openai", language="en", locale="en-US", timezone="UTC",
            websearch="bing",
        )


def test_gateway_blocks_come_from_the_gateway_settings_dataclass() -> None:
    from monoagens.gateways.whatsapp.config import WhatsAppSettings

    settings = WhatsAppSettings()
    block = templates.gateway_block("whatsapp")
    assert block["enabled"] is True
    assert block["backlog_seconds"] == settings.backlog_seconds
    assert block["session_db"] == settings.session_db.as_posix()
    assert templates.gateway_defaults()["whatsapp"]["enabled"] is False


def test_gateway_hooks_supply_settings_and_policies(monkeypatch) -> None:
    class FakeGateway:
        name = "fake"

        def default_settings(self):
            return {"enabled": True, "channel": "ops"}

        def default_role_policy(self, role):
            return {"dm": role == "administrator"}

    info = SimpleNamespace(module="", attribute="FakeGateway")
    monkeypatch.setattr(templates, "gateway_index_snapshot", lambda: {"fake": info})
    monkeypatch.setattr(templates, "_gateway_class", lambda _info: FakeGateway)

    assert templates.gateway_block("fake") == {"enabled": True, "channel": "ops"}
    roles: dict[str, dict] = {
        "administrator": {"name": "administrator"},
        "guest": {"name": "guest"},
    }
    templates.seed_gateway_policies(roles, ("fake",))
    assert roles["administrator"]["gateways"]["fake"] == {"dm": True}
    assert roles["guest"]["gateways"]["fake"] == {"dm": False}


def test_rendered_files_carry_banners_and_comments() -> None:
    files = templates.files(
        provider="openai", model="m", language="en", locale="en-US", timezone="UTC"
    )
    config_text = files["config.yaml"]
    body = yaml.safe_load(config_text)

    assert "# " + "=" * 76 in config_text
    for section in body["engine"]:
        assert f"# --- engine.{section}:" in config_text, section
    assert "# --- 1. DATABASE" in config_text
    assert "# GATEWAYS" in config_text
    assert "# Temperature for decisions" in config_text
    assert "# Connection pool size" in config_text

    roles_text = files["roles.yaml"]
    assert "# ====" in roles_text
    assert "# Role block 'administrator'" in roles_text
    assert "# Tool name patterns" in roles_text
    assert "# --- ROLE: guest ---" in roles_text

    soul_text = files["soul.yaml"]
    assert "# ====" in soul_text
    assert "# Primary language code" in soul_text
    assert "# --- VOICE:" in soul_text


def test_every_rendered_key_has_a_comment() -> None:
    files = templates.files(
        provider="openai", model="m", language="en", locale="en-US", timezone="UTC",
        gateways=("whatsapp",),
    )
    for name in ("config.yaml", "roles.yaml", "soul.yaml"):
        lines = files[name].splitlines()
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.endswith(":"):
                previous = lines[index - 1].strip() if index else ""
                assert previous.startswith("#"), f"{name}: {stripped} has no comment"


CONFIG_WITH_COMMENTS = """\
# top banner stays
database:
  # database note
  url_env: MONOAGENS_DATABASE_URL
  url: ''   # inline note
engine:
  # provider note
  llm:
    # model note
    model: old-model
    # provider note
    provider: custom
  default_role: guest
"""


def write_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG_WITH_COMMENTS, encoding="utf-8")
    return path


def test_an_update_keeps_comments_and_ordering(tmp_path) -> None:
    path = write_config(tmp_path)
    yaml_io.update_file(path, {"engine.llm.model": "new-model"})
    text = path.read_text(encoding="utf-8")

    assert "# top banner stays" in text
    assert "# database note" in text
    assert "# model note" in text
    assert "# inline note" in text
    assert "# provider note" in text
    assert text.count("\n") == CONFIG_WITH_COMMENTS.count("\n")
    assert text.index("database:") < text.index("engine:")
    loaded = yaml.safe_load(text)
    assert loaded["engine"]["llm"]["model"] == "new-model"
    assert loaded["engine"]["llm"]["provider"] == "custom"


def test_an_update_keeps_the_comment_above_the_next_key(tmp_path) -> None:
    build(tmp_path)
    path = tmp_path / "roles.yaml"
    yaml_io.update_file(path, {"roles.administrator.label": "Muqsith"})
    text = path.read_text(encoding="utf-8")

    assert "# Locked role" in text
    assert "# Tool name patterns" in text
    assert yaml.safe_load(text)["roles"]["administrator"]["label"] == "Muqsith"


def test_a_soul_update_keeps_the_banner_and_comments(tmp_path) -> None:
    build(tmp_path)
    path = tmp_path / "soul.yaml"
    yaml_io.update_file(
        path, {"voice.style.enabled": False, "rules": ["Reply in the configured language."]}
    )
    text = path.read_text(encoding="utf-8")

    assert "# " + "=" * 76 in text
    assert "# Principled rules" in text
    soul = load_soul(path)
    assert soul.voice.style.enabled is False
    assert soul.rules[0] == "Reply in the configured language."


def test_an_update_inserts_a_missing_key_and_still_loads(tmp_path) -> None:
    path = write_config(tmp_path)
    yaml_io.update_file(
        path,
        {"engine.llm.reasoning_effort": "high", "gateways": {"whatsapp": {"enabled": True}}},
    )
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert loaded["engine"]["llm"]["reasoning_effort"] == "high"
    assert loaded["gateways"]["whatsapp"]["enabled"] is True


def test_an_ebusy_rename_falls_back_to_the_same_inode(tmp_path, monkeypatch) -> None:
    path = write_config(tmp_path)
    inode = path.stat().st_ino

    def busy(_source, _target):
        raise OSError(errno.EBUSY, "Device or resource busy")

    monkeypatch.setattr(yaml_io.os, "replace", busy)
    yaml_io.update_file(path, {"engine.llm.model": "busy-model"})

    text = path.read_text(encoding="utf-8")
    assert path.stat().st_ino == inode
    assert "# model note" in text
    assert "# top banner stays" in text
    assert yaml.safe_load(text)["engine"]["llm"]["model"] == "busy-model"


def test_an_invalid_edit_never_reaches_the_file(tmp_path) -> None:
    path = write_config(tmp_path)
    before = path.read_text(encoding="utf-8")

    def refuse(_text):
        raise ValueError("not accepted")

    with pytest.raises(yaml_io.YamlUpdateError):
        yaml_io.update_file(path, {"engine.llm.model": "nope"}, validate=refuse)
    assert path.read_text(encoding="utf-8") == before


def test_update_config_proves_the_result_with_the_real_loader(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MONOAGENS_DATABASE_URL", DSN)
    build(tmp_path)
    path = tmp_path / "config.yaml"

    changed = yaml_io.update_config(path, {"engine.llm.model": "second-model"})

    assert "# ===" in changed
    assert load_config(path, require_key=False).llm.model == "second-model"


def test_role_gateway_options_say_what_they_accept() -> None:
    comments = templates.ROLE_GATEWAY_COMMENTS
    assert "true | false" in templates._role_comment("roles.member.gateways.whatsapp.dm", True)
    for option in ("groups", "channels", "threads"):
        text = templates._role_comment(f"roles.member.gateways.discord.{option}", "all")
        assert "'all'" in text
        assert "'mention'" in text
        assert "'none'" in text
    assert "reply_to_bot" in comments
    assert "true | false" in comments["reply_to_bot"]
    rendered = templates.dump_roles(
        {
            "member": {
                "name": "member",
                "gateways": {
                    "whatsapp": {"dm": True, "groups": "all", "reply_to_bot": True},
                    "slack": {"dm": True, "channels": "mention", "threads": "none"},
                },
            }
        }
    )
    assert "# Allow 1-on-1 direct messages" in rendered
    assert "# Channel trigger for this role" in rendered
    assert "owns its meaning and default" not in rendered


def test_gateway_option_comments_name_choices_and_defaults() -> None:
    from monoagens.gateways import gateway_index

    pages = gateway_index()
    for name, info in pages.items():
        for option, value in info.default_config().items():
            comment = templates.comment_for(f"gateways.{name}.{option}", value)
            assert "operator setting" not in comment
            assert "operator section" not in comment
            assert "that gateway's own settings dataclass" not in comment
            assert "own meaning" not in comment or option not in templates.GATEWAY_OPTION_COMMENTS
    assert "'polling' | 'webhook'" in templates.comment_for("gateways.telegram.mode", "polling")
    assert "'socket' | 'events'" in templates.comment_for("gateways.slack.mode", "socket")
    assert "Default: polling." in templates.comment_for("gateways.telegram.mode", "polling")
    for option in ("show_thinking", "show_steps", "interim_updates", "sync_commands"):
        assert "true | false" in templates.GATEWAY_OPTION_COMMENTS[option]


def test_soul_comments_match_the_validators_and_explain_the_layers() -> None:
    from monoagens.engine.soul import PROFANITY, SAFETY
    from monoagens.engine.stores.affect import VOLATILITY

    volatility = templates.SOUL_COMMENTS["voice.affect.volatility"]
    for value in VOLATILITY:
        assert f"'{value}'" in volatility
    profanity = templates.SOUL_COMMENTS["voice.profanity"]
    for value in PROFANITY:
        assert f"'{value}'" in profanity
    safety = templates.SOUL_COMMENTS["voice.safety"]
    for value in SAFETY:
        assert f"'{value}'" in safety

    assert "PAD" in templates.SOUL_COMMENTS["voice.affect"]
    assert "writing style" in templates.SOUL_COMMENTS["voice.style"]
    assert "examples" in templates.SOUL_COMMENTS["voice.character"]
    assert "bubbles" in templates.SOUL_COMMENTS["voice.humanize"]
    assert "modes" in templates.SOUL_COMMENTS["mode"]


def test_no_rendered_config_key_falls_back_to_a_generic_comment() -> None:
    from monoagens.gateways import gateway_index

    body = {
        **templates.CONFIG,
        "gateways": {name: info.default_config() for name, info in gateway_index().items()},
    }
    rendered = templates.dump_config(body)
    for marker in ("· section.", "· bool setting", "· str setting", "· int setting"):
        assert marker not in rendered, marker
    for line in rendered.splitlines():
        if "— operator" in line:
            raise AssertionError(line)
