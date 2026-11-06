"""Room vibe: anima-style attack/hold/decay dynamics scored against emotion prototypes.

Not a replacement for `affect`: vibe is the room mood that keeps moving from the text, stored
per chat in the `state` table, and used as prompt context. Labels come from multilingual
prototype sentences on the shared embedder, so there is no second model to load. Every
failure fails open.
"""


import asyncio
import contextlib
import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from .. import embed, io
from ..db import Database
from ..schema import state as state_table
from ..soul import VibeConfig

log = logging.getLogger(__name__)

NEUTRAL = (0.5, 0.4, 0.5)
CURRENT_TAU = (120.0, 45.0, 90.0)  # V, A, D seconds
BASELINE_TAU = 14400.0  # 4 hours
# Calibrated on the shipped bank: neutral/functional sentences top out near 0.61, while an
# emotional paraphrase clears 0.70. The margin compares the two valence groups, so `anger` and
# `disgust` may co-occur without cancelling each other.
PROTOTYPE_FLOOR = 0.58
PROTOTYPE_MARGIN = 0.063
PROTOTYPE_BASE = 0.45  # an unrelated prototype still scores about this much on MiniLM
POSITIVE = frozenset({"joy", "trust", "anticipation"})
NEGATIVE = frozenset({"anger", "disgust", "fear", "sadness"})
EMOTION_VAD = {
    "anger": (0.15, 0.85, 0.80),
    "anticipation": (0.60, 0.60, 0.55),
    "disgust": (0.10, 0.55, 0.60),
    "fear": (0.20, 0.85, 0.25),
    "joy": (0.90, 0.65, 0.65),
    "sadness": (0.15, 0.30, 0.25),
    "surprise": (0.55, 0.85, 0.45),
    "trust": (0.78, 0.40, 0.60),
}

# English-only semantic anchors. The shared multilingual embedder maps every language into the
# same vector space, so one canonical bank scores all languages without per-language files.
DEFAULT_BANK: dict[str, tuple[str, ...]] = {
    "anger": (
        "you are being ridiculous",
        "that makes me furious",
        "stop making me angry",
        "i am so done with this",
        "that really annoys me",
        "don't do that again",
    ),
    "anticipation": (
        "let's start tomorrow",
        "waiting for your news",
        "i can't wait to see you",
        "we meet later today",
        "hope this turns out well",
        "counting the days",
    ),
    "disgust": (
        "that is disgusting",
        "that is gross honestly",
        "that looks terrible frankly",
        "i can't stand that smell",
        "no thanks, that is nasty",
        "eew, keep it away",
    ),
    "fear": (
        "i'm scared of failing",
        "don't leave",
        "my hands are shaking",
        "i am afraid of this",
        "this feels dangerous honestly",
        "please don't be angry",
    ),
    "joy": (
        "haha that's great",
        "we finally made it",
        "so happy right now",
        "this made my day",
        "that was really funny",
        "yes, it worked",
    ),
    "sadness": (
        "i'm exhausted",
        "that's sad",
        "i want to cry",
        "this hurts a lot",
        "why does it always end",
        "i feel so heavy",
    ),
    "surprise": (
        "wait, really?",
        "no way",
        "oh i did not know",
        "that came out of nowhere",
        "wow, unexpected",
        "huh, is that true",
    ),
    "trust": (
        "thanks, i trust you",
        "it's fine",
        "i feel safe with you",
        "no need to worry",
        "you are reliable",
        "relax, i am here",
    ),
}

STATE_KEY = "vibe"
SAVE_ATTEMPTS = 3
SAVE_BACKOFF_SECONDS = 0.1  # 0.1, 0.2, 0.4 on write contention; never a busy loop


def load_bank(folder: Path | None = None) -> dict[str, tuple[str, ...]]:
    """English default bank, or merged prototypes from an explicit test directory."""
    if folder is None or not Path(folder).is_dir():
        return dict(DEFAULT_BANK)
    found: dict[str, list[str]] = {}
    dropped: list[str] = []
    for target in sorted(Path(folder).glob("*.json")):
        try:
            body = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("vibe: unreadable prototype file %s", target.name)
            continue
        rows = body.get("prototypes") if isinstance(body, dict) else None
        if not isinstance(rows, dict):
            continue
        for label, entries in rows.items():
            name = str(label).strip().lower()
            if name not in EMOTION_VAD:
                dropped.append(f"{target.name}:{name}")
                continue
            for entry in entries if isinstance(entries, list) else ():
                text = " ".join(str(entry or "").split())
                if len(text) < 6:
                    dropped.append(f"{target.name}:{name}:{text!r}")
                    continue
                found.setdefault(name, []).append(text)
    if dropped:
        log.warning(
            "vibe: dropped %d prototype(s) from %s: %s",
            len(dropped),
            Path(folder).name,
            ", ".join(dropped[:5]),
        )
    return {label: tuple(rows) for label, rows in found.items()}


class PrototypeSource:
    """Emotion label -> the max cosine over that label's prototypes on the shared embedder."""

    def __init__(self, bank: dict[str, tuple[str, ...]] | None = None) -> None:
        self.bank = load_bank() if bank is None else dict(bank)
        self._vectors: dict[str, list[list[float]]] = {}

    def _embed_bank(self) -> dict[str, list[list[float]]]:
        labels = [label for label, rows in self.bank.items() if rows]
        texts = [text for label in labels for text in self.bank[label]]
        if not texts:
            return {}
        found = embed.encode(texts)
        if len(found) != len(texts):
            return {}
        grouped: dict[str, list[list[float]]] = {}
        index = 0
        for label in labels:
            size = len(self.bank[label])
            grouped[label] = found[index : index + size]
            index += size
        return grouped

    def scores(self, text: str) -> dict[str, float]:
        """Label -> similarity; `{}` when the bank or the embedder is unavailable."""
        body = str(text or "").strip()
        if not body:
            return {}
        if not self._vectors:
            self._vectors = self._embed_bank()
        if not self._vectors:
            return {}
        query = embed.encode([body])
        if not query:
            return {}
        return {
            label: max(embed.cosine(query[0], vector) for vector in group)
            for label, group in self._vectors.items()
        }


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass
class VibeState:
    """The current mood and the slow-moving baseline mood."""

    current: list[float] = field(default_factory=lambda: list(NEUTRAL))
    baseline: list[float] = field(default_factory=lambda: list(NEUTRAL))
    hold_until: float = 0.0
    last_tick: float = 0.0


def advance(state: VibeState, now: float, config: VibeConfig) -> None:
    """One tick: current drifts back to baseline, baseline drifts back to neutral."""
    dt = max(0.0, now - state.last_tick) if state.last_tick > 0 else 0.0
    state.last_tick = now
    if dt <= 0 or now < state.hold_until:
        return
    for index in range(3):
        factor = 1.0 - math.exp(-dt / CURRENT_TAU[index])
        state.current[index] += (state.baseline[index] - state.current[index]) * factor
    factor = 1.0 - math.exp(-dt / BASELINE_TAU)
    for index in range(3):
        state.baseline[index] += (NEUTRAL[index] - state.baseline[index]) * factor
    state.current = [_clamp(value) for value in state.current]
    state.baseline = [_clamp(value) for value in state.baseline]


class VibeRunner:
    """Prototype scorer + per-chat vibe dynamics; a heartbeat keeps the decay running."""

    def __init__(self, db: Database, config: VibeConfig, classifier=None):
        self.config = config
        self._db = db
        self._classifier = classifier
        self._source = PrototypeSource()
        self._states: dict[str, VibeState] = self._load()
        self._lock = asyncio.Lock()
        self._running = False
        self._task: asyncio.Task | None = None
        self._failed = False

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> bool:
        """True when vibe is ready; False means disabled (fail-open, no exception)."""
        if self._running:
            return True
        if self._classifier is None:
            # ADR-0017 §4.1: the embedder is a synchronous model call.
            ready = await io.run(self._source.scores, "ok")
            if not ready:
                log.warning("vibe: embedder unavailable; vibe disabled")
                self._failed = True
                return False
        self._running = True
        self._task = asyncio.create_task(self._heartbeat())
        return True

    async def close(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await io.run(self._save)

    async def _heartbeat(self) -> None:
        interval = max(0.2, float(self.config.tick_seconds))
        while self._running:
            await asyncio.sleep(interval)
            async with self._lock:
                now = time.time()
                for state in self._states.values():
                    advance(state, now, self.config)

    # ------------------------------------------------------------------ per chat

    @staticmethod
    def vad(probs: dict | None) -> list[float] | None:
        """Label scores -> one VAD point; a weak or ambiguous top label counts as neutral."""
        if not probs:
            return None
        scores = {
            str(label).strip().lower(): float(value)
            for label, value in probs.items()
            if str(label).strip().lower() in EMOTION_VAD
        }
        if not scores:
            return None
        if max(scores.values()) < PROTOTYPE_FLOOR:
            return None
        # A margin between the valence groups, not between the global top two: `anger` and
        # `disgust` (or `joy` and `trust`) agreeing is a stronger signal, not a tie.
        positive = max((score for label, score in scores.items() if label in POSITIVE), default=0.0)
        negative = max((score for label, score in scores.items() if label in NEGATIVE), default=0.0)
        if abs(positive - negative) < PROTOTYPE_MARGIN:
            return None
        # Every prototype keeps a ~0.45 baseline cosine, so the raw average is compressed into
        # neutral. Only the mass above that baseline carries the emotion; renormalize it.
        weights = {label: max(0.0, score - PROTOTYPE_BASE) for label, score in scores.items()}
        total = sum(weights.values())
        if total <= 0:
            return None
        acc = [0.0, 0.0, 0.0]
        for label, weight in weights.items():
            point = EMOTION_VAD[label]
            for index in range(3):
                acc[index] += weight * point[index]
        return [_clamp(value / total) for value in acc]

    async def observe(self, thread_id: str, text: str) -> str:
        """One message affects this chat's vibe; returns a ready-to-use prompt block."""
        if not str(text or "").strip() or not self._running or self._failed:
            return ""
        try:
            if self._classifier is not None:
                probs = self._classifier(text)
            else:
                # ADR-0017 §4.1: prototype scoring embeds on a worker, never on the loop.
                probs = await io.run(self._source.scores, text)
            target = self.vad(probs)
            if target is None:
                return await self.describe(thread_id)
            async with self._lock:
                state = self._state(thread_id)
                now = time.time()
                advance(state, now, self.config)
                for index in range(3):
                    state.current[index] += (
                        target[index] - state.current[index]
                    ) * float(self.config.attack)
                for index in range(3):
                    state.baseline[index] += (
                        state.current[index] - state.baseline[index]
                    ) * float(self.config.baseline_influence)
                state.current = [_clamp(value) for value in state.current]
                state.baseline = [_clamp(value) for value in state.baseline]
                state.hold_until = now + float(self.config.hold_seconds)
                return self._describe(state)
        except Exception:
            log.warning("vibe: failed to process chat=%s", thread_id)
            log.debug("vibe: failure detail", exc_info=True)
            self._failed = True
            return ""

    async def describe(self, thread_id: str) -> str:
        async with self._lock:
            return self._describe(self._state(thread_id))

    def _describe(self, state: VibeState) -> str:
        # The band comes from `current` (fast mood), not `baseline`, which needs hours to move.
        v, a, d = state.current
        mood = "negative" if v < 0.35 else ("neutral" if v < 0.6 else "positive")
        energy = "calm" if a < 0.35 else ("medium" if a < 0.65 else "energetic")
        power = "pressured" if d < 0.4 else ("balanced" if d < 0.65 else "confident")
        return (
            f"Room vibe: {mood}, {energy}, {power}. "
            "This is context, not permission to attack; never cite numbers or technical terms."
        )

    # ------------------------------------------------------------------ persistence

    def _state(self, thread_id: str) -> VibeState:
        state = self._states.get(thread_id)
        if state is None:
            state = VibeState()
            self._states[thread_id] = state
        return state

    def _load(self) -> dict[str, VibeState]:
        try:
            row = self._db.fetch_one(
                select(state_table.c.value).where(state_table.c.key == STATE_KEY)
            )
            raw = json.loads(row["value"]) if row is not None else None
        except Exception:
            log.warning("vibe: failed to load state from the database", exc_info=True)
            return {}
        if not isinstance(raw, dict):
            return {}
        states: dict[str, VibeState] = {}
        for thread_id, body in raw.items():
            if not isinstance(body, dict):
                continue
            current = self._triple(body.get("current"))
            baseline = self._triple(body.get("baseline"))
            if current is None or baseline is None:
                continue
            states[str(thread_id)] = VibeState(current=current, baseline=baseline)
        return states

    @staticmethod
    def _triple(value) -> list[float] | None:
        if not isinstance(value, list) or len(value) != 3:
            return None
        try:
            return [_clamp(float(item)) for item in value]
        except (TypeError, ValueError):
            return None

    def _save(self) -> None:
        body = {
            thread_id: {"current": state.current, "baseline": state.baseline}
            for thread_id, state in self._states.items()
        }
        row = {
            "key": STATE_KEY,
            "value": json.dumps(body),
            "updated_at": time.time(),
        }
        for attempt in range(1, SAVE_ATTEMPTS + 1):
            try:
                with self._db.connect() as conn:
                    self._db.upsert(conn, state_table, [row], ["key"])
                return
            except Exception:
                if attempt >= SAVE_ATTEMPTS:
                    log.warning("vibe: failed to save state to the database", exc_info=True)
                    return
                # Contention on the shared `state` row backs off exponentially, never spins.
                delay = SAVE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                log.debug(
                    "vibe: save contention, retrying in %.2fs (attempt %d/%d)",
                    delay,
                    attempt,
                    SAVE_ATTEMPTS,
                )
                time.sleep(delay)
