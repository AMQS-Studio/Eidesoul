"""Character layer: cleans up robotic replies and keeps the register within its limits.

Not a local model and not a fine-tune: one `LLM.complete` call to the existing endpoint, only
when the main draft is detected as robotic or exceeds the allowed harshness level. Facts,
numbers, names, and tool results may not change; when the result looks suspicious, the
original draft is used.
"""


import re
from functools import lru_cache

from ..config import Config
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


def polish_system(name: str) -> str:
    """The polish prompt; persona and examples come from soul.yaml, never a local pack."""
    return POLISH_SYSTEM.format(name=name)


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
    voice = config.soul.voice
    character = voice.character
    if not body or not voice.enabled or not character.enabled:
        return body
    if not needs_polish(body, max_chars=character.max_chars, language=language):
        return body
    if FENCE_START in body:  # code/technical structure is never rewritten
        return body
    try:
        raw = await llm.complete(
            polish_system(config.soul.name),
            polish_payload(body, latest=latest, recent=recent, direct=direct, tone=tone),
            max_tokens=character.max_tokens,
            temperature=character.temperature,
            session=session or "monoagens-character",
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
