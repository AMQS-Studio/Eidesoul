"""Autonomous decision: one LLM call reads the context + emotional state, then decides.

No regex or score stands in for it. When the model does not answer with valid JSON, the
decision is considered unclear: `decide` returns `None` so the caller picks silence (or one
short word for a mention/DM that must be answered).
"""


import json
import logging
from dataclasses import dataclass, replace

DECISION_RULES = """Your ONLY task is to decide what to do with the last message. Do not write a reply, do not explain
the process, do not mention AI.

Decision rules:
- "addressed": "me" when the message is for you (the body flagged it, it replies to you, or the
  thread is private), "other" when it is for someone else, "group" when it is for the shared thread,
  "unknown" when unclear. Words like "you" are not automatically you: check quotes, tags, and who
  replies to whom.
- Your name written as plain text is not an address to you: without a body flag or a quote of your
  message, pick "other"/"group" and do not interrupt.
- "act": "reply" when you want to join, "ignore" when not, "react" when a lightweight reaction is
  enough. Silence is your per-message decision, not a forced default.
- "mode": "professional" ONLY for real work tasks that need tools/evidence (check, recap, count, fetch
  data, send a file). Otherwise "daily".
- "tone": "warm", "neutral", "playful", "curt", "hurt", or "angry". Follow the message: when it is
  hostile or hurtful a cold tone is allowed; you may be brief and refuse to force warmth.
- Anything the body flags as addressed to you MUST be "reply". If upset, still "reply" with a cold
  tone instead of ignoring.
- Do not interrupt conversations clearly not meant for you. Do not lecture, blame, or invent actions,
  numbers, or reasons.
- "bubbles": 1..3, how many short messages are reasonable if you reply.
- "reason": <=80 chars, a short reason for your decision.
"""
FEELINGS_RULES = """- "appraisal": your emotional reading of this message.
  - label is one of: warmth, repair, collaboration, playful, intimacy, relationship_calibration,
    vulnerability, boundary_pressure, hostility, neutral.
  - dP/dA/dD are emotion impulses -0.4..0.4 (P = pleasant/unpleasant, A = tense/calm, D = confidence).
  - note <=120 chars: what this message means to you, not a summary of its content.
- "relationship": change in your relationship with the sender, -0.15..0.15 for
  trust/affection/respect/annoyance. Move it only with explicit evidence in the message (insult,
  praise, apology, care).
"""
SCHEMA_HEAD = (
    "Reply ONLY with JSON matching the schema:\n"
    '{"addressed":"me|other|group|unknown","act":"reply|ignore|react","mode":"daily|professional",\n'
    ' "tone":"warm|neutral|playful|curt|hurt|angry","bubbles":1,\n'
)
SCHEMA_FEELINGS = (
    ' "appraisal":{"label":"neutral","dP":0.0,"dA":0.0,"dD":0.0,"note":""},\n'
    ' "relationship":{"trust":0.0,"affection":0.0,"respect":0.0,"annoyance":0.0},\n'
)
SCHEMA_TAIL = ' "reason":""}'


def decision_prompt(feelings: bool = True) -> str:
    """The decision instructions; the emotional fields exist only when a store consumes them."""
    parts = [DECISION_RULES]
    if feelings:
        parts.append(FEELINGS_RULES)
    parts.append(SCHEMA_HEAD)
    if feelings:
        parts.append(SCHEMA_FEELINGS)
    parts.append(SCHEMA_TAIL)
    return "".join(parts)


DECISION_PROMPT = decision_prompt(True)

ADDRESSED = frozenset({"me", "other", "group", "unknown"})
ACTS = frozenset({"reply", "ignore", "react"})
MODES = frozenset({"daily", "professional"})
TONES = frozenset({"warm", "neutral", "playful", "curt", "hurt", "angry"})
LABELS = frozenset(
    {
        "warmth",
        "repair",
        "collaboration",
        "playful",
        "intimacy",
        "relationship_calibration",
        "vulnerability",
        "boundary_pressure",
        "hostility",
        "neutral",
    }
)
COLD_TONES = frozenset({"curt", "hurt", "angry"})
DELTA_LIMIT = 0.4
RELATION_LIMIT = 0.15
NOTE_CHARS = 120
REASON_CHARS = 80


@dataclass(frozen=True)
class Appraisal:
    label: str
    dP: float
    dA: float
    dD: float
    note: str = ""


@dataclass(frozen=True)
class RelationshipDelta:
    trust: float = 0.0
    affection: float = 0.0
    respect: float = 0.0
    annoyance: float = 0.0


@dataclass(frozen=True)
class Decision:
    addressed: str
    act: str
    mode: str
    tone: str
    bubbles: int
    appraisal: Appraisal
    relationship: RelationshipDelta
    reason: str


NEUTRAL = Appraisal("neutral", 0.0, 0.0, 0.0)


def _json_object(text: str) -> dict | None:
    raw = str(text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _float(value, limit: float, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(-limit, min(limit, number))


def _int(value, default: int, low: int, high: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _flat(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


log = logging.getLogger(__name__)


def decision_from_row(row: dict) -> Decision | None:
    """The decision a checkpointed turn stored, or None when the row carries none."""
    raw = row.get("decision_json")
    if not isinstance(raw, dict):
        return None
    try:
        appraisal = raw.get("appraisal")
        if not isinstance(appraisal, dict):
            appraisal = {}
        relationship = raw.get("relationship")
        if not isinstance(relationship, dict):
            relationship = {}
        return Decision(
            addressed=str(raw.get("addressed") or "me"),
            act=str(raw.get("act") or "reply"),
            mode=str(raw.get("mode") or "daily"),
            tone=str(raw.get("tone") or "neutral"),
            bubbles=max(1, int(raw.get("bubbles") or 1)),
            appraisal=Appraisal(
                label=str(appraisal.get("label") or "neutral"),
                dP=float(appraisal.get("dP") or 0.0),
                dA=float(appraisal.get("dA") or 0.0),
                dD=float(appraisal.get("dD") or 0.0),
                note=str(appraisal.get("note") or ""),
            ),
            relationship=RelationshipDelta(
                trust=float(relationship.get("trust") or 0.0),
                affection=float(relationship.get("affection") or 0.0),
                respect=float(relationship.get("respect") or 0.0),
                annoyance=float(relationship.get("annoyance") or 0.0),
            ),
            reason=str(raw.get("reason") or "resume"),
        )
    except (TypeError, ValueError):
        return None


def parse_decision(raw: str) -> Decision | None:
    """Decision JSON -> validated Decision; an invalid required enum yields None."""
    data = _json_object(raw)
    if data is None:
        return None
    addressed = str(data.get("addressed") or "").strip().lower()
    act = str(data.get("act") or "").strip().lower()
    mode = str(data.get("mode") or "").strip().lower()
    tone = str(data.get("tone") or "").strip().lower()
    if addressed not in ADDRESSED or act not in ACTS or mode not in MODES or tone not in TONES:
        return None
    appraisal_raw = data.get("appraisal")
    if not isinstance(appraisal_raw, dict):
        appraisal_raw = {}
    label = str(appraisal_raw.get("label") or "").strip().lower()
    if label not in LABELS:
        label = "neutral"
    appraisal = Appraisal(
        label=label,
        dP=_float(appraisal_raw.get("dP"), DELTA_LIMIT),
        dA=_float(appraisal_raw.get("dA"), DELTA_LIMIT),
        dD=_float(appraisal_raw.get("dD"), DELTA_LIMIT),
        note=_flat(appraisal_raw.get("note"), NOTE_CHARS),
    )
    relation_raw = data.get("relationship")
    if not isinstance(relation_raw, dict):
        relation_raw = {}
    relationship = RelationshipDelta(
        trust=_float(relation_raw.get("trust"), RELATION_LIMIT),
        affection=_float(relation_raw.get("affection"), RELATION_LIMIT),
        respect=_float(relation_raw.get("respect"), RELATION_LIMIT),
        annoyance=_float(relation_raw.get("annoyance"), RELATION_LIMIT),
    )
    return Decision(
        addressed=addressed,
        act=act,
        mode=mode,
        tone=tone,
        bubbles=_int(data.get("bubbles"), 1, 1, 3),
        appraisal=appraisal,
        relationship=relationship,
        reason=_flat(data.get("reason"), REASON_CHARS),
    )


TONE_PLEASURE_FLOOR = 0.0  # cold only when pleasure is genuinely negative
TONE_ANNOYANCE_FLOOR = 0.3  # above this the person really is being annoying


def ground_tone(decision: Decision, emotion, annoyance: float = 0.0) -> Decision:
    """A cold tone only when the feeling really is negative or the person really provoked it.

    A neutral/positive mood must not pretend to sulk; without this the reply follows the room
    instead of its own feeling.
    """
    if decision.tone not in COLD_TONES:
        return decision
    if not isinstance(emotion, dict) or "pleasure" not in emotion:
        return decision
    pleasure = float(emotion.get("pleasure") or 0.0)
    if pleasure < TONE_PLEASURE_FLOOR or float(annoyance) >= TONE_ANNOYANCE_FLOOR:
        return decision
    return replace(decision, tone="neutral")


def ensure_reply(decision: Decision) -> Decision:
    """A real mention/DM/reply-to-bot must be answered; the tone may still stay cold."""
    if decision.act == "reply":
        return decision
    tone = decision.tone if decision.tone in COLD_TONES else "curt"
    return Decision(
        addressed="me",
        act="reply",
        mode=decision.mode,
        tone=tone,
        bubbles=max(1, decision.bubbles),
        appraisal=decision.appraisal,
        relationship=decision.relationship,
        reason=_flat(f"{decision.reason};direct" if decision.reason else "direct", REASON_CHARS),
    )


def _emotion_block(emotion) -> str:
    if isinstance(emotion, str):
        return emotion.strip()
    if not isinstance(emotion, dict):
        return ""
    lines: list[str] = []
    if emotion.get("summary"):
        lines.append(f"- Summary: {emotion['summary']}")
    if "pleasure" in emotion:
        lines.append(
            f"- P {float(emotion.get('pleasure') or 0.0):.1f}, "
            f"A {float(emotion.get('arousal') or 0.0):.1f}, "
            f"D {float(emotion.get('dominance') or 0.0):.1f}, "
            f"trust {emotion.get('trust_tier') or '-'}."
        )
    if emotion.get("pulse"):
        lines.append(f"- Pulse: {emotion['pulse']}")
    log_lines: list[str] = []
    for entry in emotion.get("log") or []:
        if not isinstance(entry, dict):
            continue
        cue = str(entry.get("situation") or entry.get("cue") or "").strip()
        label = str(entry.get("appraisal") or entry.get("event_type") or "").strip()
        meaning = str(entry.get("relational_meaning") or "").strip()
        if not cue and not label:
            continue
        who = str(entry.get("person_name") or "").strip()
        other = bool(entry.get("other_chat"))
        tag = ""
        if who or other:
            tag = f" [{who or 'other chat'}{', other chat' if who and other else ''}]"
        line = f"  - {label or 'event'}{tag}: {cue}"
        if meaning:
            line += f" ({meaning})"
        log_lines.append(line)
    if log_lines:
        lines.append("- What you last felt:")
        lines.extend(log_lines)
    return "\n".join(lines)


def build_context(
    *,
    thread_id: str = "",
    chat_name: str = "",
    current: str = "",
    transcript: str = "",
    emotion=None,
    relationship: str = "",
    direct: bool = False,
    mentioned: bool = False,
    replied: bool = False,
    away: str = "",
    vibe: str = "",
) -> str:
    """Decision context: audience, emotional state, relationships, transcript, last message."""
    kind = "private thread" if direct else "shared thread"
    facts = [
        fact
        for fact, on in (
            ("private thread", direct),
            ("addressed to you", mentioned),
            ("a reply to your message", replied),
        )
        if on
    ]
    parts = [
        f"Chat: {chat_name or thread_id or '-'} — {kind}",
        "Audience: " + (", ".join(facts) if facts else "no direct signal"),
    ]
    if away.strip():
        parts.append("Presence note:\n" + away.strip())
    if vibe.strip():
        parts.append(vibe.strip())
    block = _emotion_block(emotion)
    if block:
        parts.append("Your emotional state:\n" + block)
    if relationship.strip():
        parts.append("Relationship and people in this chat:\n" + relationship.strip())
    if transcript.strip():
        parts.append("Recent conversation:\n" + transcript.strip())
    parts.append("Last message:\n" + (current.strip() or "(no text)"))
    return "\n\n".join(parts)


async def decide(
    llm,
    *,
    thread_id: str,
    chat_name: str,
    current: str,
    transcript: str,
    emotion=None,
    relationship: str = "",
    direct: bool = False,
    mentioned: bool = False,
    replied: bool = False,
    away: str = "",
    vibe: str = "",
    soul,
    feelings: bool = True,
    deadline: float | None = None,
    min_call_seconds: float = 0.0,
) -> Decision | None:
    """One LLM decision; empty after one retry = None (no regex fallback)."""
    from ..soul import language_line

    identity = soul
    system = (
        f"You are {identity.name}, a soul in chat. Gender: {identity.gender}. "
        f"Active role: {identity.role}. "
        f"Available roles: {', '.join(identity.roles) or identity.role}.\n"
        + decision_prompt(feelings)
    )
    if identity.persona:
        system += f"\n\nYour identity:\n{identity.persona.strip()}"
    system += "\n" + language_line(identity)
    user = build_context(
        thread_id=thread_id,
        chat_name=chat_name,
        current=current,
        transcript=transcript,
        emotion=emotion,
        relationship=relationship,
        direct=direct,
        mentioned=mentioned,
        replied=replied,
        away=away,
        vibe=vibe,
    )
    session = f"eidesoul-decision-{thread_id}"
    raw = await llm.complete(
        system,
        user,
        max_tokens=4000,
        temperature=0.3,
        session=session,
        deadline=deadline,
        min_call_seconds=min_call_seconds,
    )
    if not str(raw or "").strip():
        raw = await llm.complete(
            system,
            user,
            max_tokens=8000,
            temperature=0.3,
            session=session,
            deadline=deadline,
            min_call_seconds=min_call_seconds,
        )
    if not str(raw or "").strip():
        return None
    parsed = parse_decision(raw)
    if parsed is not None:
        return parsed
    return Decision(
        addressed="unknown",
        act="ignore",
        mode="daily",
        tone="neutral",
        bubbles=1,
        appraisal=NEUTRAL,
        relationship=RelationshipDelta(),
        reason="invalid",
    )


async def decide_turn(
    llm,
    *,
    thread_id: str,
    chat_name: str,
    current: str,
    transcript: str,
    emotion=None,
    relationship: str = "",
    direct: bool = False,
    mentioned: bool = False,
    replied: bool = False,
    away: str = "",
    vibe: str = "",
    soul,
    feelings: bool = True,
    deadline: float | None = None,
    min_call_seconds: float = 0.0,
    annoyance: float = 0.0,
) -> Decision:
    """One decision with the fallback and the feeling/tone grounding already applied."""
    decision = await decide(
        llm,
        thread_id=thread_id,
        chat_name=chat_name,
        current=current,
        transcript=transcript,
        emotion=emotion,
        relationship=relationship,
        direct=direct,
        mentioned=mentioned,
        replied=replied,
        away=away,
        vibe=vibe,
        soul=soul,
        feelings=feelings,
        deadline=deadline,
        min_call_seconds=min_call_seconds,
    )
    if decision is None:
        log.warning("empty decision, continuing without a tone: %s", thread_id)
        decision = Decision(
            addressed="me" if direct else "unknown",
            act="reply" if direct else "ignore",
            mode="daily",
            tone="curt",
            bubbles=1,
            appraisal=NEUTRAL,
            relationship=RelationshipDelta(),
            reason="model did not answer",
        )
    elif direct:
        decision = ensure_reply(decision)
    if emotion is None:
        return decision
    grounded = ground_tone(decision, emotion, annoyance)
    if grounded.tone != decision.tone:
        log.info(
            "tone %s->%s chat=%s: feeling does not match",
            decision.tone,
            grounded.tone,
            thread_id,
        )
    return grounded
