"""Chat style: statistical aggregates from the archive, never message content.

This profile makes Eidesoul's replies follow the human rhythm in this chat (short, lowercase,
particles, the occasional emoji) without imitating anyone's opinions.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time

from sqlalchemy import select

from ..config import StyleConfig
from ..db import Database
from ..schema import state as state_table

log = logging.getLogger(__name__)

PLACEHOLDERS = ("[Stiker]", "[Gambar]", "[Dokumen]", "[Video]", "[Audio]")
MAX_LENGTHS = 200
TOP = 20
MAX_TEXT = 500
EMOJI = re.compile("[\U0001f300-\U0001faff\u2600-\u27bf\u2b00-\u2bff]")
LAUGH = re.compile(r"(wkwk|awok|hehe|haha)", re.IGNORECASE)
REPEAT = re.compile(r"([a-zA-Z])\1{2,}")
PARTICLES = (
    "aja", "kok", "sih", "deh", "loh", "banget", "gitu", "gini", "kan", "nih", "tuh",
    "wkwk", "anjir", "jir", "wes", "iki", "iku", "yo", "ya", "eh", "mah", "lah", "dong",
)
EMOJIS = ("😭", "🗿", "🙏", "😹", "😂", "😊", "🥲", "😌", "🙃", "🔥")
SEED = {
    "median": 34,
    "p90": 130,
    "lower": 0.86,
    "emoji": 0.20,
    "particles": ("aja", "kok", "sih", "deh", "loh", "banget", "gitu", "gini", "kan", "nih", "tuh", "wkwk", "anjir", "jir", "wes", "iki", "iku", "yo"),
    "emojis": ("😭", "🗿", "🙏", "😹", "😂", "😊"),
}
SEED_MIN = 20
PERSON_MIN = 8

STATE_KEY = "style"


def _empty() -> dict:
    return {
        "n": 0,
        "lengths": [],
        "lower": 0,
        "upper": 0,
        "emoji": 0,
        "question": 0,
        "exclaim": 0,
        "laugh": 0,
        "repeat": 0,
        "particles": {},
        "emojis": {},
    }


def _merge(target: dict, source: dict) -> dict:
    """Normalise old data shapes so new fields are never lost."""
    for key, value in _empty().items():
        item = source.get(key)
        empty_list = isinstance(value, list) and not isinstance(item, list)
        empty_dict = isinstance(value, dict) and not isinstance(item, dict)
        if item is None or empty_list or empty_dict:
            source[key] = value
    return source


def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(ratio * (len(ordered) - 1))))
    return ordered[index]


class StyleStore:
    """Persistent chat style statistics in `state`; aggregates only, no message content."""

    def __init__(self, db: Database, config: StyleConfig | None = None):
        self.config = config or StyleConfig()
        self._db = db
        self._lock = threading.RLock()
        self._data = self._load()

    # ------------------------------------------------------------------ state

    def _load(self) -> dict:
        data = {"seeded": False, "global": _empty(), "chats": {}, "people": {}}
        try:
            row = self._db.fetch_one(
                select(state_table.c.value).where(state_table.c.key == STATE_KEY)
            )
            raw = json.loads(row["value"]) if row is not None else None
        except Exception:
            log.warning("failed to read the style profile from the database", exc_info=True)
            return data
        if not isinstance(raw, dict):
            return data
        data["seeded"] = bool(raw.get("seeded"))
        data["global"] = _merge(data["global"], raw.get("global") or {})
        for scope in ("chats", "people"):
            items = raw.get(scope)
            if isinstance(items, dict):
                data[scope] = {
                    str(key): _merge(_empty(), value)
                    for key, value in items.items()
                    if isinstance(value, dict)
                }
        return data

    def _flush(self) -> None:
        try:
            with self._db.connect() as conn:
                self._db.upsert(
                    conn,
                    state_table,
                    [
                        {
                            "key": STATE_KEY,
                            "value": json.dumps(self._data, ensure_ascii=False),
                            "updated_at": time.time(),
                        }
                    ],
                    ["key"],
                )
        except Exception:
            log.warning("failed to save the style profile to the database", exc_info=True)

    def close(self) -> None:
        with self._lock:
            self._flush()

    # ------------------------------------------------------------------ learning

    @staticmethod
    def _count(bucket: dict, text: str) -> None:
        letters = [char for char in text if char.isalpha()]
        bucket["n"] += 1
        bucket["lengths"] = [*bucket["lengths"], len(text)][-MAX_LENGTHS:]
        if letters:
            lower = sum(1 for char in letters if char.islower()) / len(letters)
            if lower >= 0.6:
                bucket["lower"] += 1
            elif lower <= 0.4:
                bucket["upper"] += 1
        if EMOJI.search(text):
            bucket["emoji"] += 1
        if text.rstrip().endswith("?"):
            bucket["question"] += 1
        if text.rstrip().endswith("!"):
            bucket["exclaim"] += 1
        if LAUGH.search(text):
            bucket["laugh"] += 1
        if REPEAT.search(text):
            bucket["repeat"] += 1
        folded = text.casefold()
        for particle in PARTICLES:
            hits = len(re.findall(rf"\b{particle}\b", folded))
            if hits:
                bucket["particles"][particle] = bucket["particles"].get(particle, 0) + hits
                if len(bucket["particles"]) > TOP:
                    keep = sorted(bucket["particles"].items(), key=lambda item: -item[1])[:TOP]
                    bucket["particles"] = dict(keep)
        for emoji in EMOJI.findall(text):
            bucket["emojis"][emoji] = bucket["emojis"].get(emoji, 0) + 1
        if len(bucket["emojis"]) > TOP:
            keep = sorted(bucket["emojis"].items(), key=lambda item: -item[1])[:TOP]
            bucket["emojis"] = dict(keep)

    def learn(self, text: str, *, chat_id: str = "", person_id: str = "") -> None:
        """One human message; commands, media placeholders, and long text are skipped."""
        body = str(text or "")
        if not body.strip() or body.startswith("/") or len(body) > MAX_TEXT:
            return
        if any(marker in body for marker in PLACEHOLDERS):
            return
        with self._lock:
            self._count(self._data["global"], body)
            if chat_id:
                bucket = self._data["chats"].setdefault(chat_id, _empty())
                self._count(bucket, body)
            if person_id:
                bucket = self._data["people"].setdefault(person_id, _empty())
                self._count(bucket, body)
            self._flush()

    def seed_from_history(self, history) -> bool:
        """Once: learn human message samples from the archive, then lock `seeded`."""
        with self._lock:
            if self._data.get("seeded"):
                return False
        try:
            rows = history.style_sample(2000) if history is not None else []
        except Exception:
            log.exception("failed to read the style samples from the archive")
            rows = []
        with self._lock:
            for row in rows:
                item = row if isinstance(row, dict) else {"text": row}
                body = str(item.get("text") or "")
                if not body.strip() or body.startswith("/") or len(body) > MAX_TEXT:
                    continue
                if any(marker in body for marker in PLACEHOLDERS):
                    continue
                self._count(self._data["global"], body)
                chat_id = str(item.get("chat_id") or "")
                if chat_id:
                    self._count(self._data["chats"].setdefault(chat_id, _empty()), body)
                person_id = ""
                address = str(item.get("sender_address") or "")
                if address and history is not None:
                    try:
                        person_id = history.resolve_person(address)
                    except Exception:
                        person_id = ""
                person_id = person_id or str(item.get("sender_id") or "")
                if person_id:
                    self._count(self._data["people"].setdefault(person_id, _empty()), body)
            self._data["seeded"] = True
            self._flush()
        return True

    # ------------------------------------------------------------------ profile

    def seed_profile(self) -> dict:
        return dict(SEED)

    def _bucket(self, chat_id: str = "") -> dict:
        with self._lock:
            if chat_id:
                bucket = self._data["chats"].get(chat_id)
                if bucket is not None and bucket["n"] >= SEED_MIN:
                    return bucket
            bucket = self._data["global"]
            return bucket if bucket["n"] >= SEED_MIN else {}

    def _person_bucket(self, person_id: str) -> dict:
        with self._lock:
            bucket = self._data["people"].get(person_id) if person_id else None
        return bucket if bucket is not None and bucket["n"] >= PERSON_MIN else {}

    def person_profile(self, person_id: str) -> str:
        """Prompt block for one person's rhythm; empty when the sample is too small."""
        bucket = self._person_bucket(person_id)
        if not bucket:
            return ""
        lengths = [int(value) for value in bucket["lengths"]]
        median = _percentile(lengths, 0.5)
        p90 = _percentile(lengths, 0.9)
        total = max(1, int(bucket["n"]))
        particles = [
            name for name, _ in sorted(bucket["particles"].items(), key=lambda item: -item[1])[:5]
        ]
        emojis = [
            name for name, _ in sorted(bucket["emojis"].items(), key=lambda item: -item[1])[:3]
        ]
        lines = [
            "How this person chats (mimic the rhythm, not the content or opinions):",
            (
                f"- median {median} characters; 90% below {p90}; "
                f"{round(bucket['lower'] / total * 100)}% lowercase."
            ),
            f"- particles: {', '.join(particles) or '-'}.",
            f"- occasional emoji: {' '.join(emojis) or '-'}.",
            (
                f"- {round(bucket['laugh'] / total * 100)}% of messages contain laughter; "
                f"{round(bucket['question'] / total * 100)}% ask a question."
            ),
        ]
        text = "\n".join(lines)
        limit = max(200, int(self.config.max_chars))
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def profile(self, chat_id: str = "") -> str:
        """Chat style prompt block; empty never happens (the seed is the minimal baseline)."""
        bucket = self._bucket(chat_id)
        if not bucket:
            median, p90 = SEED["median"], SEED["p90"]
            lower = SEED["lower"]
            particles = list(SEED["particles"][:6])
            emojis = list(SEED["emojis"][:3])
        else:
            lengths = [int(value) for value in bucket["lengths"]]
            median = _percentile(lengths, 0.5)
            p90 = _percentile(lengths, 0.9)
            total = max(1, int(bucket["n"]))
            lower = bucket["lower"] / total
            particles = [
                name for name, _ in sorted(bucket["particles"].items(), key=lambda item: -item[1])[:6]
            ] or list(SEED["particles"][:6])
            emojis = [
                name for name, _ in sorted(bucket["emojis"].items(), key=lambda item: -item[1])[:3]
            ] or list(SEED["emojis"][:3])
        lines = [
            "How people chat here (mimic rhythm and diction, not content or opinions):",
            f"- median {median} characters; 90% below {p90}. Reply in 1-2 short sentences.",
            (
                f"- {round(lower * 100)}% lowercase; capitals/repeated letters only for a "
                "shocked or sulky tone."
            ),
            f"- particles: {', '.join(particles)}.",
            f"- occasional emoji: {' '.join(emojis)} (max 1).",
            "- often split into 1-3 messages; never send one long block.",
        ]
        text = "\n".join(lines)
        limit = max(200, int(self.config.max_chars))
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def stats(self) -> dict:
        with self._lock:
            return {
                "seeded": bool(self._data.get("seeded")),
                "global": int(self._data["global"]["n"]),
                "chats": {key: int(value["n"]) for key, value in self._data["chats"].items()},
                "people": len(self._data["people"]),
            }
