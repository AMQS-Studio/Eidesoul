"""Prompt/status contract: English scaffolding, language line, and the status card."""


import asyncio
from dataclasses import replace

import pytest
from support import inbound_message, make_agent, make_runtime

from eidesoul.engine.config import PromptConfig
from eidesoul.engine.prompts import (
    character_block,
    research_block,
    self_block,
    system_prompt,
)
from eidesoul.engine.roles import Role
from eidesoul.engine.runtime import RuntimeFacts
from eidesoul.engine.soul import Soul, language_line, normalize_language


def test_language_line_variants() -> None:
    assert "Indonesian" in language_line(Soul(language="id"))
    assert language_line(Soul(language="en")) == "Reply in English."
    with pytest.raises(ValueError):
        language_line(Soul(language="jv"))


def test_normalize_language_aliases() -> None:
    assert normalize_language("EN") == "en"
    assert normalize_language("english") == "en"
    assert normalize_language("in") == "id"
    assert normalize_language("auto") == "id"
    assert normalize_language("") == "id"
    with pytest.raises(ValueError):
        normalize_language("jv")


def test_system_prompt_is_english_with_self_and_roles(tmp_path) -> None:
    env = make_agent(tmp_path)
    prompt = system_prompt(
        env.config,
        (Role("member", description="Team member", context="Help the team."),),
        runtime=RuntimeFacts(mode="primary", db_label="postgresql://db/eidesoul"),
        gateway_hints="## Interface\n- test",
    )

    assert "## Self" in prompt
    assert "## Roles" in prompt
    assert "Help the team." in prompt
    assert "## Interface" in prompt
    assert "Reply in Indonesian" in prompt
    assert "You live in chat" in prompt
    assert "WhatsApp" not in prompt


def test_self_block_reports_runtime_facts(tmp_path) -> None:
    env = make_agent(tmp_path)
    runtime = RuntimeFacts(
        mode="primary",
        db_label="postgresql://db:5432/eidesoul",
        memory_status="mem0 (eidesoul_memories)",
        pending_approvals=2,
        gateways=("whatsapp", "cli"),
    )
    block = self_block(runtime, (Role("administrator", protected=True),), env.config)
    assert "Mode: primary" in block
    assert "Surface: whatsapp, cli" in block
    assert "Pending approvals: 2" in block
    assert "postgresql://db:5432/eidesoul" in block


def test_status_and_whoami(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    agent = env.agent
    roles = (
        Role(
            "administrator",
            protected=True,
            tools=("*",),
            commands=("*",),
            memory_admin=True,
        ),
    )
    member = (env.config.roles.default_role(),)

    status = asyncio.run(
        agent._command(
            "status", "", "cli:local", "local", roles, env.gateway, inbound_message("/status")
        )
    )
    assert "Mode: standby" in status
    assert "Lease:" in status
    assert "Uptime:" in status
    assert "Database:" in status
    assert "Memory:" in status
    assert "Approvals: 0 pending" in status
    assert "Soul:" in status and "language id" in status
    assert "• Language: id" in status
    assert "Privacy: enabled=True" in status

    who = asyncio.run(
        agent._command(
            "whoami", "", "cli:local", "local", member, env.gateway, inbound_message("/whoami")
        )
    )
    assert "Peran: Member" in who
    assert "Surface: fake" in who


def test_chat_kind_label_follows_the_inbound_flag(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    agent = env.agent
    member = (env.config.roles.default_role(),)
    admin = (
        Role(
            "administrator",
            protected=True,
            tools=("*",),
            commands=("*",),
            memory_admin=True,
        ),
    )

    def run(name: str, inbound, roles=member) -> str:
        return asyncio.run(
            agent._command(name, "", "chat", "local", roles, env.gateway, inbound)
        )

    assert 'Chat: group "' in run("whoami", inbound_message("/whoami", dm=False))
    assert 'Chat: dm "' in run("whoami", inbound_message("/whoami", dm=True))
    assert 'Chat: dm "' in run("whoami", inbound_message("/whoami"))
    assert "Chat: group" in run("status", inbound_message("/status", dm=False), admin)
    assert "Chat: dm" in run("status", inbound_message("/status", dm=True), admin)


def test_status_gives_a_non_administrator_only_rough_buckets(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    roles = (env.config.roles.default_role(),)

    text = asyncio.run(
        env.agent._command(
            "status", "", "cli:local", "local", roles, env.gateway, inbound_message("/status")
        )
    )

    assert "• Mode:" in text and "• Uptime:" in text
    assert "Database:" not in text and "Cost:" not in text and "Turn latency" not in text


def test_a_context_without_an_inbound_defaults_to_dm(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    roles = (env.config.roles.default_role(),)

    ctx = env.agent.command_context("chat", "local", roles, env.gateway)

    assert ctx.direct is True


def test_commands_requesting_llm_do_not_decide(tmp_path) -> None:
    env = make_agent(tmp_path)
    asyncio.run(
        env.agent.handle(
            inbound_message("/status", thread="628111@s.whatsapp.net"), env.gateway
        )
    )
    assert env.llm.decisions == 0
    assert env.llm.calls == 0


def test_system_prompt_states_the_cross_room_policy(tmp_path) -> None:
    env = make_agent(tmp_path)
    prompt = system_prompt(
        env.config, (Role("member"),), actions='- [2026-01-01 10:00] teks -> chat: "halo"'
    )

    assert "one soul across rooms" in prompt
    assert "role=manual_owner" in prompt
    assert "never reveal or hint at the requester" in prompt
    assert "## Recent actions" in prompt
    assert "The archive covers only this chat" not in prompt


def test_character_block_reports_levels_target_and_patterns() -> None:
    block = character_block((3, 2), "male", 0.9, jabs=2, patterns=["Balas sekali", " "])

    assert "Toxicity level 3/4 for this turn (target male, confidence 0.90)." in block
    assert "Adult memes 2/3:" in block
    assert "At most 2 jab per turn" in block
    assert "stay gentle: no jab, no curse" in block
    assert "Hard floor no level unlocks" in block
    assert block.count("\n- ") == 1
    assert "## Room patterns (apply the move, never copy the wording):" in block
    assert "Balas sekali" in block


def test_character_block_gentle_floor_and_disabled_gate() -> None:
    gentle = character_block((1, 0), "", 0.0)
    assert "target unknown" in gentle
    assert "Toxicity level 1/4" in gentle
    assert "## Room patterns" not in gentle
    ungated = character_block((3, 3), "female", 0.9, gate=False)
    assert "Target restriction disabled for this host." in ungated
    assert "Never target a woman" not in ungated


def test_research_block_forbids_interrogation_and_uses_the_soul_locale() -> None:
    block = research_block("id-ID")

    assert "Ask at most one question per turn" in block
    assert "Never ask about stickers" in block
    assert "Assume id-ID context" in block
    assert "never invent fresh numbers or quotes" in block
    assert "closest match" not in research_block("en-US")


def test_system_prompt_carries_the_character_and_research_blocks(tmp_path) -> None:
    env = make_agent(tmp_path)
    prompt = system_prompt(
        replace(env.agent.config, prompt=PromptConfig(max_chars=20000)),
        (Role(name="member"),),
        runtime=RuntimeFacts(),
        character=character_block((1, 0), "unknown", 0.0),
        research=research_block("id-ID"),
        currents="## Current context (fetched 2026-11-01)\n- rupiah: melemah",
        query="ada berita apa",
    )

    assert "Toxicity level 1/4" in prompt
    assert "Research before asking" in prompt
    assert "## Current context (fetched 2026-11-01)" in prompt
    assert "In a clearly joking room you may return one short jab" not in prompt
    assert "but never start them yourself" not in prompt
