"""Target gender from how people type, with the avatar only as a fallback.

No face classification: the main signal is the language markers in the last messages of the
sender themselves (the bot does not take part in the vote). The thresholds are deliberately
conservative --- on doubt the result is `unknown`, and an `unknown` target never unlocks
harshness. The avatar is only read when the writing style gives no answer and the host
enables it.
"""


import re

MALE_MARKERS = (
    "gue",
    "gua",
    "gw",
    "bro",
    "bray",
    "cuy",
    "woi",
    "jir",
    "anjir",
    "njir",
    "goblok",
    "bangsat",
    "kontol",
    "bgst",
    "bang",
)
FEMALE_MARKERS = (
    "akuu",
    "gemes",
    "gemoy",
    "kak",
    "kaka",
    "cantiq",
    "bestie",
    "girl",
    "deh",
    "sihh",
    "kawaii",
    "😝",
    "🥺",
    "💅",
    "🙈",
)
STYLE_WINDOW = 8
STYLE_MIN_HITS = 3
STYLE_MARGIN = 0.6
GENDERS = ("male", "female", "unknown", "")

GENDER_WORDS = {
    "male": "male",
    "m": "male",
    "pria": "male",
    "cowok": "male",
    "laki": "male",
    "laki-laki": "male",
    "lk": "male",
    "female": "female",
    "f": "female",
    "wanita": "female",
    "cewek": "female",
    "perempuan": "female",
    "pr": "female",
    "unknown": "unknown",
    "?": "unknown",
    "netral": "unknown",
    "none": "unknown",
}


def _marker_count(text: str, marker: str) -> int:
    """Whole words for word markers (so 'bang' skips 'banget'), substrings for emoji."""
    if marker.isalpha():
        return len(re.findall(rf"\b{re.escape(marker)}\b", text))
    return text.count(marker)


def score_style(messages: list[str]) -> tuple[str, float]:
    """(label, confidence) from the sender's message window; doubt = ("unknown", 0.0)."""
    window = [str(item or "").lower()[-400:] for item in messages if str(item or "").strip()]
    body = "\n".join(window[-STYLE_WINDOW:])
    if not body:
        return ("unknown", 0.0)
    male = sum(_marker_count(body, marker.lower()) for marker in MALE_MARKERS)
    female = sum(_marker_count(body, marker.lower()) for marker in FEMALE_MARKERS)
    margin = (male - female) / max(1, male + female)
    if male >= STYLE_MIN_HITS and margin >= STYLE_MARGIN:
        return ("male", round(abs(margin), 2))
    if female >= STYLE_MIN_HITS and -margin >= STYLE_MARGIN:
        return ("female", round(abs(margin), 2))
    return ("unknown", 0.0)


def combine(
    style: tuple[str, float],
    avatar: tuple[str, float] | None,
    *,
    gate: bool = True,
) -> tuple[str, float, str]:
    """Style wins; the avatar only when the style is unsure. Result: (label, confidence, source)."""
    label, confidence = style
    if label in ("male", "female"):
        return (label, float(confidence), "style")
    if gate and avatar is not None and avatar[0] in ("male", "female"):
        return (avatar[0], float(avatar[1]), "avatar")
    return ("unknown", float(confidence), "none")


def parse_gender(value: str) -> str | None:
    """/gender argument -> canonical label; None when it is not a known label."""
    key = " ".join(str(value or "").strip().lower().split())
    return GENDER_WORDS.get(key)


AVATAR_PROMPT = """You look at one profile picture from {app} and label the
person's apparent gender: male, female, or unknown.
Rules:
- Answer with one word only: male, female, or unknown.
- An unknown face, a cartoon, a group logo, or anything ambiguous is unknown.
- Never use names, clothes alone, or a guess you cannot see. Unknown is a fine answer."""


async def classify(llm, raw: bytes, mime: str, *, locale: str = "id-ID") -> tuple[str, float] | None:
    """Avatar -> (label, 0.6) or None; a fallback signal only, failure means unknown."""
    import base64

    if llm is None or not raw or not mime:
        return None
    app = "an Indonesian chat app" if str(locale or "").startswith("id") else "a chat app"
    payload = [
        {"type": "text", "text": "Label this profile picture."},
        {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"},
        },
    ]
    try:
        answer = await llm.complete(
            AVATAR_PROMPT.format(app=app),
            payload,
            max_tokens=8,
            temperature=0.0,
            session="eidesoul-avatar",
        )
    except (OSError, RuntimeError, ValueError):  # endpoint without vision: stay unknown
        return None
    label = str(answer or "").strip().lower().split()
    if not label:
        return None
    found = label[0].strip(".,!:;")
    return (found, 0.6) if found in ("male", "female") else None
