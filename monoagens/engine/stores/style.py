"""Chat style: statistical aggregates from the archive, never message content.

This profile makes MonoAgens's replies follow the human rhythm in this chat (short, lowercase,
particles, the occasional emoji) without imitating anyone's opinions.
"""


import json
import logging
import re
import threading
import time
from functools import lru_cache

from sqlalchemy import select

from ..db import Database
from ..schema import state as state_table
from ..soul import StyleConfig

log = logging.getLogger(__name__)

MAX_LENGTHS = 200
TOP = 20
MAX_TEXT = 500
EMOJI = re.compile("[\U0001f300-\U0001faff\u2600-\u27bf\u2b00-\u2bff]")
REPEAT = re.compile(r"([a-zA-Z])\1{2,}")
SEED_MIN = 20

# English-only universal rhythm baseline. The archive then learns the chat's own short-token
# and particle habits; no per-language file is needed.
STYLE_SEED: dict[str, object] = {
    "median": 42,
    "p90": 140,
    "lower": 0.65,
    "emoji": 0.15,
    "particles": (),
    "emojis": ("😂", "🙏", "🔥", "👍", "😭", "✨", "😅", "❤️"),
    "laughter": ("haha", "hehe", "lol", "lmao"),
}


@lru_cache(maxsize=8)
def _style_pack(code: str) -> dict:
    """The universal rhythm baseline for any non-empty catalog code."""
    return dict(STYLE_SEED) if code else {}


def style_pack(language: str) -> dict:
    code = str(language or "").strip().lower()
    return _style_pack(code) if code else {}
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


def merge_buckets(left: dict, right: dict) -> dict:
    """Sum two style buckets; `lengths` keeps the newest `MAX_LENGTHS` samples."""
    merged = _empty()
    for key, value in _empty().items():
        if isinstance(value, list):
            merged[key] = [*(left.get(key) or []), *(right.get(key) or [])][-MAX_LENGTHS:]
        elif isinstance(value, dict):
            counts: dict[str, int] = {}
            for source in (left, right):
                for name, count in (source.get(key) or {}).items():
                    counts[str(name)] = counts.get(str(name), 0) + int(count)
            merged[key] = counts
        else:
            merged[key] = int(left.get(key) or 0) + int(right.get(key) or 0)
    return merged


def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(ratio * (len(ordered) - 1))))
    return ordered[index]


class StyleStore:
    """Persistent chat style statistics in `state`; aggregates only, no message content."""

    def __init__(self, db: Database, config: StyleConfig | None = None, *, language: str):
        self.config = config or StyleConfig()
        self.language = str(language or "").strip().lower()
        self._seed = style_pack(self.language)
        self._laugh = (
            re.compile("|".join(re.escape(item) for item in self._seed["laughter"]), re.IGNORECASE)
            if self._seed
            else None
        )
        self._db = db
        self._lock = threading.RLock()
        self._data = self._load()

    # ------------------------------------------------------------------ state

    def _load(self) -> dict:
        data = {"seeded": False, "global": _empty(), "threads": {}, "people": {}}
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
        for scope in ("threads", "people"):
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

    def _count(self, bucket: dict, text: str) -> None:
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
        if self._laugh is not None and self._laugh.search(text):
            bucket["laugh"] += 1
        if REPEAT.search(text):
            bucket["repeat"] += 1
        folded = text.casefold()
        for particle in self._seed.get("particles", ()):
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

    def learn(self, text: str, *, thread_id: str = "", person_id: str = "") -> None:
        """One human message; commands, media placeholders, and long text are skipped."""
        body = str(text or "")
        if not body.strip() or body.startswith("/") or len(body) > MAX_TEXT:
            return
        with self._lock:
            self._count(self._data["global"], body)
            if thread_id:
                bucket = self._data["threads"].setdefault(thread_id, _empty())
                self._count(bucket, body)
            if person_id:
                bucket = self._data["people"].setdefault(person_id, _empty())
                self._count(bucket, body)
            self._flush()

    def people_ids(self) -> list[str]:
        """Every person id that owns a style bucket."""
        with self._lock:
            return sorted(self._data["people"])

    def merge_people(self, old: str, new: str) -> None:
        """Fold one person's bucket into another when two identities become one."""
        source = str(old or "").strip()
        target = str(new or "").strip()
        if not source or not target or source == target:
            return
        with self._lock:
            held = self._data["people"].get(source)
            if held is None:
                return
            existing = self._data["people"].get(target)
            self._data["people"][target] = (
                merge_buckets(existing, held) if existing is not None else held
            )
            del self._data["people"][source]
            self._flush()

    # ------------------------------------------------------------------ profile

    def _bucket(self, thread_id: str = "") -> dict:
        with self._lock:
            if thread_id:
                bucket = self._data["threads"].get(thread_id)
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
            "How this person writes (mimic the rhythm, not the content or opinions):",
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

    def profile(self, thread_id: str = "") -> str:
        """Chat style prompt block; a language without a pack gets none (ADR-0001 §4.2)."""
        if not self._seed:
            return ""
        bucket = self._bucket(thread_id)
        if not bucket:
            median, p90 = self._seed["median"], self._seed["p90"]
            lower = self._seed["lower"]
            particles = list(self._seed["particles"][:6])
            emojis = list(self._seed["emojis"][:3])
        else:
            lengths = [int(value) for value in bucket["lengths"]]
            median = _percentile(lengths, 0.5)
            p90 = _percentile(lengths, 0.9)
            total = max(1, int(bucket["n"]))
            lower = bucket["lower"] / total
            ranked = sorted(bucket["particles"].items(), key=lambda item: -item[1])
            particles = [name for name, _ in ranked[:6]] or list(self._seed["particles"][:6])
            emojis = [
                name for name, _ in sorted(bucket["emojis"].items(), key=lambda item: -item[1])[:3]
            ] or list(self._seed["emojis"][:3])
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
                "threads": {key: int(value["n"]) for key, value in self._data["threads"].items()},
                "people": len(self._data["people"]),
            }
