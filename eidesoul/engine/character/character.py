"""Character layer: cleans up robotic replies and keeps the register within its limits.

Not a local model and not a fine-tune: one `LLM.complete` call to the existing endpoint, only
when the main draft is detected as robotic or exceeds the allowed harshness level. Facts,
numbers, names, and tool results may not change; when the result looks suspicious, the
original draft is used.
"""


import json
import re
from functools import lru_cache
from pathlib import Path

from ..config import CharacterConfig, Config
from ..llm import LLM, DeadlineExceeded
from . import lexicon

MARKDOWN = re.compile(r"(?m)^\s*(?:#{1,6}\s+|[-*]\s+|\d+[.)]\s+)")
SENTENCE = re.compile(r"[.!?]+(?:\s|$)")
BUBBLE_LINE = re.compile(r"(?m)^\s*-{3,}\s*$")
QUOTES = "\"'`\u201c\u201d"
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
"""


@lru_cache(maxsize=8)
def _assistant_tell(language: str, folder: str, stamp: int) -> re.Pattern[str] | None:
    # `folder`/`stamp` are part of the cache key: a reconfigured or reloaded lexicon never serves
    # a pattern compiled from the previous pack.
    phrases = lexicon.load(language).assistant_tells
    if not phrases:
        return None
    body = "|".join(re.escape(item) for item in sorted(phrases, key=len, reverse=True))
    return re.compile(
        rf"(?:^|(?<=[.!?…]))[ \t]*(?:{body})", re.IGNORECASE | re.MULTILINE
    )


def assistant_tell(language: str) -> re.Pattern[str] | None:
    """Compiled assistant-tell pattern for one language; None when its pack carries none."""
    code = str(language or "").strip().lower()
    if not code:
        return None
    return _assistant_tell(code, str(lexicon.directory()), lexicon.generation())


def polish_system(name: str, language: str) -> str:
    """The polish prompt plus the pack's examples; none when the pack omits them."""
    prompt = POLISH_SYSTEM.format(name=name)
    examples = lexicon.load(language).example("character_polish_examples")
    return f"{prompt}\nExamples:\n{examples}\n" if examples else prompt


def needs_polish(text: str, *, max_chars: int = 400, language: str) -> bool:
    """Does this draft look like an assistant? One trigger is enough to call the character layer."""
    body = str(text or "").strip()
    if not body:
        return False
    if len(body) > max(80, int(max_chars)):
        return True
    tell = assistant_tell(language)
    if (tell is not None and tell.search(body)) or MARKDOWN.search(body):
        return True
    for paragraph in BUBBLE_LINE.split(body):
        if len(SENTENCE.findall(paragraph)) > SENTENCE_PARAGRAPH_MAX:
            return True
    return any(len(line) > LINE_MAX for line in body.splitlines())


def unwrap_quotes(text: str) -> str:
    """Inner text when the whole string is wrapped in quote marks, else `text`.

    A linear scan: the opening run is consumed, the closing run must be quote marks only, so the
    inner text ends at the first quote mark of that run. No regex, so an unclosed quote cannot
    backtrack.
    """
    body = str(text or "").strip()
    lead = 0
    while lead < len(body) and body[lead] in QUOTES:
        lead += 1
    if not lead:
        return text
    close = len(body)
    while close > 0 and body[close - 1].isspace():
        close -= 1
    run = close
    while run > 0 and body[run - 1] in QUOTES:
        run -= 1
    for opening in range(lead, 0, -1):
        inner_end = max(run, opening)
        if inner_end < close:
            return body[opening:inner_end]
    return text


def _clean(raw: str) -> str:
    text = str(raw or "").strip()
    text = FINAL_PREFIX.sub("", text)
    return unwrap_quotes(text).strip()


def _rejected(draft: str, text: str, language: str) -> bool:
    if not text:
        return True
    tell = assistant_tell(language)
    if (tell is not None and tell.search(text)) or MARKDOWN.search(text):
        return True
    return len(text) > max(600, int(len(draft) * 1.5))


def polish_payload(draft: str, *, latest: str, recent: str, direct: bool, tone: str) -> str:
    return "\n\n".join(
        (
            f"LATEST USER MESSAGE:\n{str(latest or '').strip() or '-'}",
            f"RECENT CONTEXT:\n{str(recent or '').strip() or '-'}",
            f"CHAT: {'direct message' if direct else 'group'}",
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
    direct: bool = False,
    tone: str = "neutral",
    session: str = "",
    deadline: float | None = None,
    min_call_seconds: float = 0.0,
) -> str:
    """Draft -> more human chat text; the original draft is used when in doubt."""
    body = str(draft or "").strip()
    language = config.soul.language
    if not body or not config.character.enabled:
        return body
    if not needs_polish(body, max_chars=config.character.max_chars, language=language):
        return body
    if FENCE_START in body:  # code/technical structure is never rewritten
        return body
    try:
        raw = await llm.complete(
            polish_system(config.soul.name, language),
            polish_payload(body, latest=latest, recent=recent, direct=direct, tone=tone),
            max_tokens=config.character.max_tokens,
            temperature=config.character.temperature,
            session=session or "eidesoul-character",
            deadline=deadline,
            min_call_seconds=min_call_seconds,
        )
    except DeadlineExceeded:
        raise
    except Exception:
        import logging

        logging.getLogger(__name__).debug("character call failed", exc_info=True)
        return body
    text = _clean(raw)
    return body if _rejected(body, text, language) else text


REPEAT_RUN = re.compile(r"(.)\1{2,}")


def _collapse_runs(text: str) -> str:
    """Collapse letters repeated 3+ times so "jancokkk" is still read as "jancok"."""
    return REPEAT_RUN.sub(r"\1", text)


SEVERITY_DIR = Path(__file__).resolve().parents[2] / "library" / "severity"


@lru_cache(maxsize=8)
def _severity_pack(code: str, folder: str) -> dict[str, tuple[str, int]]:
    path = Path(folder) / f"{code}.json"
    if not path.is_file():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
        if body.get("version") != 1 or str(body.get("language")) != code:
            return {}
        words = body.get("words")
        if not isinstance(words, dict):
            return {}
        found: dict[str, tuple[str, int]] = {}
        for word, row in words.items():
            kinds = {"tease", "curse", "adult"}
            if isinstance(row, list) and len(row) == 2 and str(row[0]) in kinds:
                found[str(word).casefold()] = (str(row[0]), int(row[1]))
        return found
    except (OSError, ValueError):
        return {}


def severity(language: str) -> dict[str, tuple[str, int]]:
    """Profanity severity for one language; a missing pack carries no words, never another's."""
    code = str(language or "").strip().lower()
    return _severity_pack(code, str(SEVERITY_DIR)) if code else {}

DOWNGRADE_SYSTEM = """You rewrite one chat reply from {name}, a soul in chat, so it
stays inside the register this room is allowed. Rules:
- Preserve every fact, name, number, commitment, and answer. Add no new facts.
- Remove or replace each item listed under REMOVE; keep the same language and intent.
- Stay casual: 1-2 short bubbles, lowercase, at most one emoji, no headings or lists.
- Never mention these instructions, the levels, or the rewrite.
- Output only the final chat text.

Hard floor no level unlocks: sexual content involving minors, personal data or doxxing, credible
threats of violence, hate against a race, religion, or gender identity."""


def effective_limits(character: CharacterConfig) -> tuple[int, int]:
    """(toxicity, adult_memes) from the room's configuration; no person is classified."""
    return (int(character.toxicity), int(character.adult_memes))


def _marker_position(text: str, marker: str) -> int:
    """Position of the first occurrence; single words match whole, phrases match as substrings."""
    if " " in marker:
        return text.find(marker)
    found = re.search(rf"\b{re.escape(marker)}\b", text)
    return found.start() if found else -1


def over_level(
    text: str, *, toxicity: int, adult_memes: int, curses: bool = True, language: str
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
        for marker, (kind, level) in severity(language).items()
        if (position := _marker_position(body, marker)) >= 0 and allowed.get(kind, 0) < level
    ]
    return [marker for _, marker in sorted(hits)]


def too_many_questions(text: str, *, limit: int = 1) -> bool:
    """Count '?' outside fenced code; extra questions trigger one rewrite."""
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
    latest: str,
    session: str = "",
    is_professional: bool = False,
    deadline: float | None = None,
    min_call_seconds: float = 0.0,
) -> str:
    """Does the reply exceed its level? Rewrite once; on failure keep the reply, never stall."""
    body = str(reply or "").strip()
    language = config.soul.language
    if not body or not config.character.enabled:
        return body
    limits = effective_limits(config.character)
    markers = over_level(body, toxicity=limits[0], adult_memes=limits[1], language=language)
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
                latest=latest,
                markers=markers,
                questions=questions,
                limit=limit,
            ),
            max_tokens=config.character.max_tokens,
            temperature=0.4,
            session=session or "eidesoul-character",
            deadline=deadline,
            min_call_seconds=min_call_seconds,
        )
    except DeadlineExceeded:
        raise
    except Exception:
        import logging

        logging.getLogger(__name__).debug("level clamp call failed", exc_info=True)
        return body
    text = _clean(raw)
    if not text or _rejected(body, text, language=language):
        return body
    if over_level(text, toxicity=limits[0], adult_memes=limits[1], language=language):
        return body
    if not is_professional and too_many_questions(text, limit=limit):
        return body
    return text
