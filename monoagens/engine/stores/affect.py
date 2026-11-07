"""Persistent affect: a self-contained PAD heart, relationships, topics, and drives.

One `emotion` document in the `state` table holds the heart shared by every chat: a
Pleasure-Arousal-Dominance mood, its personality baseline, an affective pulse, and a short log.
Mood drifts back to the baseline with elapsed time, an appraisal moves PAD, and the drives grow
while nobody talks. Relationships live in `people`/`thread_people`, topics in
`topics`/`thread_eras`, and MonoAgens's own drives in `drives`. The caller catches database
failures; this store never kills the bot.
"""


import json
import logging
import time
from datetime import UTC, datetime

from sqlalchemy import func, select

from .. import clock, guard
from ..db import Database
from ..jobs.lease import FencedStore, fenced_insert
from ..schema import drives, people, state, thread_eras, thread_people, threads, topics
from ..soul import AffectConfig

log = logging.getLogger(__name__)

CHARACTER = "soul"
STATE_KEY = "emotion"
NOTES_KEEP, NOTES_TEXT = 4, 120
PROMPT_CHARS, TOPIC_CHARS, ERA_CHARS = 1200, 400, 700
ERA_PREFIX = "Sebelumnya: "
LOG_LINES, LOG_LIMIT, EVENTS_KEEP = 3, 200, 512
PULSE_MINUTES, PULSE_MAX_HOURS = 5.0, 24.0
WINDOW_SECONDS, WINDOW_LIMIT = 600.0, 0.20
DECAY_HALF_LIFE_HOURS = 6.0
PAD_WEIGHT = {"me": 1.0, "group": 0.6, "other": 0.2, "unknown": 0.2}
FAMILIARITY_FULL = 200.0
# documented familiarity thresholds: 5 / 15 / 40 / 80 interactions
STAGE_ACQUAINTANCE, STAGE_FRIEND = 0.025, 0.075
STAGE_CLOSE, STAGE_INTIMATE = 0.20, 0.40
PAD_BOUNDS = {"pleasure": (-1.0, 1.0), "arousal": (0.0, 1.0), "dominance": (0.0, 1.0)}
# profile -> (baseline pull per turn, pulse multiplier, pulse retention)
VOLATILITY = {"steady": (0.08, 1.0, 0.18), "expressive": (0.05, 1.55, 0.28),
              "dramatic_test": (0.03, 2.1, 0.35)}
PERSON_DEFAULTS = {"name": "", "aliases": [], "stage": "stranger",
                   "trust": 0.1, "affection": 0.0, "respect": 0.5, "annoyance": 0.0,
                   "familiarity": 0.0, "interactions": 0, "last_seen": 0.0,
                   "window_start": 0.0, "window_trust": 0.0, "window_affection": 0.0,
                   "window_respect": 0.0, "window_annoyance": 0.0, "notes": []}
DRIVE_DEFAULTS = {"energy": 0.6, "boredom": 0.2, "loneliness": 0.0, "last_pulse": 0.0}
TOPIC_EMPTY = {"summary": "", "keywords": [], "unresolved": [], "pending": 0,
               "last_message": 0.0, "last_digest": 0.0, "last_research": 0.0,
               "research_day": "", "research_count": 0}
PULSE_ZERO = {"P": 0.0, "A": 0.0, "D": 0.0, "intensity": 0.0, "label": "none",
              "source": "default", "created_at": None}
STATE_DEFAULTS = {"emotion": {"pleasure": 0.0, "arousal": 0.3, "dominance": 0.5},
                  "personality_baseline": {"pleasure": 0.0, "arousal": 0.3, "dominance": 0.5},
                  "affective_pulse": PULSE_ZERO, "trust": 0.1, "total_turns": 0,
                  "emotion_log": [], "processed_event_ids": []}


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


def _dim(dimension, value):
    low, high = PAD_BOUNDS[dimension]
    return max(low, min(high, float(value)))


def _inertia(delta, current):
    """The result shrinks as the value gets extreme: annoyance does not grow linearly."""
    return delta * (1.0 - current) if delta >= 0 else delta * current


def _pulse_from_delta(multiplier, label, delta):
    values = {key: round(max(-0.45, min(0.45, float(delta.get(key) or 0.0) * multiplier)), 4)
              for key in ("P", "A", "D")}
    intensity = min(1.0, sum(abs(value) for value in values.values()) / 0.6)
    return {**values, "intensity": round(intensity, 4), "label": label or "turn",
            "source": "turn", "created_at": _iso(time.time()) if intensity > 0.005 else None}


def _zero_pulse(source="default"):
    return {**PULSE_ZERO, "source": source}


def _iso(moment):
    return datetime.fromtimestamp(float(moment), UTC).isoformat(timespec="seconds")


def _stamp(value):
    try:
        return datetime.fromisoformat(str(value)).timestamp() if value else 0.0
    except ValueError:
        return 0.0


def _ago(stamp, moment):
    """Coarse time distance for the prompt; raw numbers are never shown."""
    age = float(stamp or 0.0)
    if age <= 0:
        return "unknown"
    delta = max(0.0, moment - age)
    if delta < 3600:
        return "just now"
    return f"{int(delta // 3600)}h ago" if delta < 86400 else f"{int(delta // 86400)}d ago"


def _as_list(value):
    """The JSON column is already a list; older JSON strings are still accepted."""
    if isinstance(value, list):
        return [item for item in value]
    if isinstance(value, str) and value.startswith("["):
        try:
            loaded = json.loads(value)
        except ValueError:
            return []
        return loaded if isinstance(loaded, list) else []
    return []


def _clean(items, cap):
    return [str(item).strip() for item in items if str(item).strip()][:cap]


def stage_for(interactions, trust=0.0, affection=0.0):
    """The documented familiarity ladder; intimacy also needs trust and affection."""
    familiarity = min(1.0, max(0, int(interactions)) / FAMILIARITY_FULL)
    if familiarity >= STAGE_INTIMATE and trust >= 0.5 and affection >= 0.7:
        return "intimate"
    ladders = ((STAGE_CLOSE, "close"), (STAGE_FRIEND, "friend"),
               (STAGE_ACQUAINTANCE, "acquaintance"))
    return next((name for limit, name in ladders if familiarity >= limit), "stranger")


def _recent_log(state_data, limit=LOG_LINES):
    """Log entries with a real trigger: internal decay is not "what you last felt"."""
    internal = {"pre_turn_decay", "time_decay", "session_start", "session_end", "configure"}
    entries = [item for item in state_data.get("emotion_log", []) if isinstance(item, dict)
               and str(item.get("event_type") or "turn") not in internal]
    return entries[-limit:]


def _relation(affection, annoyance, trust, stage=""):
    """Relationship label for the prompt, honouring the archive stage for old people."""
    labels = (("enemy", annoyance >= 0.75), ("love", affection >= 0.85 and trust >= 0.50),
              ("crush", affection >= 0.70 and trust >= 0.40),
              ("close", affection >= 0.55 or stage == "intimate"),
              ("friend", affection >= 0.30 or trust >= 0.40 or stage in {"friend", "close"}),
              ("acquaintance", trust >= 0.15 or stage == "acquaintance"))
    return next((name for name, hit in labels if hit), "new")


def _tier(trust):
    bands = ((0.2, "New"), (0.4, "Acquaintance"), (0.6, "Familiar"), (0.8, "Close"))
    return next((name for limit, name in bands if trust < limit), "Intimate")


def _summary(state_data):
    emotion = state_data["emotion"]
    return ", ".join((
        "warm" if emotion["pleasure"] >= 0.25 else "guarded" if emotion["pleasure"] <= -0.2
        else "even",
        "energized" if emotion["arousal"] >= 0.6 else "calm" if emotion["arousal"] <= 0.25
        else "steady",
        "firm" if emotion["dominance"] >= 0.65 else "soft" if emotion["dominance"] <= 0.35
        else "balanced"))


def _pulse_text(state_data):
    pulse = state_data.get("affective_pulse") or {}
    intensity = float(pulse.get("intensity") or 0.0)
    strength = next((name for limit, name in
                     ((0.03, "quiet"), (0.18, "subtle"), (0.35, "noticeable"))
                     if intensity <= limit), "strong")
    return f"{strength} {pulse.get('label') or 'none'} pulse"


def _event_done(state_data, event_id):
    return bool(event_id) and str(event_id) in state_data.get("processed_event_ids", [])


def _mark_event(state_data, event_id):
    if event_id and not _event_done(state_data, event_id):
        events = [*(state_data.get("processed_event_ids") or []), str(event_id)]
        state_data["processed_event_ids"] = events[-EVENTS_KEEP:]


class AffectStore(FencedStore):
    """PAD mood plus per-person relationships, topics, and drives for one heart."""

    def __init__(self, db: Database, config: AffectConfig | None = None, soul: str = ""):
        self.db = db
        self.config = config or AffectConfig()
        self.soul = str(soul or "").strip()

    def close(self) -> None:
        return None

    def _upsert(self, table, row, keys, names):
        """One fenced upsert: a deposed instance's heartbeat refuses the row (ADR-0002)."""
        stmt = fenced_insert(table, row, self.fence_clause())
        return stmt.on_conflict_do_update(
            index_elements=keys, set_={name: getattr(stmt.excluded, name) for name in names})

    def _add(self, conn, table, row):
        """One fenced insert for the paths that never conflict (a new era, a new thread)."""
        return conn.execute(fenced_insert(table, row, self.fence_clause()))

    def _update(self, statement):
        fence = self.fence_clause()
        return statement if fence is None else statement.where(fence)

    def _write(self, table, row, keys, names):
        with self.db.connect() as conn:
            conn.execute(self._upsert(table, row, keys, names))

    # ------------------------------------------------------------------ state document

    def _profile(self):
        name = str(self.config.volatility or "expressive").strip().lower()
        return name if name in VOLATILITY else "expressive"

    def _baseline(self):
        text = self.soul.casefold()
        return {"pleasure": 0.10 if any(w in text for w in ("warm", "kind", "gentle")) else 0.0,
                "arousal": 0.25 if any(w in text for w in ("calm", "steady", "quiet")) else 0.30,
                "dominance": 0.60 if any(w in text for w in ("firm", "boundar")) else 0.50}

    def _normalize(self, state_data, *, fresh=False):
        """Keep an old document usable without losing the keys this deployment already wrote."""
        for key, value in STATE_DEFAULTS.items():
            state_data.setdefault(key, value.copy() if isinstance(value, (dict, list)) else value)
        state_data.setdefault("identity", {"state_id": f"soul-{int(time.time())}",
                                           "character_id": CHARACTER,
                                           "relationship_id": CHARACTER, "status": "bound"})
        state_data.setdefault("volatility_profile", self._profile())
        state_data.setdefault("decayed_at", _stamp(state_data.get("last_interaction_iso"))
                              or time.time())
        if fresh:
            state_data.update(emotion=self._baseline(),
                              personality_baseline=self._baseline(),
                              last_interaction_iso=_iso(time.time()))
        for key in ("emotion", "personality_baseline"):
            if not isinstance(state_data.get(key), dict):
                state_data[key] = dict(STATE_DEFAULTS[key])
        state_data["emotion"] = {dim: _dim(dim, state_data["emotion"].get(dim, 0.0))
                                 for dim in PAD_BOUNDS}
        state_data["personality_baseline"] = {
            dim: _dim(dim, state_data["personality_baseline"].get(
                dim, STATE_DEFAULTS["personality_baseline"][dim])) for dim in PAD_BOUNDS}
        return state_data

    def _load_state(self, conn, *, for_update=False):
        """Read the single heart (the `emotion` document); defaults only apply when unbound."""
        statement = select(state.c.value).where(state.c.key == STATE_KEY)
        row = conn.execute(statement.with_for_update() if for_update else statement).first()
        try:
            raw = json.loads(row[0]) if row is not None and row[0] else None
        except ValueError:
            raw = None
        if isinstance(raw, dict) and raw.get("identity", {}).get("status") == "bound":
            return self._normalize(raw)
        fresh = self._normalize({}, fresh=True)
        self._save_state(conn, fresh)
        return fresh

    def _save_state(self, conn, data):
        row = {"key": STATE_KEY, "value": json.dumps(data), "updated_at": time.time()}
        conn.execute(self._upsert(state, row, ["key"], ("value", "updated_at")))

    def _decay(self, state_data, moment):
        """PAD drifts toward the personality baseline as time passes; the pulse fades too."""
        last = float(state_data.get("decayed_at") or 0.0) or _stamp(
            state_data.get("last_interaction_iso"))
        hours = max(0.0, moment - last) / 3600.0 if last else 0.0
        pull_floor, _, retention = VOLATILITY[self._profile()]
        pull = min(0.95, max(pull_floor, 1.0 - 0.5 ** (hours / DECAY_HALF_LIFE_HOURS)))
        baseline, emotion = state_data["personality_baseline"], state_data["emotion"]
        state_data["emotion"] = {dim: _dim(dim, float(emotion[dim]) * (1 - pull)
                                           + float(baseline[dim]) * pull) for dim in PAD_BOUNDS}
        pulse = state_data.get("affective_pulse") or {}
        faded = {key: round(float(pulse.get(key) or 0.0)
                            * retention ** max(1.0, min(8.0, hours * 2.0)), 4)
                 for key in ("P", "A", "D")}
        intensity = min(1.0, sum(abs(value) for value in faded.values()) / 0.6)
        state_data["affective_pulse"] = _zero_pulse("decay") if intensity <= 0.015 else {
            **faded, "intensity": round(intensity, 4), "source": "decay",
            "label": str(pulse.get("label") or "none"), "created_at": pulse.get("created_at")}
        state_data["decayed_at"] = moment

    def _log(self, state_data, event_type, **fields):
        entry = {"timestamp": _iso(time.time()), "event_type": event_type,
                 "trust": round(float(state_data.get("trust") or 0.1), 4)}
        entry.update({key: value if isinstance(value, dict)
                      else " ".join(str(value).split())[:240]
                      for key, value in fields.items() if value})
        state_data["emotion_log"] = [*(state_data.get("emotion_log") or []), entry][-LOG_LIMIT:]

    # ------------------------------------------------------------------ turn

    def turn_start(self, thread_id: str, message_id: str) -> dict:
        """Apply one decay step before the LLM decision is made."""
        moment, event_id = time.time(), f"decay:{message_id}"
        with self.db.connect() as conn:
            state_data = self._load_state(conn, for_update=True)
            if not _event_done(state_data, event_id):
                self._decay(state_data, moment)
                _mark_event(state_data, event_id)
                self._save_state(conn, state_data)
            emotion = state_data["emotion"]
            return {"summary": _summary(state_data), "pulse": _pulse_text(state_data),
                    "pleasure": float(emotion["pleasure"]),
                    "arousal": float(emotion["arousal"]),
                    "dominance": float(emotion["dominance"]),
                    "trust_tier": _tier(float(state_data.get("trust") or 0.0)),
                    "log": [{**item, "other_chat": str(item.get("thread_id") or "")
                             not in ("", thread_id)} for item in _recent_log(state_data)]}

    def turn_end(self, thread_id: str, person_id: str, name: str, text: str, role: str,
                 sender_address: str, decision, message_id: str) -> None:
        """One message has been judged: PAD moves, relationships and topic are recorded too."""
        moment = time.time()
        with self.db.connect() as conn:
            states = self._load_state(conn, for_update=True)
            if _event_done(states, message_id):
                return  # the same event must not move the emotion twice
            addressed = act = ""
            if decision is not None:
                addressed = str(getattr(decision, "addressed", "") or "")
                act = str(getattr(decision, "act", "") or "")
                weight = PAD_WEIGHT.get(addressed, 0.2)
                weight = min(weight, 0.2) if act == "ignore" else weight
                appraisal, before = decision.appraisal, states["emotion"]
                states["emotion"] = {dim: _dim(dim, float(before[dim])
                                               + float(getattr(appraisal, key)) * weight)
                                     for dim, key in (("pleasure", "dP"), ("arousal", "dA"),
                                                      ("dominance", "dD"))}
                delta = {short: round(float(states["emotion"][dim]) - float(before[dim]), 4)
                         for short, dim in (("P", "pleasure"), ("A", "arousal"),
                                            ("D", "dominance"))}
                states["affective_pulse"] = _pulse_from_delta(
                    VOLATILITY[self._profile()][1],
                    str(getattr(appraisal, "label", "") or "neutral"), delta)
                states["total_turns"] = int(states.get("total_turns") or 0) + 1
                states["last_interaction_iso"] = _iso(moment)
                self._log(states, "turn", situation=str(text or "")[:240] or "(no text)",
                          appraisal=str(getattr(appraisal, "label", "") or "neutral"),
                          relational_meaning=self._meaning(decision),
                          thread_id=thread_id, person_name=name,
                          event_id=message_id, delta=delta)
                _mark_event(states, message_id)
            person = self._touch_person(conn, person_id, name, role, moment)
            self._touch_chat_person(conn, thread_id, person_id, moment)
            drive = self._drives(conn)
            drive.update(boredom=_clamp(float(drive["boredom"]) - 0.15),
                         loneliness=_clamp(float(drive["loneliness"]) - 0.20),
                         energy=_clamp(float(drive["energy"]) + 0.03), last_pulse=moment)
            self._save_drives(conn, drive)
            self._bump_topic(conn, thread_id, moment)
            if decision is not None and addressed == "me" and act != "ignore":
                self._apply_relationship(conn, person_id, person, decision.relationship, moment)
            if decision is not None:
                self._save_state(conn, states)

    @staticmethod
    def _meaning(decision):
        note = str(getattr(decision.appraisal, "note", "") or "").strip()
        return note or str(getattr(decision, "reason", "") or "").strip()

    # ------------------------------------------------------------------ people

    def _touch_person(self, conn, person_id, name, role, moment):
        row = conn.execute(select(people).where(
            people.c.person_id == person_id)).mappings().first()
        person = {**PERSON_DEFAULTS, **(dict(row) if row is not None else {})}
        person["person_id"] = person_id
        aliases = _as_list(person.get("aliases"))
        clean = " ".join(str(name or "").split())[:80]
        if clean and person["name"] and clean not in {person["name"], *aliases}:
            aliases = [*aliases, clean]
        person.update(name=clean or person["name"], aliases=aliases[-8:],
                      interactions=int(person["interactions"]) + 1, last_seen=moment)
        person["familiarity"] = min(1.0, person["interactions"] / FAMILIARITY_FULL)
        person["notes"] = _as_list(person.get("notes"))
        conn.execute(self._upsert(people, person, ["person_id"],
                                  tuple(person)))
        return person

    def _touch_chat_person(self, conn, thread_id, person_id, moment):
        """Chat membership: who actually talks here, not globally."""
        conn.execute(fenced_insert(people, {"person_id": person_id, "name": ""},
                                   self.fence_clause())
                     .on_conflict_do_nothing(index_elements=["person_id"]))
        stmt = fenced_insert(thread_people, {"thread_id": thread_id, "person_id": person_id,
                                             "last_seen": moment, "interactions": 1},
                             self.fence_clause())
        conn.execute(stmt.on_conflict_do_update(
            index_elements=["thread_id", "person_id"],
            set_={"last_seen": stmt.excluded.last_seen,
                  "interactions": thread_people.c.interactions + 1}))

    def _apply_relationship(self, conn, person_id, person, delta, moment):
        """Relationship delta with diminishing returns and a cap per 10-minute window."""
        start = float(person.get("window_start") or 0.0)
        if moment - start >= WINDOW_SECONDS:
            start = moment
            person.update(window_trust=0.0, window_affection=0.0, window_respect=0.0,
                          window_annoyance=0.0)
        values = {}
        for name in ("trust", "affection", "respect", "annoyance"):
            raw, current = float(getattr(delta, name, 0.0)), float(person[name])
            applied = _inertia(raw, current) if raw else 0.0
            used = float(person.get(f"window_{name}") or 0.0)
            room = WINDOW_LIMIT - max(0.0, used) if applied >= 0 else WINDOW_LIMIT + min(0.0, used)
            applied = max(-max(room, 0.0), min(max(room, 0.0), applied))
            values[name] = _clamp(current + applied)
            person[f"window_{name}"] = used + values[name] - current
        values.update({f"window_{name}": person[f"window_{name}"]
                       for name in ("trust", "affection", "respect", "annoyance")},
                      window_start=start,
                      stage=stage_for(person["interactions"], values["trust"],
                                      values["affection"]))
        conn.execute(self._update(
            people.update().where(people.c.person_id == person_id).values(**values)))

    def _match_person(self, name, *, thread_id: str | None = None):
        target = " ".join(str(name or "").split()).casefold()
        if not target:
            return None
        if thread_id is None:
            rows = self.db.fetch_all(
                select(people.c.person_id, people.c.name, people.c.aliases)
                .order_by(people.c.last_seen.desc()).limit(500)
            )
        else:
            # Chat-scoped resolution: only people who actually take part in this thread may
            # answer a name here, so a stranger from another chat never leaks in.
            rows = self.db.fetch_all(
                select(people.c.person_id, people.c.name, people.c.aliases)
                .select_from(thread_people.join(
                    people, people.c.person_id == thread_people.c.person_id))
                .where(thread_people.c.thread_id == str(thread_id))
                .order_by(thread_people.c.last_seen.desc()).limit(500)
            )
        exact = [row for row in rows if str(row["name"]).casefold() == target]
        alias = [row for row in rows
                 if target in [str(item).casefold() for item in _as_list(row["aliases"])]]
        loose = [row for row in rows if target in str(row["name"]).casefold()]
        found = (exact or alias or loose)
        return str(found[0]["person_id"]) if found else None

    def person_by_name(self, thread_id: str, name: str) -> str | None:
        chat = str(thread_id or "").strip()
        if not chat:
            return None
        return self._match_person(name, thread_id=chat)

    def known_people(self, thread_id: str, limit: int = 8) -> list[dict]:
        """People who actually take part in this thread, newest first; others never leak in."""
        rows = self.db.fetch_all(
            select(people, thread_people.c.interactions.label("chat_interactions"),
                   thread_people.c.last_seen.label("chat_last_seen"))
            .select_from(thread_people.join(people,
                                            people.c.person_id == thread_people.c.person_id))
            .where(thread_people.c.thread_id == thread_id)
            .order_by(thread_people.c.last_seen.desc()).limit(max(1, int(limit))))
        return [{**row, "aliases": _as_list(row["aliases"]), "notes": _as_list(row["notes"])}
                for row in rows]

    def note_person(self, person_id: str, note: str, language: str) -> None:
        message = guard.safe_text(note, NOTES_TEXT, language=language)
        if not person_id or not message:
            return
        row = self.db.fetch_one(select(people.c.notes).where(people.c.person_id == person_id))
        notes = [str(item) for item in _as_list(row["notes"] if row else [])]
        if message not in notes:
            self._write(people, {"person_id": person_id,
                                 "notes": [*notes, message][-NOTES_KEEP:]},
                        ["person_id"], ("notes",))

    # ------------------------------------------------------------------ drives

    def _drives(self, conn):
        """Drives belong to MonoAgens itself, not the room: one row for every chat."""
        row = conn.execute(select(drives).where(
            drives.c.thread_id == CHARACTER)).mappings().first()
        return {"thread_id": CHARACTER, **DRIVE_DEFAULTS, **dict(row or {})}

    def _save_drives(self, conn, data):
        row = {"thread_id": CHARACTER, "energy": _clamp(data["energy"]),
               "boredom": _clamp(data["boredom"]), "loneliness": _clamp(data["loneliness"]),
               "last_pulse": float(data["last_pulse"])}
        conn.execute(self._upsert(drives, row, ["thread_id"], tuple(row)))

    def drives(self, thread_id: str) -> dict:
        with self.db.connect() as conn:
            data = self._drives(conn)
        return {key: float(data[key]) for key in ("energy", "boredom", "loneliness")}

    def _bump_topic(self, conn, thread_id, moment):
        stmt = fenced_insert(topics, {"thread_id": thread_id, "pending": 1,
                                      "last_message": moment}, self.fence_clause())
        conn.execute(stmt.on_conflict_do_update(index_elements=["thread_id"], set_={
            "pending": topics.c.pending + 1, "last_message": stmt.excluded.last_message}))

    def pulse(self, thread_id: str) -> dict:
        """A 24/7 heartbeat: the state decays (once per bucket) and the drives grow."""
        moment = time.time()
        event_id = f"pulse:{CHARACTER}:{int(moment // 300)}"
        with self.db.connect() as conn:
            states, data = self._load_state(conn, for_update=True), self._drives(conn)
            if not _event_done(states, event_id):
                self._decay(states, moment)
                _mark_event(states, event_id)
                self._save_state(conn, states)
                last = float(data["last_pulse"] or 0.0)
                hours = min(max(0.0, moment - last), PULSE_MAX_HOURS * 3600.0) / 3600.0
                if last <= 0:  # old chat/new DB: the first tick only anchors the time
                    data["last_pulse"] = moment
                    self._save_drives(conn, data)
                elif hours * 60 >= PULSE_MINUTES:
                    data["energy"] = _clamp(float(data["energy"]) - 0.06 * hours)
                    data["boredom"] = _clamp(float(data["boredom"]) + 0.08 * hours)
                    data["loneliness"] = _clamp(float(data["loneliness"]) + 0.06 * hours)
                    self._soothe(conn, hours)
                    data["last_pulse"] = moment
                    self._save_drives(conn, data)
            return self._payload(states, data)

    def _soothe(self, conn, hours):
        """Annoyance fades when nobody provokes it; closeness slows the half-life down."""
        base = float(self.config.grudge_half_life_hours or 0.0)
        if base <= 0 or hours <= 0:
            return
        life = (base * (1.0 + 3.0 * people.c.affection + 2.0 * people.c.trust)
                * (1.0 + 2.0 * people.c.familiarity))
        conn.execute(self._update(people.update().where(people.c.annoyance > 0).values(
            annoyance=func.greatest(0.0, people.c.annoyance * func.power(0.5, hours / life)))))

    # ------------------------------------------------------------------ calm

    def calm(self, thread_id: str, person_id: str = "") -> bool:
        """Repair turn: PAD returns to baseline and the target person's annoyance halves."""
        target = " ".join(str(person_id or "").split())
        with self.db.connect() as conn:
            states = self._load_state(conn, for_update=True)
            pid = self._match_person(target) or (target if target.isdigit() else "")
            if pid:
                query = select(people.c.person_id, people.c.annoyance).where(
                    people.c.person_id == pid)
            else:
                query = (select(people.c.person_id, people.c.annoyance).select_from(
                    thread_people.join(people,
                                       people.c.person_id == thread_people.c.person_id))
                    .where(thread_people.c.thread_id == thread_id))
            rows = conn.execute(query).mappings().all()
            emotion = states["emotion"]
            annoyance = max([float(row["annoyance"]) for row in rows],
                            default=max(0.0, -float(emotion["pleasure"])))
            upset = float(emotion["pleasure"]) < 0 or annoyance > 0.1
            for row in rows:
                conn.execute(self._update(
                    people.update().where(people.c.person_id == row["person_id"])
                    .values(annoyance=_clamp(float(row["annoyance"]) * 0.5))))
            states["emotion"] = {dim: _dim(dim, states["personality_baseline"][dim])
                                 for dim in PAD_BOUNDS}
            states["affective_pulse"] = _zero_pulse("repair")
            states["decayed_at"] = time.time()
            self._log(states, "repair", situation="the owner calmed me down",
                      appraisal="repair", thread_id=thread_id)
            self._save_state(conn, states)
        return bool(upset)

    # ------------------------------------------------------------------ topic & eras

    def topic(self, thread_id: str) -> dict:
        row = self.db.fetch_one(select(topics).where(topics.c.thread_id == thread_id)) or {}
        return {**TOPIC_EMPTY, **row, "thread_id": thread_id,
                "keywords": _as_list(row.get("keywords")),
                "unresolved": _as_list(row.get("unresolved"))}

    def topic_block(self, thread_id: str) -> str:
        data = self.topic(thread_id)
        summary = str(data["summary"]).strip()
        if not summary:
            return ""
        past = self.eras(thread_id, 1)
        lines = [f"{ERA_PREFIX}{str(past[0]['summary']).strip()}"] if past else []
        lines.append(f"Topic currently hot in this chat: {summary}")
        pending = [str(item) for item in data["unresolved"] if str(item).strip()][:2]
        if pending:
            lines.append(f"Unanswered questions: {'; '.join(pending)}")
        text = "\n".join(lines)
        limit = ERA_CHARS if past else TOPIC_CHARS
        if len(text) > limit:
            text = text[: limit - 1] + "…"
        return text

    def archive_era(self, thread_id: str, summary: str, keywords: list[str],
                    unresolved: list[str], *, started_at: float, ended_at: float) -> None:
        """Close a topic era; the next summary starts a new one."""
        with self.db.connect() as conn:
            conn.execute(fenced_insert(threads, {"thread_id": thread_id, "name": "",
                                                 "reset_ts": 0.0}, self.fence_clause())
                         .on_conflict_do_nothing(index_elements=["thread_id"]))
            self._add(conn, thread_eras, {
                "thread_id": thread_id, "started_at": float(started_at),
                "ended_at": float(ended_at),
                "summary": " ".join(str(summary or "").split())[:240],
                "keywords": _clean(keywords, 10), "unresolved": _clean(unresolved, 5)})

    def eras(self, thread_id: str, limit: int = 3) -> list[dict]:
        """Closed topic eras for this chat, newest first."""
        rows = self.db.fetch_all(select(thread_eras)
                                 .where(thread_eras.c.thread_id == thread_id)
                                 .order_by(thread_eras.c.ended_at.desc(),
                                           thread_eras.c.id.desc()).limit(max(1, int(limit))))
        return [{**row, "keywords": _as_list(row["keywords"]),
                 "unresolved": _as_list(row["unresolved"])} for row in rows]

    def set_topic(self, thread_id: str, summary: str, keywords: list[str],
                  unresolved: list[str], now: float | None = None) -> None:
        row = {"thread_id": thread_id, "last_message": time.time() if now is None else now,
               "summary": " ".join(str(summary or "").split())[:240],
               "keywords": _clean(keywords, 10), "unresolved": _clean(unresolved, 5)}
        self._write(topics, row, ["thread_id"],
                    ("last_message", "summary", "keywords", "unresolved"))

    def pending(self, thread_id: str) -> int:
        return int(self.topic(thread_id)["pending"])

    def mark_digest(self, thread_id: str, now: float | None = None) -> None:
        self._write(topics, {"thread_id": thread_id, "pending": 0,
                             "last_digest": time.time() if now is None else now},
                    ["thread_id"], ("pending", "last_digest"))

    def mark_research(self, thread_id: str, now: float | None = None, *, tz: str = "UTC") -> None:
        moment = time.time() if now is None else float(now)
        day = clock.day(moment, tz)
        row = self.db.fetch_one(select(topics.c.research_count, topics.c.research_day)
                                .where(topics.c.thread_id == thread_id))
        count = 1 if row is None or row["research_day"] != day else int(row["research_count"]) + 1
        self._write(topics, {"thread_id": thread_id, "last_research": moment,
                             "research_day": day, "research_count": count}, ["thread_id"],
                    ("last_research", "research_day", "research_count"))

    # ------------------------------------------------------------------ prompt

    def _payload(self, state_data, drive, person_id="", thread_id=""):
        emotion = state_data["emotion"]
        person = next((item for item in self.known_people(thread_id, 50)
                       if item["person_id"] == person_id), None) if person_id else None
        return {"summary": _summary(state_data), "pulse": _pulse_text(state_data),
                "pleasure": float(emotion["pleasure"]), "arousal": float(emotion["arousal"]),
                "dominance": float(emotion["dominance"]),
                "trust_tier": _tier(float(state_data.get("trust") or 0.0)),
                "trust": float(state_data.get("trust") or 0.0),
                "energy": float(drive["energy"]), "boredom": float(drive["boredom"]),
                "loneliness": float(drive["loneliness"]),
                "turns": int(state_data.get("total_turns") or 0),
                "annoyance": float(person["annoyance"]) if person else 0.0,
                "relation": _relation(float(person["affection"]),
                                      float(person["annoyance"]),
                                      float(person["trust"]),
                                      str(person["stage"])) if person else "",
                "person_name": str(person["name"]) if person else ""}

    def status(self, thread_id: str, person_id: str = "") -> dict:
        """Data for /mood: state summary, PAD, trust, and the relationship with the sender."""
        with self.db.connect() as conn:
            return self._payload(self._load_state(conn), self._drives(conn), person_id,
                                 thread_id)

    def prompt(self, thread_id: str, person_id: str = "") -> str:
        """Prompt block: state summary, pulse, PAD, latest log, and stance toward the chat."""
        with self.db.connect() as conn:
            states, drive = self._load_state(conn), self._drives(conn)
        emotion = states["emotion"]
        parts = [f"Your mood: {_summary(states)}. "
                 f"P {float(emotion['pleasure']):.1f}, A {float(emotion['arousal']):.1f}, "
                 f"D {float(emotion['dominance']):.1f}, "
                 f"trust {_tier(float(states.get('trust') or 0.0))}. "
                 f"Pulse: {_pulse_text(states)}."]
        log_lines = []
        for entry in _recent_log(states):
            cue = str(entry.get("situation") or entry.get("cue") or "").strip()
            label = str(entry.get("appraisal") or entry.get("event_type") or "").strip()
            if cue or label:
                who = str(entry.get("person_name") or "").strip()
                other = str(entry.get("thread_id") or "") not in ("", thread_id)
                tag = (f" [{who or 'other chat'}"
                       f"{', other chat' if who and other else ''}]") if who or other else ""
                meaning = str(entry.get("relational_meaning") or "").strip()
                log_lines.append(f"- {label or 'event'}{tag}: {cue}"
                                 + (f" ({meaning})" if meaning else ""))
        if log_lines:
            parts.append("What you recently felt:\n" + "\n".join(log_lines))
        urges = [name for name, value in (("bored", drive["boredom"]),
                                          ("lonely", drive["loneliness"]))
                 if float(value) >= 0.65]
        if float(drive["energy"]) <= 0.25:
            urges.append("tired")
        if urges:
            parts.append(f"You are {', '.join(urges)}; it may show in how you talk.")
        people = sorted(self.known_people(thread_id, 6),
                        key=lambda item: item["person_id"] != person_id)
        lines = []
        for item in people[:6]:
            if not str(item["name"]).strip():
                continue
            relation = _relation(float(item["affection"]), float(item["annoyance"]),
                                 float(item["trust"]), str(item["stage"]))
            line = (f"- {item['name']} ({str(item['stage']) or 'stranger'}): {relation}, "
                    f"trust {float(item['trust']):.1f}, "
                    f"affection {float(item['affection']):.1f}, "
                    f"annoyance {float(item['annoyance']):.1f}, "
                    f"{int(item['chat_interactions'])} interactions, "
                    f"last seen {_ago(item['chat_last_seen'], time.time())}.")
            notes = [str(note) for note in item["notes"][:3] if str(note).strip()]
            lines.append(line + (f" Notes: {'; '.join(notes)}." if notes else ""))
        if lines:
            parts.append("Your relationships:")
            parts.extend(lines)
        parts.append("Express this through word choice and reply length. "
                     "Never mention these numbers or state.")
        text = "\n".join(parts)
        return text if len(text) <= PROMPT_CHARS else text[: PROMPT_CHARS - 1].rstrip() + "…"

    # ------------------------------------------------------------------ stats

    def stats(self) -> dict:
        with self.db.connect() as conn:
            counts = {name: conn.execute(select(func.count()).select_from(table)).scalar_one()
                      for name, table in (("people", people), ("topics", topics),
                                          ("drives", drives),
                                          ("thread_people", thread_people))}
            found = conn.execute(select(state.c.key).where(state.c.key == STATE_KEY)).first()
        return {**{key: int(value) for key, value in counts.items()},
                "states": 1 if found is not None else 0}
