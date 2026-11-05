"""Capability indexes: tools, gateways, voice parts, and the generated host files."""

import io

import yaml

from monoagens.admin import catalog, gateway, init, templates, terminal


def test_core_and_gateway_tools_are_indexed_without_a_hand_list(tmp_path):
    names = {item.name for item in catalog.tools()}
    assert {"read", "web_search", "chat_history"} <= names
    assert catalog.tool_names() == names


def test_gateways_are_discovered_from_packages_and_pairing_is_indexed():
    names = {item.name for item in catalog.gateways()}
    assert {"cli", "whatsapp"} <= names
    from monoagens.gateways import gateway_index

    assert gateway_index()["whatsapp"].can_pair is True


def test_voice_parts_come_from_the_voice_config():
    assert {"affect", "style", "character", "humanize"} == {
        item.name for item in catalog.voice_parts()
    }


def test_init_applies_capability_choices():
    contents = init.build(
        provider="openai",
        language="en",
        locale="en-US",
        timezone="Asia/Jakarta",
        websearch="searxng",
        websearch_url="http://127.0.0.1:8888",
        tools=("read", "web_search"),
        gateways=("cli", "whatsapp"),
        voice_parts=("affect", "style"),
    )
    config = yaml.safe_load(contents["config.yaml"])
    roles = yaml.safe_load(contents["roles.yaml"])
    soul = yaml.safe_load(contents["soul.yaml"])

    assert config["web_search"]["base_url"] == "http://127.0.0.1:8888"
    assert config["gateways"] == {
        "cli": {
            "enabled": True,
            "user": "local",
            "thread_id": "cli:local",
            "chat_name": "Terminal",
        },
        "whatsapp": {"enabled": True},
    }
    assert roles["roles"]["member"]["tools"] == ["read", "web_search"]
    assert roles["roles"]["administrator"]["tools"] == ["*"]
    assert soul["voice"]["affect"]["enabled"] is True
    assert soul["voice"]["style"]["enabled"] is True
    assert soul["voice"]["character"]["enabled"] is False
    assert soul["voice"]["humanize"]["enabled"] is False


def test_disabling_websearch_disables_research():
    contents = init.build(
        provider="openai",
        language="en",
        locale="en-US",
        timezone="UTC",
        websearch="off",
    )
    config = yaml.safe_load(contents["config.yaml"])
    assert config["web_search"]["base_url"] == ""
    assert config["engine"]["research"]["mode"] == "off"


def test_gateway_edit_preserves_comments_and_toggles(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "gateways:\n"
        "  # keep this comment\n"
        "  cli:\n"
        "    enabled: true\n"
        "engine:\n"
        "  llm:\n"
        "    provider: openai\n",
        encoding="utf-8",
    )
    gateway.set_enabled(str(path), "whatsapp", True)
    text = path.read_text(encoding="utf-8")
    assert "# keep this comment" in text
    assert "whatsapp:\n    enabled: true" in text
    gateway.set_enabled(str(path), "whatsapp", False)
    assert "whatsapp:\n    enabled: false" in path.read_text(encoding="utf-8")


def test_gateway_add_installs_a_declared_extra(monkeypatch, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("gateways:\n  cli:\n    enabled: true\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\n[project.optional-dependencies]\nwhatsapp=['neonize']\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    class Completed:
        returncode = 0

    monkeypatch.setattr(gateway.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(
        gateway.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or Completed(),
    )

    assert gateway.add(str(path), "whatsapp") == 0
    assert calls
    assert calls[0][-2:] == ["--extra", "whatsapp"]
    assert "whatsapp:\n    enabled: true" in path.read_text(encoding="utf-8")


def test_template_voice_parts_are_explicit():
    files = templates.files(
        provider="openai",
        language="en",
        locale="en-US",
        timezone="UTC",
        voice_enabled=True,
        voice_parts=(),
    )
    soul = yaml.safe_load(files["soul.yaml"])
    assert all(
        soul["voice"][part]["enabled"] is False
        for part in ("affect", "style", "character", "humanize")
    )


def test_multi_choose_keeps_defaults_or_reads_numbers():
    choices = [
        terminal.Choice("read", "read files", "read"),
        terminal.Choice("write", "write files", "write"),
        terminal.Choice("web", "search", "web"),
    ]
    stdout = io.StringIO()
    kept = terminal.multi_choose(
        stdin=io.StringIO("\n"),
        stdout=stdout,
        title="tools",
        choices=choices,
        selected=("read", "web"),
    )
    assert kept == ["read", "web"]

    stdout = io.StringIO()
    picked = terminal.multi_choose(
        stdin=io.StringIO("1 3\n"),
        stdout=stdout,
        title="tools",
        choices=choices,
        selected=("read",),
    )
    assert picked == ["read", "web"]
