"""Read-only ablation: replay reply rules, prompt budget, and decision shifts.

This harness never calls `handle_message`, never sends a message, and never writes runtime
state. Every timestamp comes from `message.when` so the replay is deterministic.
"""


import dataclasses
from dataclasses import dataclass

from .config import Config
from .roles import Role
from .turn import salience
from .turn.context import AUTHOR_BOT, AUTHOR_MANUAL, Message, prompt_entry
from .turn.decision import decide
from .turn.prompts import system_prompt

REPLY_WINDOW = 1
TRANSCRIPT_LINES = 30
ABLATABLE = ("emotion", "vibe", "away")


@dataclass(frozen=True)
class Row:
    message: Message
    called: bool
    replied: bool


def classify(messages: list[Message], *, bot_name: str) -> list[Row]:
    """Replay the reply gate from the archive; one row per incoming message, chronological.

    The archive does not store the real mention, so `called` only comes from quotes of the bot:
    a lower bound of the runtime gate, which also sees the real mentions.
    """
    rows: list[Row] = []
    pending = -1  # index of the incoming row still waiting for a reply
    pending_at = -1  # its position in the archive, for the reply window
    for position, message in enumerate(messages):
        if message.author == AUTHOR_BOT:
            if pending < 0 or position - pending_at > REPLY_WINDOW:
                continue
            rows[pending] = dataclasses.replace(rows[pending], replied=True)
            pending = -1
            continue
        called = message.quote is not None and message.quote.sender_name == bot_name
        rows.append(Row(message, called, False))
        pending = len(rows) - 1
        pending_at = position
    return rows


def replay_metrics(rows: list[Row]) -> dict:
    """Gate counts: calls, replies, and wrong-direction mistakes."""
    incoming = len(rows)
    false_positive = sum(1 for row in rows if row.replied and not row.called)
    return {
        "incoming": incoming,
        "called": sum(1 for row in rows if row.called),
        "replied": sum(1 for row in rows if row.replied),
        "false_positive": false_positive,
        "missed": sum(1 for row in rows if row.called and not row.replied),
        "unprompted_rate": false_positive / incoming if incoming else 0.0,
    }


def _mean(values: list[int]) -> float:
    return sum(values) / len(values) if values else 0.0


def prompt_budget(
    config: Config,
    messages: list[Message],
    *,
    sample: int,
    blocks_for,
) -> dict:
    """System prompt size before/after block competition, plus how often a block is dropped."""
    incoming = [
        message for message in messages if message.author not in {AUTHOR_BOT, AUTHOR_MANUAL}
    ]
    if sample > 0:
        incoming = incoming[-sample:]
    stripped = dataclasses.replace(
        config, prompt=dataclasses.replace(config.prompt, salience=False)
    )
    mandatory = {
        "base": "x",
        "self": "",
        "roles": "",
        "safety": "",
        "away": "",
        "emotion": "x",
        "vibe": "x",
        "memory": "",
        "actions": "",
    }
    before: list[int] = []
    after: list[int] = []
    dropped: dict[str, int] = {}
    for message in incoming:
        source = blocks_for(message)
        blocks = {
            name: source.get(name, "")
            for name in (*salience.COMPETING, "memory", "actions")
        }
        member = (Role(name="member"),)
        before.append(len(system_prompt(stripped, member, query=message.text, **blocks)))
        after.append(
            len(system_prompt(config, member, query=message.text, bonuses={}, **blocks))
        )
        _, names = salience.select(
            {**mandatory, **blocks},
            message.text,
            bonuses={},
            max_chars=config.prompt.max_chars,
            enabled=config.prompt.salience,
        )
        for name in names:
            dropped[name] = dropped.get(name, 0) + 1
    return {
        "samples": len(incoming),
        "before_mean": _mean(before),
        "after_mean": _mean(after),
        "saved_mean": _mean(before) - _mean(after),
        "dropped": dropped,
    }


def emotion_brief(affect, thread_id: str) -> str | dict:
    """Read-only summary of the emotion state; `""` when the state is missing or unreadable.

    `status()` may write a default state when no file exists yet, so the harness only reads it
    when the state folder already holds real state.
    """
    if affect is None:
        return ""
    try:
        status = affect.status(thread_id)
    except (OSError, ValueError, KeyError):
        return ""
    return {
        key: status[key] for key in ("summary", "pleasure", "arousal", "dominance", "trust_tier")
    }


def _transcript(history: list[Message], target: Message, bot_name: str) -> str:
    index = next((i for i, item in enumerate(history) if item is target), len(history))
    window = history[max(0, index - TRANSCRIPT_LINES) : index]
    return "\n".join(prompt_entry(item, bot_name, 500) for item in window)


async def decision_shift(
    llm,
    config: Config,
    rows: list[Row],
    *,
    sample: int,
    ablate: tuple[str, ...],
    thread_id: str = "",
    history: list[Message] | None = None,
    affect=None,
    direct: bool = True,
) -> dict:
    """Compare the gate decision with and without the ablated blocks (a real LLM)."""
    if sample <= 0:
        return {
            "samples": 0,
            "addressed_changed": 0,
            "act_changed": 0,
            "tone_changed": 0,
            "examples": [],
        }
    sampled = rows[-sample:]
    messages = history if history is not None else [row.message for row in rows]
    bot_name = config.soul.name
    counts = {"addressed_changed": 0, "act_changed": 0, "tone_changed": 0}
    compared = 0
    examples: list[dict] = []
    for row in sampled:
        message = row.message
        current = prompt_entry(message, bot_name, 500)
        transcript = _transcript(messages, message, bot_name)
        common = {
            "thread_id": thread_id,
            "chat_name": "",
            "current": current,
            "transcript": transcript,
            "relationship": "",
            "direct": direct,
            "mentioned": False,
            "replied": message.quote is not None and message.quote.sender_name == bot_name,
            "away": "" if "away" in ablate else "catatan",
            "vibe": "" if "vibe" in ablate else "suasana hangat",
            "soul": config.soul.persona,
            "language": config.soul.language,
        }
        emotion = emotion_brief(affect, thread_id)
        before = await decide(llm, emotion=emotion, **common)
        after = await decide(llm, emotion="" if "emotion" in ablate else emotion, **common)
        if before is None or after is None:
            continue
        compared += 1
        changed = {
            "addressed_changed": before.addressed != after.addressed,
            "act_changed": before.act != after.act,
            "tone_changed": before.tone != after.tone,
        }
        for key, hit in changed.items():
            counts[key] += int(hit)
        if any(changed.values()) and len(examples) < 3:
            examples.append(
                {
                    "text": message.text,
                    "before": {
                        "addressed": before.addressed,
                        "act": before.act,
                        "tone": before.tone,
                    },
                    "after": {
                        "addressed": after.addressed,
                        "act": after.act,
                        "tone": after.tone,
                    },
                }
            )
    return {"samples": compared, **counts, "examples": examples}
