"""Prompt contract (ADR-0033 §4.5, §6.11): properties hold per body, never per wording."""

from pathlib import Path

import pytest
import yaml
from support import build_config

from monoagens.engine.access.roles import Role
from monoagens.engine.inbound import PROMPT_SECTION_MAX, CoreDefaults, prompt_section
from monoagens.engine.llm import prompt_prefix_hash
from monoagens.engine.soul import Soul
from monoagens.engine.turn.prompts import Prompt, PromptContext, system_prompt

ROOT = Path(__file__).resolve().parents[1]
CASES = yaml.safe_load((ROOT / "tests" / "data" / "prompt_cases.yaml").read_text())[
    "cases"
]


def _soul(case: dict) -> Soul:
    return Soul(
        language=case["language"],
        locale=case["locale"],
        timezone=case["timezone"],
        name="MonoAgens",
        persona="p",
    )


def _section(case: dict) -> str:
    source = case.get("section_from")
    if source == "console":
        from monoagens.admin.console import Console

        return Console().prompt_section()
    return str(case.get("section") or "")


def _prompt(tmp_path, case: dict) -> Prompt:
    config = build_config(tmp_path, soul=_soul(case))
    context = PromptContext(
        config=config,
        roles=(Role("member"),),
        body_section=prompt_section(_Body(_section(case))),
        language=case["language"],
        query="hello",
    )
    return system_prompt(config, (Role("member"),), context=context)


class _Body(CoreDefaults):
    def __init__(self, text: str) -> None:
        self._text = text

    def prompt_section(self) -> str:
        return self._text


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_prompt_case(tmp_path, case) -> None:
    prompt = _prompt(tmp_path, case)
    system = prompt.stable + "\n" + prompt.volatile
    for fragment in case.get("expect_contains") or ():
        assert fragment in system, (case["id"], fragment)
    for fragment in case.get("expect_absent") or ():
        assert fragment not in system, (case["id"], fragment)
    recorded = case.get("stable_prefix_hash")
    if recorded:
        stable = prompt.stable.replace(str(tmp_path), "<root>")
        assert prompt_prefix_hash(stable, []) == recorded, case["id"]


def test_the_stable_prefix_survives_a_new_day(tmp_path) -> None:
    case = CASES[0]
    first = _prompt(tmp_path, case)
    second = _prompt(tmp_path, case)
    assert first.stable == second.stable
    assert prompt_prefix_hash(first.stable, []) == prompt_prefix_hash(second.stable, [])
    assert "Date:" in first.volatile
    assert "Date:" not in first.stable


def test_a_body_without_a_section_adds_nothing() -> None:
    assert CoreDefaults().prompt_section() == ""


def test_the_section_cap_is_enforced() -> None:
    class Huge(CoreDefaults):
        def prompt_section(self) -> str:
            return "x" * (PROMPT_SECTION_MAX + 1)

    with pytest.raises(ValueError, match="cap"):
        prompt_section(Huge())


def test_the_console_section_is_static_and_under_the_cap() -> None:
    from monoagens.admin.console import Console

    first = Console().prompt_section()
    second = Console().prompt_section()
    assert first == second
    assert 0 < len(first) <= PROMPT_SECTION_MAX


def test_the_whatsapp_section_is_static_and_under_the_cap() -> None:
    from types import SimpleNamespace

    from monoagens.gateways.whatsapp.gateway import WhatsAppGateway

    body = SimpleNamespace(self_handles=lambda: {"monoagens"})
    first = WhatsAppGateway.prompt_section(body)
    second = WhatsAppGateway.prompt_section(body)
    assert first == second
    assert 0 < len(first) <= PROMPT_SECTION_MAX


def test_the_voice_never_classifies_a_person() -> None:
    from monoagens.engine.soul import VoiceConfig
    from monoagens.engine.turn.prompts import voice_block

    block = voice_block(VoiceConfig(profanity="avoid"))
    for word in ("male", "female", "confidence", "gender_gate", "toxicity"):
        assert word not in block.lower()
    assert "avoid swearing" in block


def test_a_language_without_a_catalog_is_refused() -> None:
    with pytest.raises(ValueError, match="available"):
        Soul(language="fr", locale="fr-FR", timezone="UTC")


def test_stable_parts_are_pure_across_per_turn_contexts(tmp_path) -> None:
    case = CASES[0]
    config = build_config(tmp_path, soul=_soul(case))
    roles = (Role("member"),)
    first_context = PromptContext(
        config=config,
        roles=roles,
        sender="Budi",
        direct=True,
        mode="daily",
        tone="neutral",
        query="first",
        memory="memory one",
        emotion="emotion one",
    )
    second_context = PromptContext(
        config=config,
        roles=roles,
        sender="Sari",
        direct=False,
        mode="professional",
        tone="angry",
        query="second",
        memory="memory two",
        emotion="emotion two",
    )

    first = system_prompt(config, roles, context=first_context)
    second = system_prompt(config, roles, context=second_context)

    assert first.stable == second.stable
    assert prompt_prefix_hash(first.stable, []) == prompt_prefix_hash(second.stable, [])


def test_safety_and_a_research_card_survive_an_aggressive_budget(tmp_path) -> None:
    """ADR-0003 §4.2: crisis instructions and a research card are never dropped for budget."""
    from dataclasses import replace

    from monoagens.engine.turn.prompts import CRISIS_SAFETY
    from monoagens.engine.turn.research import build_card, render

    card = build_card(
        "harga emas hari ini",
        [
            {
                "title": "Harga emas hari ini",
                "url": "https://example.com/gold",
                "body": "Harga emas naik tipis pada perdagangan pagi ini.",
            }
        ],
        timezone="UTC",
        reason="numeric",
    )
    research = render(card)
    base = build_config(tmp_path)
    config = replace(base, prompt=replace(base.prompt, max_chars=50))
    context = PromptContext(
        config=config,
        roles=(Role("member"),),
        language="id",
        safety=CRISIS_SAFETY,
        research=research,
    )

    text = str(system_prompt(config, (Role("member"),), context=context))

    assert CRISIS_SAFETY in text
    assert research in text


def test_a_block_named_in_pinned_blocks_defeats_the_budget(tmp_path) -> None:
    """`config.prompt.pinned_blocks` pins extension blocks without editing their producer."""
    from dataclasses import replace

    config = build_config(tmp_path)
    tight_prompt = replace(config.prompt, max_chars=50)
    context = PromptContext(
        config=config, roles=(Role("member"),), language="id", memory="MEMORY " * 100
    )
    loose_config = replace(config, prompt=tight_prompt)
    pinned = replace(config, prompt=replace(tight_prompt, pinned_blocks=("memory",)))

    loose = str(
        system_prompt(
            loose_config,
            (Role("member"),),
            context=replace(context, config=loose_config),
        )
    )
    tight = str(
        system_prompt(
            pinned,
            (Role("member"),),
            context=replace(context, config=pinned),
        )
    )

    assert "MEMORY" not in loose
    assert "MEMORY" in tight
