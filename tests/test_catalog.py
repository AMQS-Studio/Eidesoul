"""Capability indexes: tools, gateways, voice parts, and the generated host files."""

import io

import yaml

from monoagens.admin import catalog, gateway, init, templates, terminal


def test_core_and_gateway_tools_are_indexed_without_a_hand_list(tmp_path):
    names = {item.name for item in catalog.tools()}
    assert {"read", "web_search", "thread_history", "send_message", "send_file"} <= names
    assert catalog.tool_names() == names


def test_gateways_are_discovered_from_packages_and_pairing_is_indexed():
    names = {item.name for item in catalog.gateways()}
    assert names == {"whatsapp"}
    from monoagens.gateways import gateway_index

    info = gateway_index()["whatsapp"]
    assert info.can_pair is True
    assert info.descriptor.extra == "whatsapp"
    assert info.descriptor.onboarding == "pair"
    assert info.descriptor.schema == "monoagens.gateways.whatsapp.schema"
    assert info.descriptor.capabilities == ("send", "send_to", "chat_history")


def test_core_tools_keep_their_real_description_and_parameters():
    from monoagens.engine.tools import REGISTRY

    items = {item.name: item for item in catalog.tools()}
    for name in ("thread_history", "send_message", "send_file"):
        assert items[name].detail
        assert not items[name].detail.startswith("gateway:")
        assert REGISTRY.by_name[name].parameters["type"] == "object"


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
        gateways=("whatsapp",),
        voice_parts=("affect", "style"),
    )
    config = yaml.safe_load(contents["config.yaml"])
    roles = yaml.safe_load(contents["roles.yaml"])
    soul = yaml.safe_load(contents["soul.yaml"])

    assert config["web_search"]["base_url"] == "http://127.0.0.1:8888"
    # Gateway keys come from the gateway's own settings dataclass plus the shared progress
    # flags every body declares, not from a literal block.
    from monoagens.gateways.base import BaseGateway
    from monoagens.gateways.whatsapp.config import WhatsAppSettings

    settings = WhatsAppSettings()
    expected = dict(BaseGateway.default_settings())
    expected.update(
        {
            "session_db": settings.session_db.as_posix(),
            "backlog_seconds": settings.backlog_seconds,
        }
    )
    assert config["gateways"]["whatsapp"] == expected
    assert roles["roles"]["member"]["tools"] == ["read", "web_search"]
    assert roles["roles"]["administrator"]["tools"] == ["*"]
    assert roles["roles"]["guest"]["gateways"] == {
        "whatsapp": {"dm": False, "groups": "none", "reply_to_bot": False},
    }
    assert roles["roles"]["administrator"]["gateways"]["whatsapp"] == {
        "dm": True,
        "groups": "all",
        "reply_to_bot": True,
    }
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
        "  telegram:\n"
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
    path.write_text("gateways:\n  telegram:\n    enabled: true\n", encoding="utf-8")
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


def test_gateway_add_seeds_missing_role_policies(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("gateways:\n  telegram:\n    enabled: true\n", encoding="utf-8")
    (tmp_path / "roles.yaml").write_text(
        "roles:\n"
        "  administrator:\n    name: administrator\n    tools: ['*']\n"
        "  member:\n    name: member\n"
        "  auditor:\n    name: auditor\n",
        encoding="utf-8",
    )

    assert gateway.add(str(path), "whatsapp") == 0

    roles = yaml.safe_load((tmp_path / "roles.yaml").read_text(encoding="utf-8"))["roles"]
    assert roles["administrator"]["gateways"]["whatsapp"] == {
        "dm": True,
        "groups": "all",
        "reply_to_bot": True,
    }
    assert roles["member"]["gateways"]["whatsapp"]["groups"] == "mention"
    assert roles["auditor"]["gateways"]["whatsapp"] == {
        "dm": False,
        "groups": "none",
        "reply_to_bot": False,
    }


def test_gateway_doctor_reports_and_fixes_missing_policies(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    path.write_text(
        "gateways:\n  whatsapp:\n    enabled: true\n", encoding="utf-8"
    )
    (tmp_path / "roles.yaml").write_text(
        "roles:\n  guest:\n    name: guest\n", encoding="utf-8"
    )

    assert gateway.doctor(str(path)) == 1
    assert "roles.guest.gateways.whatsapp" in capsys.readouterr().out

    assert gateway.doctor(str(path), fix=True) == 0
    roles = yaml.safe_load((tmp_path / "roles.yaml").read_text(encoding="utf-8"))["roles"]
    assert roles["guest"]["gateways"]["whatsapp"]["dm"] is False
    assert gateway.doctor(str(path)) == 0


def test_gateway_add_connect_calls_the_descriptor_onboarding(monkeypatch, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("gateways:\n  telegram:\n    enabled: true\n", encoding="utf-8")
    (tmp_path / "roles.yaml").write_text(
        "roles:\n  guest:\n    name: guest\n", encoding="utf-8"
    )
    calls: list[tuple] = []

    def onboarding(name, command, config):
        calls.append((name, command, config))
        return 0

    monkeypatch.setattr("monoagens.gateways.gateway_cli", onboarding)

    assert gateway.connect(str(path), "whatsapp") == 0
    assert calls == [("whatsapp", "pair", str(path))]
    assert "enabled: true" in path.read_text(encoding="utf-8")


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
