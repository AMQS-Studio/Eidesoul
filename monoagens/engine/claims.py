"""Claim detection: an inability is a tool result, never a transcript line or a memory.

`note.md` §10: a model that repeats "I cannot read that room" from its own history, from a
stored memory, or from an unverified guess must be corrected before the reply is sent. There is
no phrase list here: a refusal can be worded in millions of ways in any language, so the check
is one light yes/no model call over the draft, bounded by the caller's retry budget.
"""

from . import i18n

# The one system correction the guard adds before it re-asks (prompt scaffolding, English).
NOTE = (
    "Your draft said something is impossible without a tool result. Call the relevant tool "
    "(thread_history, info, memory, or the body tool that owns the target) and answer from what "
    "it returns. Never state an inability, a missing archive, or somebody's membership before "
    "checking it in this turn."
)

# The judge: one yes/no token, so parsing never depends on the reply language.
JUDGE = (
    "Answer with one word only. Does the text below claim that you (the assistant) cannot "
    "read, send, reach, or access something, that an archive does not exist, or that somebody "
    "is not a member of a room? YES or NO."
)
JUDGE_CHARS = 1200
JUDGE_TOKENS = 4

# A refusal the code itself produced is authoritative: the guard never re-asks it.
OFFICIAL = (
    "agent.access_refused",
    "agent.cross_chat_blocked",
    "agent.cross_room_blocked",
    "agent.research_abstain",
)


async def detect(llm, text: str, language: str = "") -> bool:
    """True when the text asserts an inability about access, capability, or membership.

    One small tool-free call, never a keyword match: the wording and the language are the
    model's to judge. A missing model or a failed call answers "not a claim", so detection can
    only ever cost the retry it guards, never a turn.
    """
    body = " ".join(str(text or "").split())[:JUDGE_CHARS]
    if llm is None or not body:
        return False
    try:
        answer = await llm.complete(
            JUDGE,
            f"Language: {language or 'unknown'}\nText:\n{body}",
            max_tokens=JUDGE_TOKENS,
            purpose="claim_guard",
        )
    except Exception:
        return False
    return str(answer or "").strip().upper().startswith("YES")


def official(text: str, language: str) -> bool:
    """True when the text is (or quotes) a refusal the code produced, not a model claim."""
    body = " ".join(str(text or "").split())
    if not body:
        return False
    for key in OFFICIAL:
        message = " ".join(i18n.t(key, language).split())
        if message and message in body:
            return True
    return False
