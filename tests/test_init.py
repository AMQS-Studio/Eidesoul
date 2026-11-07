"""Interactive init contract: location, provider, credential, model, database, identity, voice.

ADR-0005 §4.2/§4.3: the wizard shows the Eidos lockup, navigates menus with the line fallback in
tests, reviews, then writes atomically. Piped stdin fails closed; a secret stays in `.env` and
never reaches the review, the screen, or the log.
"""


import io
import os
import sys

import pytest
import yaml

from monoagens.__main__ import main
from monoagens.admin import brand, templates, terminal
from monoagens.admin import init as admin_init


def modes(folder) -> dict[str, int]:
    return {
        name: (folder / name).stat().st_mode & 0o777
        for name in admin_init.HOST_FILES
    }


class Pipe:
    """A stdin that is not a terminal, like a pipe or CI."""

    def isatty(self) -> bool:
        return False

    def readline(self) -> str:
        raise AssertionError("the wizard must not read off a terminal")


class Tty(io.StringIO):
    """A stdin that answers as a terminal would."""

    def isatty(self) -> bool:
        return True


class InterruptingTty(Tty):
    """A terminal where Ctrl-C arrives at the first prompt."""

    def readline(self) -> str:
        raise KeyboardInterrupt


def scripted(
    *,
    location: str = "",
    provider: str = "groq",
    credential: str = "",
    model: str = "llama-operator",
    database_url: str = "",
    label: str = "Muqsith",
    websearch: str = "ddgs",
    search_key: str = "",
    search_url: str = "",
    search_engine: str = "",
    approval: str = "default",
    natural: str = "yes",
    voice: str = "1 2",
    gateways: str = "",
    tools: str = "",
    review: str = "1",
) -> str:
    """One answer per prompt in rail order, for the ten-step wizard."""
    lines = [location, provider, credential, model, database_url, label, websearch]
    if websearch == "searxng":
        lines.append(search_url or "http://127.0.0.1:8888")
    elif websearch in ("tavily", "brave", "exa", "serper", "google"):
        lines.append(search_key)
        if websearch == "google":
            lines.append(search_engine)
    lines.extend([approval, natural, voice, gateways, tools, review])
    return "\n".join(lines) + "\n"


def test_non_interactive_writes_the_four_files_with_modes(tmp_path):
    written, skipped = admin_init.init(tmp_path, provider="openai", model="operator-model")

    assert sorted(path.name for path in written) == sorted(admin_init.HOST_FILES)
    assert skipped == []
    assert modes(tmp_path) == {
        "config.yaml": 0o644,
        "roles.yaml": 0o600,
        "soul.yaml": 0o644,
        ".env": 0o600,
    }
    body = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert body["engine"]["llm"]["provider"] == "openai"
    assert body["engine"]["llm"]["model"] == "operator-model"
    assert (tmp_path / ".monoagens" / "workspace").is_dir()


def test_a_rejected_key_is_reported_at_the_credential_step(monkeypatch):
    from monoagens.admin import wizard as admin_wizard

    seen: list[str] = []

    def fake_model_ids(provider, api_key=""):
        seen.append(api_key)
        if api_key == "bad-key":
            raise admin_init.ConfigError("HTTP Error 401: Unauthorized")
        return ["model-a"]

    monkeypatch.setattr(admin_init, "model_ids", fake_model_ids)
    state = admin_init.Answers(provider="openai")
    stdout = io.StringIO()

    admin_wizard._ask_credential(state, Tty("bad-key\n3\n"), stdout)

    assert seen == ["bad-key"]
    assert state.api_key == ""
    assert "rejected this key" in stdout.getvalue()
    assert "what now?" in stdout.getvalue()


def test_a_rejected_key_can_be_kept_for_later(monkeypatch):
    from monoagens.admin import wizard as admin_wizard

    def fake_model_ids(provider, api_key=""):
        raise admin_init.ConfigError("HTTP Error 401: Unauthorized")

    monkeypatch.setattr(admin_init, "model_ids", fake_model_ids)
    state = admin_init.Answers(provider="openai")

    admin_wizard._ask_credential(state, Tty("bad-key\n2\n"), io.StringIO())

    assert state.api_key == "bad-key"


def test_model_discovery_uses_the_key_entered_in_the_wizard(monkeypatch):
    from monoagens.admin import models as admin_models

    seen: dict[str, str] = {}

    def fake_list(config, *, timeout):
        seen["key"] = config.api_key()
        return ["model-a"]

    monkeypatch.setattr(admin_models, "list_models", fake_list)

    assert admin_init.model_ids("openai", "wizard-key") == ["model-a"]
    assert seen["key"] == "wizard-key"


def test_piped_init_fails_closed_and_names_the_flags(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", Pipe())

    code = main(["--env", "absent.env", "init", "--dir", str(tmp_path)])

    err = capsys.readouterr().err
    assert code == 2
    assert "--provider" in err
    assert "--non-interactive" in err or "terminal" in err
    assert not (tmp_path / "config.yaml").exists()


def test_an_unknown_provider_fails_before_any_file_is_written(tmp_path, capsys):
    code = main(
        [
            "--env",
            "absent.env",
            "init",
            "--dir",
            str(tmp_path),
            "--provider",
            "mystery",
            "--non-interactive",
        ]
    )

    assert code == 2
    assert "unknown provider 'mystery'" in capsys.readouterr().err
    assert not (tmp_path / "config.yaml").exists()


def test_a_non_interactive_cli_run_writes_into_dir_with_every_flag(tmp_path, capsys):
    code = main(
        [
            "--env",
            "absent.env",
            "init",
            "--dir",
            str(tmp_path),
            "--provider",
            "openai",
            "--model",
            "operator-model",
            "--database-url",
            "postgresql+psycopg://u:p@h/db",
            "--label",
            "Muqsith",
            "--voice-parts",
            "affect",
            "--non-interactive",
        ]
    )

    assert code == 0
    assert capsys.readouterr().out.count("wrote") == 4
    assert modes(tmp_path) == {
        "config.yaml": 0o644,
        "roles.yaml": 0o600,
        "soul.yaml": 0o644,
        ".env": 0o600,
    }
    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert "affect" not in config["engine"]
    assert "character" not in config["engine"]
    assert config["engine"]["approval"]["mode"] == "default"
    assert config["engine"]["approval"]["natural_confirm"] is True
    soul = yaml.safe_load((tmp_path / "soul.yaml").read_text(encoding="utf-8"))
    assert "enabled" not in soul["voice"]
    assert soul["voice"]["affect"]["enabled"] is True
    assert soul["voice"]["style"]["enabled"] is False
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "MONOAGENS_DATABASE_URL=postgresql+psycopg://u:p@h/db" in env


def test_the_wizard_shows_the_rail_and_the_review_then_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_init, "model_ids", lambda provider, api_key="": [])
    # The rail lists every discovered body, so the answer names the position of WhatsApp.
    from monoagens.admin import catalog

    rail = [item.name for item in catalog.gateways()]
    answer = scripted(
        database_url="postgresql+psycopg://monoagens:MARKER-PASSWORD@127.0.0.1:5433/monoagens",
        websearch="tavily",
        search_key="tvly-SEARCH",
        approval="strict",
        natural="no",
        gateways=str(rail.index("whatsapp") + 1),
    )
    stdin = Tty(answer)
    stdout = io.StringIO()

    written, skipped = admin_init.wizard(tmp_path, stdin=stdin, stdout=stdout)

    screen = stdout.getvalue()
    assert "[1/10] location" in screen
    assert "[7/10] web search" in screen
    assert "[8/10] approval" in screen
    assert "[9/10] voice" in screen
    assert "MONOAGENS" in screen
    assert "Review" in screen
    assert "Receipt" in screen
    assert "MARKER-PASSWORD" not in screen
    assert "tvly-SEARCH" not in screen
    assert "MONOAGENS_DATABASE_URL" in screen
    assert "voice layer" not in screen
    assert skipped == []
    assert len(written) == 4
    assert modes(tmp_path)[".env"] == 0o600
    assert modes(tmp_path)["roles.yaml"] == 0o600

    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert "affect" not in config["engine"]
    assert config["web_search"]["provider"] == "tavily"
    assert config["web_search"]["api_key_env"] == "TAVILY_API_KEY"
    assert config["engine"]["approval"]["mode"] == "strict"
    assert config["engine"]["approval"]["natural_confirm"] is False
    assert config["gateways"]["whatsapp"]["enabled"] is True
    soul = yaml.safe_load((tmp_path / "soul.yaml").read_text(encoding="utf-8"))
    assert "enabled" not in soul["voice"]
    assert soul["voice"]["affect"]["enabled"] is True
    assert soul["voice"]["style"]["enabled"] is True
    assert soul["voice"]["character"]["enabled"] is False
    roles = yaml.safe_load((tmp_path / "roles.yaml").read_text(encoding="utf-8"))
    assert roles["roles"]["administrator"]["label"] == "Muqsith"
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "MONOAGENS_DATABASE_URL=postgresql+psycopg://" in env
    assert "TAVILY_API_KEY=tvly-SEARCH" in env
    assert "MARKER-PASSWORD" in env  # the value lives in .env, at 0600


def test_no_location_uses_the_installer_owned_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(admin_init, "model_ids", lambda provider, api_key="": [])
    answer = scripted().split("\n", 1)[1]
    monkeypatch.setattr(sys, "stdin", Tty(answer))
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    code = main(
        ["--env", "absent.env", "init", "--dir", str(tmp_path), "--no-location"]
    )

    assert code == 0
    screen = output.getvalue()
    assert "where should this soul live?" not in screen
    assert "[2/10] provider" in screen
    assert (tmp_path / "config.yaml").is_file()


def test_a_piped_wizard_run_through_the_command_writes_the_host(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_init, "model_ids", lambda provider, api_key="": [])
    monkeypatch.setattr(admin_init, "DEFAULT_LOG", tmp_path / "install.log")
    answer = scripted()
    monkeypatch.setattr(sys, "stdin", Tty(answer))
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    code = main(["--env", "absent.env", "init", "--dir", str(tmp_path)])

    assert code == 0
    assert modes(tmp_path) == {
        "config.yaml": 0o644,
        "roles.yaml": 0o600,
        "soul.yaml": 0o644,
        ".env": 0o600,
    }


def test_cancel_writes_nothing(tmp_path):
    stdin = Tty(scripted(review="3"))

    with pytest.raises(admin_init.InitCancelled):
        admin_init.wizard(tmp_path, stdin=stdin, stdout=io.StringIO())

    assert list(tmp_path.iterdir()) == []


def test_ctrl_c_at_a_step_writes_nothing(tmp_path):
    with pytest.raises(admin_init.InitCancelled):
        admin_init.wizard(tmp_path, stdin=InterruptingTty(), stdout=io.StringIO())

    assert list(tmp_path.iterdir()) == []


def test_write_then_rename_never_leaves_a_partial_file(tmp_path, monkeypatch):
    target = tmp_path / "config.yaml"

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        admin_init._atomic_write(target, "database: {}\n", 0o644)

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_an_interrupted_write_rolls_the_new_files_back(tmp_path, monkeypatch):
    contents = admin_init.build(provider="openai")
    real = admin_init._atomic_write
    calls = {"count": 0}

    def flaky(path, text, mode):
        calls["count"] += 1
        if calls["count"] == 2:
            raise KeyboardInterrupt
        real(path, text, mode)

    monkeypatch.setattr(admin_init, "_atomic_write", flaky)

    with pytest.raises(KeyboardInterrupt):
        admin_init.write_files(tmp_path, contents)

    assert list(tmp_path.iterdir()) == []


def test_a_second_run_keeps_every_file(tmp_path):
    admin_init.init(tmp_path, provider="openai")
    before = (tmp_path / "config.yaml").read_text(encoding="utf-8")

    written, skipped = admin_init.init(tmp_path, provider="groq")

    assert written == []
    assert len(skipped) == 4
    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == before


def test_force_replaces_an_existing_file(tmp_path):
    admin_init.init(tmp_path, provider="openai")

    admin_init.init(tmp_path, provider="groq", force=True)

    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config["engine"]["llm"]["provider"] == "groq"


def test_replacing_env_keeps_generated_database_credentials(tmp_path):
    admin_init.init(tmp_path, provider="openai")
    (tmp_path / ".env").write_text(
        "POSTGRES_PASSWORD=generated\nMONOAGENS_DB_CONTAINER=1\n", encoding="utf-8"
    )
    contents = admin_init.build(
        provider="openai",
        database_url="postgresql+psycopg://monoagens:generated@127.0.0.1:5432/monoagens",
    )
    admin_init.write_files(
        tmp_path,
        contents,
        {name: "replace" for name in admin_init.HOST_FILES},
    )
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=generated" in env
    assert "MONOAGENS_DB_CONTAINER=1" in env


def test_existing_files_offer_keep_change_replace(tmp_path):
    admin_init.init(tmp_path, provider="openai")
    before_config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    before_config["custom"] = {"keep": "me"}
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(before_config, sort_keys=False), encoding="utf-8"
    )
    before_roles = (tmp_path / "roles.yaml").read_text(encoding="utf-8")
    # location + credential blank, web search ddgs, approval default/yes, capabilities
    # blank, then config=change and keep the other three, then apply.
    answers = ["", "", "ddgs", "default", "yes", "", "", "2", "1", "1", "1", "1"]
    stdin = Tty("\n".join(answers) + "\n")
    stdout = io.StringIO()

    admin_init.wizard(
        tmp_path,
        stdin=stdin,
        stdout=stdout,
        provider="groq",
        model="",
        database_url="",
        label="",
        voice_parts=("affect",),
    )

    screen = stdout.getvalue()
    assert "keep it, change the values, or replace it?" in screen
    assert "replace" in screen
    after = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert after["custom"] == {"keep": "me"}
    assert after["engine"]["llm"]["provider"] == "groq"
    assert (tmp_path / "roles.yaml").read_text(encoding="utf-8") == before_roles


def test_voice_parts_come_from_the_voice_config():
    from monoagens.admin import catalog

    names = [item.name for item in catalog.voice_parts()]
    assert names == list(admin_init.part_names())
    assert {"affect", "style", "character", "humanize"} <= set(names)


def test_voice_selection_uses_the_shipped_parts_and_accepts_an_explicit_list():
    from monoagens.admin import catalog

    assert admin_init.voice_selection(None, None) == catalog.default_voice_parts()
    assert admin_init.voice_selection("on") == catalog.default_voice_parts()
    assert admin_init.voice_selection("off") == ()
    assert admin_init.voice_selection("off", ("affect",)) == ("affect",)
    assert admin_init.voice_selection(None, ("style", "affect")) == ("style", "affect")


def test_the_retired_voice_layer_flag_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="retired"):
        admin_init.voice_selection("character")
    with pytest.raises(ValueError, match="retired"):
        admin_init.init(tmp_path, provider="openai", voice_layer="character")
    assert list(tmp_path.iterdir()) == []


def test_google_search_writes_the_engine_id_env_names():
    contents = admin_init.build(
        provider="openai",
        websearch="google",
        websearch_key="g-key",
        websearch_engine_id="cx-1",
        language="en",
        locale="en-US",
        timezone="UTC",
    )
    config = yaml.safe_load(contents["config.yaml"])

    assert config["web_search"]["provider"] == "google"
    assert config["web_search"]["api_key_env"] == "GOOGLE_API_KEY"
    assert config["web_search"]["engine_id_env"] == "GOOGLE_CSE_ID"
    assert "GOOGLE_API_KEY=g-key" in contents[".env"]
    assert "GOOGLE_CSE_ID=cx-1" in contents[".env"]


def test_a_custom_search_key_env_name_is_written_to_config_and_env():
    contents = admin_init.build(
        provider="openai",
        websearch="brave",
        websearch_key="b-key",
        websearch_key_env="MY_BRAVE_TOKEN",
        language="en",
        locale="en-US",
        timezone="UTC",
    )
    config = yaml.safe_load(contents["config.yaml"])

    assert config["web_search"]["provider"] == "brave"
    assert config["web_search"]["api_key_env"] == "MY_BRAVE_TOKEN"
    assert "MY_BRAVE_TOKEN=b-key" in contents[".env"]


def test_unknown_search_and_approval_values_are_rejected():
    with pytest.raises(ValueError, match="unknown web search provider"):
        admin_init.build(provider="openai", websearch="bing")
    with pytest.raises(ValueError, match="unknown approval mode"):
        admin_init.build(provider="openai", approval_mode="loose")
    with pytest.raises(ValueError, match="takes no API key"):
        admin_init.build(provider="openai", websearch="ddgs", websearch_key="nope")
    with pytest.raises(ValueError, match="needs a URL"):
        admin_init.build(provider="openai", websearch="searxng")


def test_the_wizard_asks_for_the_searxng_url(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_init, "model_ids", lambda provider, api_key="": [])
    answer = scripted(websearch="searxng", search_url="http://127.0.0.1:8889")

    admin_init.wizard(tmp_path, stdin=Tty(answer), stdout=io.StringIO())

    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config["web_search"]["provider"] == "searxng"
    assert config["web_search"]["base_url"] == "http://127.0.0.1:8889"


def test_the_database_url_is_validated_at_the_step():
    assert admin_init.validate_database_url("") is None
    assert admin_init.validate_database_url("postgresql+psycopg://u:p@h/db") is None
    assert "postgresql+psycopg" in admin_init.validate_database_url("sqlite:///tmp/x.db")
    assert "database name" in admin_init.validate_database_url("postgresql+psycopg://u:p@h")


def test_the_review_comes_from_the_templates_plus_answers():
    contents = admin_init.build(
        provider="openai",
        database_url="postgresql+psycopg://u:secret@h/db",
        voice_parts=("affect",),
        approval_mode="yolo",
        natural_confirm=False,
    )

    assert set(contents) == set(
        templates.files(provider="openai", language="en", locale="en-US", timezone="UTC")
    )
    config = yaml.safe_load(contents["config.yaml"])
    assert "character" not in config["engine"]
    assert "affect" not in config["engine"]
    assert config["engine"]["approval"]["mode"] == "yolo"
    assert config["engine"]["approval"]["natural_confirm"] is False
    soul = yaml.safe_load(contents["soul.yaml"])
    assert "enabled" not in soul["voice"]
    assert soul["voice"]["affect"]["enabled"] is True
    assert soul["voice"]["style"]["enabled"] is False
    assert "postgresql+psycopg://u:secret@h/db" not in contents["config.yaml"]
    assert "MONOAGENS_DATABASE_URL=postgresql+psycopg://u:secret@h/db" in contents[".env"]


def test_the_header_uses_the_existing_wordmark_and_not_a_placeholder() -> None:
    screen = "\n".join(admin_init.banner())

    assert "MONOAGENS" in screen
    assert "persistent mono agent" in screen
    assert "[logo]" not in screen


def test_the_location_step_writes_to_the_chosen_directory(tmp_path):
    target = tmp_path / "nested" / "soul"
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(admin_init, "model_ids", lambda provider, api_key="": [])
    answer = scripted(location=str(target))
    try:
        written, _ = admin_init.wizard(
            tmp_path, stdin=Tty(answer), stdout=io.StringIO()
        )
    finally:
        monkeypatch.undo()

    assert len(written) == 4
    assert (target / "config.yaml").is_file()
    assert (target / ".monoagens" / "workspace").is_dir()
    assert not (tmp_path / "config.yaml").exists()


def test_the_credential_is_written_to_env_but_never_echoed(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(admin_init, "model_ids", lambda provider, api_key="": [])
    answer = scripted(credential="sk-MARKER")
    stdout = io.StringIO()
    try:
        admin_init.wizard(tmp_path, stdin=Tty(answer), stdout=stdout)
    finally:
        monkeypatch.undo()

    assert "sk-MARKER" not in stdout.getvalue()
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "GROQ_API_KEY=sk-MARKER" in env


def test_line_menu_accepts_a_name_and_blank_keeps_the_default():
    choices = [
        terminal.Choice("OpenCode Go", "opencode", "opencode"),
        terminal.Choice("OpenAI", "openai", "openai"),
    ]
    stdout = io.StringIO()
    assert terminal.choose(
        stdin=Tty("openai\n"), stdout=stdout, title="provider", choices=choices
    ) == 1
    assert terminal.choose(
        stdin=Tty("\n"), stdout=io.StringIO(), title="provider", choices=choices
    ) == 0


def test_menu_filter_is_case_insensitive():
    choices = [
        terminal.Choice("OpenCode Go", "opencode", "opencode"),
        terminal.Choice("OpenAI", "openai", "openai"),
    ]

    assert terminal._filtered(choices, "CODE") == [0]
    assert terminal._filtered(choices, "open") == [0, 1]


def test_api_key_file_is_written_without_echo(tmp_path):
    key_file = tmp_path / "key.txt"
    key_file.write_text("sk-FILE-MARKER\n", encoding="utf-8")
    target = tmp_path / "soul"

    code = main(
        [
            "--env",
            "absent.env",
            "init",
            "--dir",
            str(target),
            "--provider",
            "groq",
            "--model",
            "operator-model",
            "--api-key-file",
            str(key_file),
            "--non-interactive",
        ]
    )

    assert code == 0
    env = (target / ".env").read_text(encoding="utf-8")
    assert "GROQ_API_KEY=sk-FILE-MARKER" in env
    assert (target / ".env").stat().st_mode & 0o777 == 0o600


def test_api_key_file_is_the_headless_equivalent_of_the_masked_step(tmp_path):
    written, _ = admin_init.init(
        tmp_path, provider="groq", model="operator-model", api_key="sk-DIRECT"
    )

    assert len(written) == 4
    assert "GROQ_API_KEY=sk-DIRECT" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_a_location_that_is_a_file_is_rejected_and_reprompted(tmp_path):
    file_path = tmp_path / "not-a-directory"
    file_path.write_text("x", encoding="utf-8")
    target = tmp_path / "soul"
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(admin_init, "model_ids", lambda provider, api_key="": [])
    answer = scripted(location=f"{file_path}\n{target}")
    stdout = io.StringIO()
    try:
        written, _ = admin_init.wizard(
            tmp_path, stdin=Tty(answer), stdout=stdout
        )
    finally:
        monkeypatch.undo()

    assert "not a directory" in stdout.getvalue()
    assert len(written) == 4
    assert (target / "config.yaml").is_file()


def test_line_menu_q_cancels_without_a_choice():
    choices = [terminal.Choice("OpenCode Go", "opencode", "opencode")]

    with pytest.raises(terminal.Cancelled):
        terminal.choose(
            stdin=Tty("q\n"), stdout=io.StringIO(), title="provider", choices=choices
        )


def test_line_fallback_never_emits_ansi(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(admin_init, "model_ids", lambda provider, api_key="": [])
    answer = scripted()
    stdout = io.StringIO()
    try:
        admin_init.wizard(tmp_path, stdin=Tty(answer), stdout=stdout, color=True)
    finally:
        monkeypatch.undo()

    assert "\x1b" not in stdout.getvalue()


def test_curses_header_never_contains_ansi():
    from monoagens.admin.wizard import _menu_header

    assert "\x1b" not in "\n".join(_menu_header(0, True))


def test_logo_art_keeps_the_asset_shape_and_separates_ansi_from_plain():
    rows = brand.logo_cells()
    assert len(rows) == brand.LOGO_HEIGHT
    assert all(len(row) == brand.LOGO_WIDTH for row in rows)
    assert any(cell.fg and cell.bg for row in rows for cell in row)
    assert all("\x1b" not in cell.char for row in rows for cell in row)
    assert "\x1b" in "\n".join(brand.logo_lines(True))
    assert "\x1b" not in "\n".join(brand.logo_lines(False))


def test_logo_layout_skips_a_crowded_screen():
    assert terminal._logo_column(80, 24) == 23
    assert terminal._logo_column(60, 24) == 0
    assert terminal._logo_column(80, 18) == 0


def test_curses_logo_draws_cells_without_ansi(monkeypatch):
    import curses

    calls: list[tuple[int, int, str, int]] = []

    class Screen:
        def addnstr(self, y, x, text, limit, attr):
            calls.append((y, x, text, attr))

    monkeypatch.setattr(curses, "start_color", lambda: None)
    monkeypatch.setattr(curses, "use_default_colors", lambda: None)
    monkeypatch.setattr(curses, "init_pair", lambda pair, fg, bg: None)
    monkeypatch.setattr(curses, "color_pair", lambda pair: pair)
    monkeypatch.setattr(curses, "has_colors", lambda: True)
    monkeypatch.setattr(curses, "COLORS", 256, raising=False)
    monkeypatch.setattr(curses, "COLOR_PAIRS", 65536, raising=False)
    terminal._init_colors(True)
    terminal._draw_logo(Screen(), 1, 2, True)

    assert calls
    assert all("\x1b" not in text for _, _, text, _ in calls)


def _leaf_paths(body: dict, prefix: str = "") -> list[tuple[str, object]]:
    found: list[tuple[str, object]] = []
    for key, value in body.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            found.extend(_leaf_paths(value, path))
        else:
            found.append((path, value))
    return found


def test_generated_config_comments_every_key() -> None:
    text = templates.files(
        provider="openai", language="en", locale="en-US", timezone="UTC"
    )["config.yaml"]
    body = yaml.safe_load(text)

    for path, value in _leaf_paths(body):
        assert templates.comment_for(path, value), path
    assert "# Temperature for decisions" in text
    assert "# off, auto, or always" in text
    assert "# Connection pool size" in text


def test_a_shipped_scaffold_without_env_is_replaced_without_asking(tmp_path):
    from monoagens.admin import init, templates

    (tmp_path / "config.yaml").write_text(
        templates.dump_config(
            {
                **templates.CONFIG,
                "engine": {
                    **templates.CONFIG["engine"],
                    "llm": {**templates.CONFIG["engine"]["llm"], "provider": ""},
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "roles.yaml").write_text(templates.dump_roles(templates.ROLES), encoding="utf-8")
    (tmp_path / "soul.yaml").write_text(templates.soul_yaml("en", "en", "UTC"), encoding="utf-8")

    assert init.unconfigured(tmp_path) is True
    _written, skipped = init.init(
        tmp_path,
        provider="openai",
        language="en",
        locale="en-US",
        timezone="UTC",
    )
    assert not skipped
    assert (tmp_path / ".env").exists()
    body = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert body["engine"]["llm"]["provider"] == "openai"
