"""Claim guard (`note.md` §10): an inability needs a tool result, not a transcript line.

The guard, the verified-facts block, and the catalog markers are one contract: a draft may not
assert an access/capability limit the turn never checked, and the code proves what the sender
may reach instead of letting the model guess from its own history.
"""

import asyncio
from dataclasses import replace
from datetime import datetime

from support import inbound_message, make_agent

from monoagens.engine import claims
from monoagens.engine.access.roles import Role
from monoagens.engine.config import ClaimGuardConfig
from monoagens.engine.i18n import t
from monoagens.engine.tools import execute
from monoagens.engine.turn import facts
from monoagens.engine.turn.context import Message

CLAIM = "Aku tidak bisa membaca arsip grup itu."
ANSWER = "Ini isi arsipnya: rapat jam tiga."
ROOM = "111@g.us"


def scripted(env, answers, *, call_tool=None, judge="YES"):
    """Patch the fixture model: a scripted draft per call, plus the claim judge's verdict."""
    systems: list[str] = []
    judged: list[str] = []

    async def run(system, content, spec_map, ctx, session=None, **kwargs):
        systems.append(str(system))
        if call_tool is not None and len(systems) == 1:
            await call_tool(spec_map, ctx)
        return answers[min(len(systems) - 1, len(answers) - 1)]

    original = env.llm.complete

    async def complete(system, user, **kwargs):
        if kwargs.get("purpose") != "claim_guard":
            return await original(system, user, **kwargs)  # the decision step is untouched
        judged.append(str(user))
        return judge

    env.llm.run = run
    env.llm.complete = complete
    return systems, judged


def turn(env, text: str = "apa isi arsip grup itu?") -> str:
    asyncio.run(env.agent.handle(inbound_message(text, thread="chat"), env.gateway))
    return env.gateway.sent[-1][1]


def test_a_claim_without_a_tool_result_is_re_asked_once(tmp_path) -> None:
    env = make_agent(tmp_path)
    systems, judged = scripted(env, [CLAIM, ANSWER])

    reply = turn(env)

    assert len(systems) == 2
    assert len(judged) == 1  # one verdict per guard attempt, then the single re-ask
    assert claims.NOTE not in systems[0]
    assert claims.NOTE in systems[1]
    assert reply == ANSWER


def test_a_turn_that_already_ran_a_denied_tool_is_not_re_asked(tmp_path) -> None:
    env = make_agent(tmp_path)

    async def denied(spec_map, ctx):
        # A real tool call: the code answers, so the guard must not spend a second model call.
        specs = {spec.name: spec for spec in spec_map}
        assert await execute(specs, "thread_history", {"chat": "222@g.us"}, ctx) == (
            "denied: room"
        )

    systems, judged = scripted(env, [CLAIM], call_tool=denied)

    reply = turn(env)

    assert len(systems) == 1
    assert judged == []  # the judge is never asked when the code already answered
    assert reply == CLAIM


def test_an_official_policy_refusal_is_never_re_asked(tmp_path) -> None:
    env = make_agent(tmp_path)
    notice = t("agent.access_refused", env.config.soul.language)
    systems, _judged = scripted(env, [notice])

    reply = turn(env)

    assert len(systems) == 1
    assert reply == notice


def test_the_guard_can_be_disabled_in_config(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.config = replace(
        env.config,
        turns=replace(env.config.turns, claim_guard=ClaimGuardConfig(enabled=False)),
    )
    systems, judged = scripted(env, [CLAIM, ANSWER])

    reply = turn(env)

    assert len(systems) == 1
    assert judged == []
    assert reply == CLAIM


def test_a_draft_without_a_claim_is_sent_unchanged(tmp_path) -> None:
    env = make_agent(tmp_path)
    systems, judged = scripted(env, [ANSWER], judge="NO")

    reply = turn(env)

    assert len(systems) == 1
    assert len(judged) == 1
    assert reply == ANSWER


def test_a_second_claim_after_the_retry_is_sent_as_is(tmp_path) -> None:
    env = make_agent(tmp_path)
    systems, _judged = scripted(env, [CLAIM, "Maaf, aku bukan anggota ruangan itu."])

    reply = turn(env)

    assert len(systems) == 2  # max_retries: 1, never a loop
    assert reply == "Maaf, aku bukan anggota ruangan itu."


def test_the_judge_decides_a_claim_in_any_language() -> None:
    """No phrase list: the light judge reads the draft, whatever language it is written in."""

    class Judge:
        def __init__(self, answer: str) -> None:
            self.answer = answer
            self.calls: list[dict] = []

        async def complete(self, system, user, **kwargs):
            self.calls.append({"system": str(system), "user": str(user), **kwargs})
            return self.answer

    yes = Judge("YES")
    no = Judge("no")
    broken = Judge("")

    assert asyncio.run(claims.detect(yes, "Aku tidak bisa membaca arsip grup itu", "id"))
    assert not asyncio.run(claims.detect(no, "Ini isi arsipnya: rapat jam tiga", "id"))
    assert not asyncio.run(claims.detect(broken, "no puedo acceder al archivo", "es"))
    assert not asyncio.run(claims.detect(None, "no puedo acceder al archivo", "es"))
    # The judge sees the draft and its language, never a keyword table.
    assert yes.calls[0]["user"].endswith("Aku tidak bisa membaca arsip grup itu")
    assert "Language: id" in yes.calls[0]["user"]
    assert yes.calls[0]["purpose"] == "claim_guard"


def test_a_failing_judge_never_blocks_the_turn() -> None:
    class Broken:
        async def complete(self, system, user, **kwargs):
            raise RuntimeError("provider down")

    assert not asyncio.run(claims.detect(Broken(), CLAIM, "id"))


def member_env(tmp_path):
    """One archived room the sender wrote in, plus the fixture directory."""
    env = make_agent(tmp_path)
    env.history.add(
        ROOM,
        Message(
            id="m1",
            sender_name="Budi",
            sender_id="628111",
            when=datetime(2026, 1, 1, 10),
            text="rapat jam tiga",
        ),
    )
    return env


def test_verified_facts_name_rooms_roles_and_tools_in_a_direct_chat(tmp_path) -> None:
    env = member_env(tmp_path)
    roles = (env.config.roles.get("member"),)

    block = facts.verified_facts(
        env.agent.turn_runtime,
        inbound_message("halo", thread="628111@s.whatsapp.net", dm=True),
        roles,
        env.gateway,
    )

    assert f'"{ROOM}"' in block
    assert "- Roles: member." in block
    assert "- Tools you may call here:" in block
    assert "rapat jam tiga" not in block  # names only, never message content


def test_a_shared_room_only_names_the_gateway(tmp_path) -> None:
    env = member_env(tmp_path)
    roles = (Role("member"),)

    block = facts.verified_facts(
        env.agent.turn_runtime,
        inbound_message("halo", thread=ROOM, dm=False),
        roles,
        env.gateway,
    )

    assert "fake" in block
    assert ROOM not in block
    assert "member" not in block


def test_the_facts_block_reaches_the_rendered_prompt(tmp_path) -> None:
    env = member_env(tmp_path)

    async def run(system, content, spec_map, ctx, session=None, **kwargs):
        env.llm.system = str(system)
        return ANSWER

    env.llm.run = run
    turn(env)

    assert "Verified facts from the runtime" in env.llm.system
    assert f'"{ROOM}"' in env.llm.system
    assert "rapat jam tiga" not in env.llm.system


