"""Self knowledge: generated-first topics, info, documents, and the guard (ADR-0015)."""


import asyncio
from dataclasses import replace

from support import build_config, role

from monoagens.engine import self_knowledge as sk
from monoagens.engine.commands import REGISTRY as COMMANDS
from monoagens.engine.config import SelfKnowledgeConfig
from monoagens.engine.self_knowledge import SelfKnowledge, check, slug
from monoagens.engine.tools import REGISTRY as TOOLS
from monoagens.engine.tools import ToolContext, execute
from monoagens.engine.tools.info import TOOL as INFO


def manifest(tmp_path) -> SelfKnowledge:
    return SelfKnowledge(build_config(tmp_path), TOOLS, COMMANDS)


def test_slug_is_github_compatible() -> None:
    assert slug("soul.yaml") == "soulyaml"
    assert slug("## **Bold** `code` [link](x)") == "bold-code-link"
    assert slug("🚀 Emoji") == "emoji"


def test_document_index_and_section_resolution(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sk, "ROOT", tmp_path)
    (tmp_path / "README.md").write_text(
        "# Title\n"
        "## Same\nfirst\n"
        "## Same\nsecond\n"
        "## soul.yaml\nsettings\n"
        "```\n## fenced\n```\n"
        "## Nested\ntop\n"
        "### Child\nchild body\n"
        "## Next\nafter\n",
        encoding="utf-8",
    )
    found = manifest(tmp_path)

    index = found.doc_index("README.md")

    assert "- same (2): Same" in index
    assert "- same-2 (2): Same" in index
    assert "soulyaml" in index
    assert "fenced" not in index
    assert "child body" in found.doc_section("README.md", "nested")
    assert "after" not in found.doc_section("README.md", "nested")
    assert "settings" in found.doc_section("README.md", "soulyaml")


def test_missing_document_and_allowlist(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sk, "ROOT", tmp_path)
    (tmp_path / "README.md").write_text("# T\n", encoding="utf-8")
    (tmp_path / "LOG.md").write_text("# Log\n", encoding="utf-8")

    found = manifest(tmp_path)

    assert "not available" in found.doc_index("LOG.md")
    assert "not available" in found.doc_index("MISSING.md")


def test_catalog_marks_allowed_and_locked(tmp_path) -> None:
    config = build_config(
        tmp_path, roles={"member": role("member", tools=("read", "info"), commands=("help",))}
    )
    found = SelfKnowledge(config, TOOLS, COMMANDS)

    catalog = found.catalog((role("member", tools=("read", "info"), commands=("help",)),))

    assert "tool read [allowed]" in catalog
    assert "tool grep [locked]" in catalog
    assert "command approve [locked]" in catalog


def test_disabled_self_knowledge_returns_a_signal(tmp_path) -> None:
    config = build_config(
        tmp_path,
        self_knowledge=SelfKnowledgeConfig(enabled=False),
    )
    found = SelfKnowledge(config, TOOLS, COMMANDS)

    assert found.index((role("member"),)) == ""
    assert "disabled" in found.info("catalog", "", (role("member"),))


def test_config_kind_shows_structure_not_values(tmp_path) -> None:
    config = build_config(tmp_path)
    found = SelfKnowledge(config, TOOLS, COMMANDS)

    schema = found.config_schema()

    assert "engine.llm.max_calls_per_turn" in schema
    assert config.llm.base_url not in schema


def test_topics_resolve_and_leak_no_infrastructure(tmp_path) -> None:
    config = build_config(tmp_path)
    found = SelfKnowledge(config, TOOLS, COMMANDS)

    for topic in found.topics():
        body = found.topic(topic)
        assert body.strip()
        assert str(config.paths.config) not in body
        assert config.llm.base_url not in body
        assert "behavior:" in body


def test_info_activates_a_tool_without_sending(tmp_path) -> None:
    config = build_config(tmp_path)
    ctx = ToolContext(config, (role("member", tools=("info",)),), ("*",))

    result = asyncio.run(
        execute({"info": INFO}, "info", {"kind": "tool", "name": "grep"}, ctx)
    )

    assert "grep" in result
    assert ctx.activated == {"grep"}
    assert ctx.sent == []


def test_check_is_clean_on_the_default_manifest(tmp_path) -> None:
    config = replace(
        build_config(tmp_path),
        self_knowledge=SelfKnowledgeConfig(topology=True),
    )

    assert check(config) == []
