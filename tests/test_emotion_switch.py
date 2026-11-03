"""The emotion layer is optional: off means no state, no relationship, no feeling fields."""

import asyncio

from support import inbound_message, make_agent

DM = "628111@s.whatsapp.net"


def handle(env, inbound):
    return asyncio.run(env.agent.handle(inbound, env.gateway))


def test_emotion_off_leaves_both_prompts_clean(tmp_path) -> None:
    env = make_agent(tmp_path)  # affect=None and vibe=None

    handle(env, inbound_message("halo", thread=DM))

    assert "Your emotional state" not in env.llm.complete_user
    assert "Relationship and people" not in env.llm.complete_user
    assert "appraisal" not in env.llm.complete_system
    assert "relationship" not in env.llm.complete_system
    assert "Your emotional state" not in env.llm.system
    assert "## Feelings" not in env.llm.system


def test_emotion_on_adds_the_state_and_the_fields(tmp_path) -> None:
    env = make_agent(tmp_path, affect=True)

    handle(env, inbound_message("halo", thread=DM))

    assert "Your emotional state" in env.llm.complete_user
    assert '"appraisal"' in env.llm.complete_system
    assert '"relationship"' in env.llm.complete_system
