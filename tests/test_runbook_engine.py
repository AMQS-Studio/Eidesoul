"""Regression tests for the remediation runbook findings (III.3-VI.6)."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from monoagens.engine.access.roles import Role
from monoagens.engine.character.character import _numeric_signature
from monoagens.engine.commands.approve import decision_refusal
from monoagens.engine.config import DEFAULT_SENSITIVE_COMMANDS, WebSearchConfig
from monoagens.engine.config.load import _llm_config
from monoagens.engine.guard import patterns as guard_patterns
from monoagens.engine.guard import screen
from monoagens.engine.guard.policy import scan_command
from monoagens.engine.jobs.automation_manifest import _task_moment
from monoagens.engine.llm.client import _round_text
from monoagens.engine.tools import send_file as send_file_module
from monoagens.engine.tools import web_search
from monoagens.engine.turn import assembly
from monoagens.engine.turn.research import EXACT
from monoagens.providers import resolve

SENSITIVE = DEFAULT_SENSITIVE_COMMANDS


def test_administrator_approves_across_rooms() -> None:
    approval = SimpleNamespace(
        thread_id="room-a", origin_thread="room-b", sender="alice", kind="tool", tool="shell"
    )
    boss = Role(name="boss", protected=True)
    member = Role(name="member")
    assert decision_refusal(approval, thread_id="console", sender="boss", roles=(boss,)) == ""
    assert (
        decision_refusal(approval, thread_id="console", sender="boss", roles=(member,))
        == "foreign"
    )


def test_read_only_commands_do_not_false_positive() -> None:
    for command in (
        "ls docker-compose.yml",
        "diff shutdown.log",
        "git diff docker-compose.yml",
        "git log --oneline shutdown.log",
    ):
        decision = screen(command, SENSITIVE)
        assert decision is None or decision.verdict != "deny", command
    assert screen("shutdown -h now", SENSITIVE).verdict == "deny"


def test_eval_and_pipe_interpreter_are_scanned() -> None:
    assert scan_command("eval 'git push --force'", SENSITIVE) is True
    assert scan_command("echo 'git push --force' | sh", SENSITIVE) is True
    assert scan_command("echo hello | grep hi", SENSITIVE) is False


def test_send_file_iterates_a_list_of_handles(monkeypatch, tmp_path) -> None:
    seen: list[str] = []

    def fake_source(ctx, raw):
        seen.append(raw)
        return tmp_path / f"{raw}.txt", ""

    async def fake_local(ctx, path, caption, reason):
        return f"ok: {path.name}"

    monkeypatch.setattr(send_file_module, "_source", fake_source)
    monkeypatch.setattr(send_file_module, "body_of", lambda ctx, gateway: ctx.gateway)
    monkeypatch.setattr(
        send_file_module, "_target", lambda ctx, body, chat: (SimpleNamespace(thread="chat"), "")
    )
    monkeypatch.setattr(send_file_module, "_reason_refusal", lambda address, reason: "")
    monkeypatch.setattr(send_file_module, "_local", fake_local)
    ctx = SimpleNamespace(agent=object(), gateway=object(), thread_id="chat")

    result = asyncio.run(
        send_file_module._send_file({"path": ["att_1", "att_2"], "gateway": "g"}, ctx)
    )

    assert result == "ok: att_1.txt\nok: att_2.txt"
    assert seen == ["att_1", "att_2"]


def test_fallback_provider_uses_its_own_api_key_env(monkeypatch) -> None:
    config = WebSearchConfig(
        provider="tavily", fallbacks=("brave",), api_key_env="TAVILY_KEY"
    )
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    assert web_search._provider_config("brave", config) is config
    monkeypatch.setenv("BRAVE_API_KEY", "brave-secret")
    assert web_search._provider_config("brave", config).api_key_env == "BRAVE_API_KEY"


def test_clock_times_are_not_exact_claims_but_scores_are() -> None:
    assert EXACT.search("we meet at 10:00") is None
    assert EXACT.search("2-1") is not None
    assert EXACT.search("3.5% growth") is not None
    assert EXACT.search("2026-11-06") is not None


def test_at_time_rolls_to_tomorrow_and_parses_iso() -> None:
    now = datetime(2026, 1, 1, 10, 0, tzinfo=ZoneInfo("UTC")).timestamp()
    assert _task_moment("09:00", "UTC", now).date().isoformat() == "2026-01-02"
    assert _task_moment("11:00", "UTC", now).date().isoformat() == "2026-01-01"
    utc = _task_moment("2026-01-01T12:00:00Z", "UTC", now)
    assert utc.hour == 12
    assert utc.utcoffset().total_seconds() == 0
    assert _task_moment("2026-01-01T12:00:00+07:00", "UTC", now).hour == 5


def test_sampling_supported_override_is_explicit_only() -> None:
    _name, defaults = resolve("openai", "", "")
    base = {"provider": "openai", "model": "some-model"}
    default = _llm_config(dict(base), require_key=False, require_model=True)
    assert default.sampling_supported == defaults.sampling_supported
    assert (
        _llm_config(
            {**base, "sampling_supported": []}, require_key=False, require_model=True
        ).sampling_supported
        == ()
    )
    assert (
        _llm_config(
            {**base, "sampling_supported": ["temperature"]},
            require_key=False,
            require_model=True,
        ).sampling_supported
        == ("temperature",)
    )


def test_rendered_measures_history_against_the_current_message(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_message_lines(config, thread_id, entry, numbers, current, *, persist=True):
        seen["current"] = current
        return ["x"]

    monkeypatch.setattr(assembly, "_message_lines", fake_message_lines)
    current, entry = SimpleNamespace(), SimpleNamespace()
    assert assembly._rendered(None, "t", entry, current) == 1
    assert seen["current"] is current


def test_interim_text_is_not_reused_as_the_final_reply() -> None:
    assert _round_text("Checking...", "Checking...", interim=True) == ""
    assert _round_text("", "Checking...", interim=False) == "Checking..."
    assert _round_text("final", "old", interim=False) == "final"


def test_numeric_signature_ignores_internal_spacing() -> None:
    assert _numeric_signature("costs $100") == _numeric_signature("costs $ 100")
    assert _numeric_signature("10%") == _numeric_signature("10 %")


def test_credential_terms_load_from_library_data() -> None:
    terms = guard_patterns._credential_terms()
    assert r"kata[\s_-]+sandi" in terms["strong_keys"]
    assert "adalah" in terms["connectors"]
