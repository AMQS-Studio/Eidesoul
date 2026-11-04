"""Interactive init contract: location, provider, credential, model, database, identity, voice.

ADR-0039 §4.2/§4.3: the wizard shows the Eidos lockup, navigates menus with the line fallback in
tests, reviews, then writes atomically. Piped stdin fails closed; a secret stays in `.env` and
never reaches the review, the screen, or the log.
"""


import io
import os
import sys

import pytest
import yaml

from eidesoul.__main__ import main
from eidesoul.admin import init as admin_init
from eidesoul.admin import templates, terminal


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
    assert (tmp_path / ".eidesoul" / "workspace").is_dir()


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
            "--voice-layer",
            "off",
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
    assert config["engine"]["affect"]["enabled"] is False
    assert config["engine"]["humanize"]["enabled"] is False
    assert config["engine"]["character"]["enabled"] is False
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "EIDESOUL_DATABASE_URL=postgresql+psycopg://u:p@h/db" in env


def test_the_wizard_shows_the_rail_and_the_review_then_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_init, "model_ids", lambda provider: [])
    answer = "\n".join(
        [
            "",  # 1/7 location: keep the passed directory
            "groq",  # 2/7 provider
            "",  # 3/7 credential: fill .env later
            "llama-operator",  # 4/7 model
            "postgresql+psycopg://eidesoul:MARKER-PASSWORD@127.0.0.1:5433/eidesoul",
            "Muqsith",  # 6/7 administrator label
            "off",  # 7/7 voice layer
            "1",  # review: apply
        ]
    )
    stdin = Tty(answer + "\n")
    stdout = io.StringIO()

    written, skipped = admin_init.wizard(tmp_path, stdin=stdin, stdout=stdout)

    screen = stdout.getvalue()
    assert "[1/7] location" in screen
    assert "[7/7] voice layer" in screen
    assert "EIDESOUL" in screen
    assert "Review" in screen
    assert "Receipt" in screen
    assert "MARKER-PASSWORD" not in screen
    assert "EIDESOUL_DATABASE_URL" in screen
    assert skipped == []
    assert len(written) == 4
    assert modes(tmp_path)[".env"] == 0o600
    assert modes(tmp_path)["roles.yaml"] == 0o600

    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config["engine"]["llm"]["provider"] == "groq"
    assert config["engine"]["llm"]["model"] == "llama-operator"
    assert config["engine"]["affect"]["enabled"] is False
    assert config["engine"]["humanize"]["enabled"] is False
    assert config["engine"]["character"]["enabled"] is False
    roles = yaml.safe_load((tmp_path / "roles.yaml").read_text(encoding="utf-8"))
    assert roles["roles"]["administrator"]["label"] == "Muqsith"
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "EIDESOUL_DATABASE_URL=postgresql+psycopg://" in env
    assert "MARKER-PASSWORD" in env  # the value lives in .env, at 0600


def test_a_piped_wizard_run_through_the_command_writes_the_host(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_init, "model_ids", lambda provider: [])
    monkeypatch.setattr(admin_init, "DEFAULT_LOG", tmp_path / "install.log")
    answer = "\n".join(["", "groq", "", "", "", "", "on", "1"]) + "\n"
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
    stdin = Tty("\n".join(["", "groq", "", "", "", "", "on", "3"]) + "\n")

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


def test_existing_files_offer_keep_change_replace(tmp_path):
    admin_init.init(tmp_path, provider="openai")
    before_config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    before_config["custom"] = {"keep": "me"}
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(before_config, sort_keys=False), encoding="utf-8"
    )
    before_roles = (tmp_path / "roles.yaml").read_text(encoding="utf-8")
    stdin = Tty("\n\n2\n\n\n\n\n")  # location/credential blank; config change; keep the rest
    stdout = io.StringIO()

    admin_init.wizard(
        tmp_path,
        stdin=stdin,
        stdout=stdout,
        provider="groq",
        model="",
        database_url="",
        label="",
        voice_layer="on",
    )

    screen = stdout.getvalue()
    assert "keep it, change the values, or replace it?" in screen
    assert "replace" in screen
    after = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert after["custom"] == {"keep": "me"}
    assert after["engine"]["llm"]["provider"] == "groq"
    assert (tmp_path / "roles.yaml").read_text(encoding="utf-8") == before_roles


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("on", ()),
        ("off", admin_init.VOICE_PARTS),
        ("character", ("character",)),
        ("character,humanize", ("humanize", "character")),
        ("emotion,humanize,character", admin_init.VOICE_PARTS),
    ],
)
def test_the_voice_layer_switch_parses(answer, expected):
    assert admin_init.voice_off(answer) == expected


def test_an_unknown_voice_part_is_rejected():
    with pytest.raises(ValueError, match="unknown voice layer part"):
        admin_init.voice_off("character,singing")


def test_the_database_url_is_validated_at_the_step():
    assert admin_init.validate_database_url("") is None
    assert admin_init.validate_database_url("postgresql+psycopg://u:p@h/db") is None
    assert "postgresql+psycopg" in admin_init.validate_database_url("sqlite:///tmp/x.db")
    assert "database name" in admin_init.validate_database_url("postgresql+psycopg://u:p@h")


def test_the_review_comes_from_the_templates_plus_answers():
    contents = admin_init.build(
        provider="openai", database_url="postgresql+psycopg://u:secret@h/db", voice=("character",)
    )

    assert set(contents) == set(
        templates.files(provider="openai", language="en", locale="en-US", timezone="UTC")
    )
    config = yaml.safe_load(contents["config.yaml"])
    assert config["engine"]["character"]["enabled"] is False
    assert config["engine"]["affect"]["enabled"] is True
    assert "postgresql+psycopg://u:secret@h/db" not in contents["config.yaml"]
    assert "EIDESOUL_DATABASE_URL=postgresql+psycopg://u:secret@h/db" in contents[".env"]


def test_the_lockup_replaces_the_logo_placeholder() -> None:
    screen = "\n".join(admin_init.banner())

    assert "◈" in screen
    assert "EIDESOUL" in screen
    assert "a soul engine you run on your own machine" in screen
    assert "[logo]" not in screen


def test_the_location_step_writes_to_the_chosen_directory(tmp_path):
    target = tmp_path / "nested" / "soul"
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(admin_init, "model_ids", lambda provider: [])
    answer = "\n".join(
        [str(target), "groq", "", "", "", "", "on", "1"]
    )
    try:
        written, _ = admin_init.wizard(
            tmp_path, stdin=Tty(answer + "\n"), stdout=io.StringIO()
        )
    finally:
        monkeypatch.undo()

    assert len(written) == 4
    assert (target / "config.yaml").is_file()
    assert (target / ".eidesoul" / "workspace").is_dir()
    assert not (tmp_path / "config.yaml").exists()


def test_the_credential_is_written_to_env_but_never_echoed(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(admin_init, "model_ids", lambda provider: [])
    answer = "\n".join(["", "groq", "sk-MARKER", "", "", "", "on", "1"])
    stdout = io.StringIO()
    try:
        admin_init.wizard(tmp_path, stdin=Tty(answer + "\n"), stdout=stdout)
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
    monkeypatch.setattr(admin_init, "model_ids", lambda provider: [])
    answer = "\n".join(
        [str(file_path), str(target), "groq", "", "", "", "", "on", "1"]
    )
    stdout = io.StringIO()
    try:
        written, _ = admin_init.wizard(
            tmp_path, stdin=Tty(answer + "\n"), stdout=stdout
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
    monkeypatch.setattr(admin_init, "model_ids", lambda provider: [])
    answer = "\n".join(["", "groq", "", "", "", "", "on", "1"])
    stdout = io.StringIO()
    try:
        admin_init.wizard(tmp_path, stdin=Tty(answer + "\n"), stdout=stdout, color=True)
    finally:
        monkeypatch.undo()

    assert "\x1b" not in stdout.getvalue()


def test_curses_header_never_contains_ansi():
    from eidesoul.admin.wizard import _menu_header

    assert "\x1b" not in "\n".join(_menu_header(0, True))
