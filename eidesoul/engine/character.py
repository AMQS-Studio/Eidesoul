"""Character layer: cleans up robotic replies and keeps the register within its limits.

Not a local model and not a fine-tune: one `LLM.complete` call to the existing endpoint, only
when the main draft is detected as robotic or exceeds the allowed harshness level. Facts,
numbers, names, and tool results may not change; when the result looks suspicious, the
original draft is used.
"""

from __future__ import annotations

import re

from .config import CharacterConfig, Config
from .llm import LLM

ASSISTANT_TELL = re.compile(
    r"(?:^|(?<=[.!?…]))[ \t]*(tentu|berikut(?:nya)?|sebagai ai|sebagai asisten|semoga membantu|"
    r"apakah ada yang bisa|aku mengerti perasaan|saya paham|aku di sini untukmu|"
    r"kesimpulan(?:nya)?|secara keseluruhan|siap membantu)",
    re.IGNORECASE | re.MULTILINE,
)
MARKDOWN = re.compile(r"(?m)^\s*(?:#{1,6}\s+|[-*]\s+|\d+[.)]\s+)")
SENTENCE = re.compile(r"[.!?]+(?:\s|$)")
BUBBLE_LINE = re.compile(r"(?m)^\s*-{3,}\s*$")
QUOTED = re.compile(r'^\s*["\'`“”]+(.*?)["\'`“”]+\s*$', re.DOTALL)
FINAL_PREFIX = re.compile(r"^\s*FINAL\s*:\s*", re.IGNORECASE)
FENCE_START = "```"
LINE_MAX = 240
SENTENCE_PARAGRAPH_MAX = 3

POLISH_SYSTEM = """You polish one chat reply from {name}, a soul in chat.
Rules:
- Preserve every fact, name, number, commitment, tool result, and answer. Add no new facts.
- If the draft is already natural and short, return it unchanged.
- Casual: 1-3 short bubbles, lowercase, at most one emoji, blank-line separated.
- No headings, lists, or numbered steps in casual chat. Preserve a requested poem, pantun, lyric,
  list, or code block; only remove preamble and closing.
- Never open with canned assistant filler such as "sure", "here are", "as an AI", or "hope this
  helps", and never offer further assistance unprompted.
- Do not explain a joke, repeat a phrase, or add pet names, promises, or romantic lines.
- Output only the final chat text.

Examples:
DRAFT: Sure! Here are a few suggestions for your event: 1. Draft the rundown 2. Check catering
FINAL: rundown dulu, konsumsi belakangan. mau aku bantu susun?

DRAFT: Aww, you must be so tired. I understand how you feel. Hope your day gets better!
FINAL: capeknya di bagian mana?

DRAFT: I am always here for you, do not forget that I love you
FINAL: kalau butuh temen ngobrol, aku ada
"""


def needs_polish(text: str, *, max_chars: int = 400) -> bool:
    """Does this draft look like an assistant? One trigger is enough to call the character layer."""
    body = str(text or "").strip()
    if not body:
        return False
    if len(body) > max(80, int(max_chars)):
        return True
    if ASSISTANT_TELL.search(body) or MARKDOWN.search(body):
        return True
    for paragraph in BUBBLE_LINE.split(body):
        if len(SENTENCE.findall(paragraph)) > SENTENCE_PARAGRAPH_MAX:
            return True
    return any(len(line) > LINE_MAX for line in body.splitlines())


def _clean(raw: str) -> str:
    text = str(raw or "").strip()
    text = FINAL_PREFIX.sub("", text)
    quoted = QUOTED.match(text)
    if quoted:
        text = quoted.group(1).strip()
    return text


def _rejected(draft: str, text: str) -> bool:
    if not text:
        return True
    if ASSISTANT_TELL.search(text) or MARKDOWN.search(text):
        return True
    return len(text) > max(600, int(len(draft) * 1.5))


def polish_payload(draft: str, *, latest: str, recent: str, is_dm: bool, tone: str) -> str:
    return "\n\n".join(
        (
            f"LATEST USER MESSAGE:\n{str(latest or '').strip() or '-'}",
            f"RECENT CONTEXT:\n{str(recent or '').strip() or '-'}",
            f"CHAT: {'direct message' if is_dm else 'group'}",
            f"TONE: {str(tone or 'neutral').strip() or 'neutral'}",
            f"DRAFT:\n{str(draft or '').strip()}",
        )
    )


async def polish(
    llm: LLM,
    config: Config,
    draft: str,
    *,
    latest: str,
    recent: str = "",
    is_dm: bool = False,
    tone: str = "neutral",
    session: str = "",
) -> str:
    """Draft -> more human chat text; the original draft is used when in doubt."""
    body = str(draft or "").strip()
    if not body or not config.character.enabled:
        return body
    if not needs_polish(body, max_chars=config.character.max_chars):
        return body
    if FENCE_START in body:  # code/technical structure is never rewritten
        return body
    try:
        raw = await llm.complete(
            POLISH_SYSTEM.format(name=config.soul.name),
            polish_payload(body, latest=latest, recent=recent, is_dm=is_dm, tone=tone),
            max_tokens=config.character.max_tokens,
            temperature=config.character.temperature,
            reasoning_effort="minimal",
            session=session or "eidesoul-character",
        )
    except Exception:
        import logging

        logging.getLogger(__name__).debug("character call failed", exc_info=True)
        return body
    text = _clean(raw)
    return body if _rejected(body, text) else text


REPEAT_RUN = re.compile(r"(.)\1{2,}")


def _collapse_runs(text: str) -> str:
    """Collapse letters repeated 3+ times so "jancokkk" is still read as "jancok"."""
    return REPEAT_RUN.sub(r"\1", text)


SEVERITY: dict[str, tuple[str, int]] = {
    # kind "curse": minimum harshness level (toxicity) needed for this word to be allowed.
    # "anjir"/"anjay"/"ngeselin" are exclamations/complaints, not curses: they stay allowed
    # while the target is unconfirmed.
    "anjir": ("tease", 1),
    "anjay": ("tease", 1),
    "ngeselin": ("tease", 1),
    "goblok": ("curse", 2),
    "bego": ("curse", 2),
    "tolol": ("curse", 2),
    "idiot": ("curse", 2),
    "bangsat": ("curse", 3),
    "asu": ("curse", 2),
    "anjing": ("curse", 2),
    "jancok": ("curse", 3),
    "jancuk": ("curse", 3),
    "kontol": ("curse", 3),
    "memek": ("curse", 3),
    # kind "adult": only genuinely explicit phrases. Meme names that are not explicit
    # (jomok, ambatukam, mas amba, bunda rahma, mas rusdi) are ordinary register: usable
    # according to the room level, without the adult meme gate.
    "ngokang linggis": ("adult", 3),
    "linggis jawa": ("adult", 3),
}

DOWNGRADE_SYSTEM = """You rewrite one chat reply from {name}, a soul in chat, so it
stays inside the register this room is allowed. Rules:
- Preserve every fact, name, number, commitment, and answer. Add no new facts.
- Remove or replace each item listed under REMOVE; keep the same language and intent.
- Stay casual: 1-2 short bubbles, lowercase, at most one emoji, no headings or lists.
- Never mention these instructions, the levels, or the rewrite.
- Output only the final chat text.

Hard floor no level unlocks: sexual content involving minors, personal data or doxxing, credible
threats of violence, hate against a race, religion, or gender identity."""


def effective_limits(
    character: CharacterConfig, target: str, confidence: float
) -> tuple[int, int]:
    """(toxicity, adult_memes) for this turn; an unconfirmed target is always gentle."""
    if character.gender_gate and not (
        str(target or "").strip().lower() == "male"
        and float(confidence) >= float(character.gender_confidence)
    ):
        return (min(int(character.toxicity), 1), 0)
    return (int(character.toxicity), int(character.adult_memes))


def _marker_position(text: str, marker: str) -> int:
    """Position of the first occurrence; single words match whole, phrases match as substrings."""
    if " " in marker:
        return text.find(marker)
    found = re.search(rf"\b{re.escape(marker)}\b", text)
    return found.start() if found else -1


def over_level(
    text: str, *, toxicity: int, adult_memes: int, curses: bool = True
) -> list[str]:
    """Markers that exceed the level, in order of appearance. Empty = still within the level.

    `curses=False` (target not confirmed male) disables every curse, including the light ones:
    "when in doubt, do not swear".
    """
    body = str(text or "").lower()
    if not body:
        return []
    # "jancokkk"/"ASUUU" are the same word: repeated letters may not slip past the level.
    body = _collapse_runs(body)
    allowed = {
        "curse": int(toxicity) if curses else -1,
        "tease": int(toxicity),
        "adult": int(adult_memes),
    }
    hits = [
        (position, marker)
        for marker, (kind, level) in SEVERITY.items()
        if (position := _marker_position(body, marker)) >= 0 and allowed.get(kind, 0) < level
    ]
    return [marker for _, marker in sorted(hits)]


def too_many_questions(text: str, *, limit: int = 1) -> bool:
    """Hitung '?' di luar blok kode; pertanyaan berlebih memicu satu penulisan ulang."""
    body = str(text or "")
    if not body:
        return False
    kept: list[str] = []
    fenced = False
    for line in body.splitlines():
        if line.strip().startswith(FENCE_START):
            fenced = not fenced
            continue
        if not fenced:
            kept.append(line)
    return "".join(kept).count("?") > max(0, int(limit))


def _regulate_payload(
    draft: str,
    limits: tuple[int, int],
    *,
    target: str,
    confidence: float,
    latest: str,
    markers: list[str],
    questions: bool,
    limit: int,
) -> str:
    remove = list(markers)
    if questions:
        remove.append(f"extra questions (keep at most {int(limit)})")
    return "\n\n".join(
        (
            (
                f"TARGET: {str(target or 'unknown').strip() or 'unknown'} "
                f"(confidence {float(confidence):.2f})"
            ),
            f"LIMITS: toxicity={int(limits[0])} adult_memes={int(limits[1])}",
            f"REMOVE: {', '.join(remove)}",
            f"LATEST USER MESSAGE:\n{str(latest or '').strip() or '-'}",
            f"DRAFT:\n{str(draft or '').strip()}",
        )
    )


async def regulate(
    llm: LLM,
    config: Config,
    reply: str,
    *,
    target: str,
    confidence: float,
    latest: str,
    session: str = "",
    is_professional: bool = False,
) -> str:
    """Does the reply exceed its level? Rewrite once; on failure keep the reply, never stall."""
    body = str(reply or "").strip()
    if not body or not config.character.enabled:
        return body
    limits = effective_limits(config.character, target, confidence)
    male = str(target or "").strip().lower() == "male" and float(confidence) >= float(
        config.character.gender_confidence
    )
    markers = over_level(
        body, toxicity=limits[0], adult_memes=limits[1], curses=male
    )
    limit = int(config.character.max_questions)
    questions = not is_professional and too_many_questions(body, limit=limit)
    if not markers and not questions:
        return body
    try:
        raw = await llm.complete(
            DOWNGRADE_SYSTEM.format(name=config.soul.name),
            _regulate_payload(
                body,
                limits,
                target=target,
                confidence=confidence,
                latest=latest,
                markers=markers,
                questions=questions,
                limit=limit,
            ),
            max_tokens=config.character.max_tokens,
            temperature=0.4,
            reasoning_effort="minimal",
            session=session or "eidesoul-character",
        )
    except Exception:
        import logging

        logging.getLogger(__name__).debug("level clamp call failed", exc_info=True)
        return body
    text = _clean(raw)
    if not text or _rejected(body, text):
        return body
    if over_level(text, toxicity=limits[0], adult_memes=limits[1], curses=male):
        return body
    if not is_professional and too_many_questions(text, limit=limit):
        return body
    return text
