"""Runtime pattern catalog: pick character moves from the packs under `library/patterns`.

A pack is not a transcript. One row = one move ("if this, then that") plus its gates: the
room register, the toxicity/adult level, and the gender target. Pack selection lives in
soul.yaml (`style.patterns`); no pack means no rows. Selection here is deterministic:
keywords first, then MiniLM cosine similarity when `<pack>_vectors.json` exists; without
the model the keyword score still runs. No sampling and no network call on the keyword
path.
"""


import json
import logging
import re
from functools import cache
from pathlib import Path

from .. import embed

log = logging.getLogger(__name__)

MODEL = embed.MODEL
LIBRARY = Path(__file__).resolve().parents[2] / "library" / "patterns"
PACK = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
DIM = embed.DIM
LEXICON_KEYS = ("serious", "laughter", "roast", "meme")
SERIOUS_REGISTERS = frozenset({"serious", "comfort", "soal"})
TARGETS = frozenset({"male", "female", "unknown"})
WINDOW = 8
CAP = 6
LINE_MAX = 180
MIN_SIMILARITY = 0.20
COVERAGE = 0.8
KEY_WEIGHT, HIT_WEIGHT = 0.7, 0.3
_AUTO = object()
_warned: set[str] = set()


def _warn_once(reason: str, message: str, *args) -> None:
    if reason in _warned:
        return
    _warned.add(reason)
    log.warning(message, *args)


WORD = re.compile(r"[^\W_]+", re.UNICODE)


def _number(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_row(row: dict) -> dict | None:
    ident = str(row.get("id") or "").strip()
    content = str(row.get("content") or "").strip()
    registers = [str(item) for item in row.get("register") or [] if str(item).strip()]
    if not ident or not content or not registers:
        return None
    return {
        "id": ident,
        "register": registers,
        "keys": [str(item).lower() for item in row.get("keys") or [] if str(item).strip()],
        "target": str(row.get("target") or "any").strip().lower(),
        "min_toxicity": _number(row.get("min_toxicity"), 0),
        "max_toxicity": _number(row.get("max_toxicity"), 4),
        "min_adult": _number(row.get("min_adult"), 0),
        "content": content,
        "say": [str(item).strip() for item in row.get("say") or [] if str(item).strip()],
        "priority": _number(row.get("priority"), 1),
        "always": bool(row.get("always")),
        "source": str(row.get("source") or ""),
    }


@cache
def load_pack(name: str) -> dict:
    """One pattern pack; an invalid or missing name yields an empty dict plus a warning."""
    if not isinstance(name, str) or not PACK.match(name):
        _warn_once(f"pack:{name}", "patterns: invalid pack name %r", name)
        return {}
    target = LIBRARY / f"{name}.json"
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _warn_once(f"pack:{name}", "patterns: %s is not readable", target)
        return {}
    return body if isinstance(body, dict) else {}


@cache
def load_patterns(packs: tuple[str, ...] = (), path: str | None = None) -> list[dict]:
    """Rows from the selected packs; `path` keeps the single-file script behaviour."""
    if path is not None:
        target = Path(path)
        try:
            body = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _warn_once(f"patterns:{target}", "patterns: %s is not readable", target)
            return []
        found = body.get("patterns") if isinstance(body, dict) else None
        if not isinstance(found, list):
            return []
        clean = [_clean_row(row) for row in found if isinstance(row, dict)]
        return [row for row in clean if row]
    rows: list[dict] = []
    seen: set[str] = set()
    for name in tuple(packs):
        found = load_pack(name).get("patterns")
        if not isinstance(found, list):
            continue
        for item in found:
            if not isinstance(item, dict):
                continue
            row = _clean_row(item)
            if row is None or row["id"] in seen:
                continue
            seen.add(row["id"])
            rows.append(row)
    return rows


@cache
def load_lexicon(packs: tuple[str, ...] = ()) -> dict[str, tuple[str, ...]]:
    """Merge the register markers of the selected packs; missing keys stay empty."""
    merged: dict[str, list[str]] = {key: [] for key in LEXICON_KEYS}
    for name in tuple(packs):
        body = load_pack(name).get("lexicon")
        if not isinstance(body, dict):
            continue
        for key in LEXICON_KEYS:
            for item in body.get(key) or ():
                text = str(item).strip()
                if text and text not in merged[key]:
                    merged[key].append(text)
    return {key: tuple(values) for key, values in merged.items()}


def room_register(
    messages: list[str], lexicon: dict[str, tuple[str, ...]] | None = None
) -> str:
    """`serious` | `banter` | `meme` | `neutral` for the recent message window."""
    marks = lexicon or {}
    body = " ".join(str(item or "") for item in messages[-WINDOW:]).lower()
    if any(marker in body for marker in marks.get("serious", ())):
        return "serious"
    if any(marker in body for marker in marks.get("laughter", ())) or any(
        marker in body for marker in marks.get("roast", ())
    ):
        return "banter"
    if any(marker in body for marker in marks.get("meme", ())):
        return "meme"
    return "neutral"


def _target(value: str) -> str:
    body = str(value or "").strip().lower()
    return body if body in TARGETS else "unknown"


def _register_ok(found: list[str], register: str) -> bool:
    if register in found:
        return True
    return register == "serious" and any(item in SERIOUS_REGISTERS for item in found)


def _eligible(row: dict, register: str, target: str, limits: tuple[int, int]) -> bool:
    toxic, adult = int(limits[0]), int(limits[1])
    if row["target"] not in ("any", target):
        return False
    if row["min_toxicity"] > toxic or toxic > row["max_toxicity"]:
        return False
    if row["min_adult"] > adult:
        return False
    return _register_ok(row["register"], register)


def _found(key: str, body: str, tokens: set[str]) -> bool:
    """Short keys match per word so "ai" never hits "sampai"."""
    return key in tokens if len(key) <= 3 else key in body


def _hits(keys: list[str], body: str) -> int:
    tokens = set(WORD.findall(body))
    return sum(1 for key in keys if _found(key, body, tokens))


@cache
def _load_vectors(pack: str) -> dict:
    target = LIBRARY / f"{pack}_vectors.json"
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(body, dict) or body.get("model") != MODEL or body.get("dim") != DIM:
        return {}
    vectors = body.get("vectors")
    return vectors if isinstance(vectors, dict) else {}


def _vectors(ids: list[str], packs: tuple[str, ...] = ()) -> dict[str, list[float]]:
    found: dict[str, list[float]] = {}
    for name in tuple(packs):
        found.update(_load_vectors(name))
    if not found:
        return {}
    usable = {
        ident: found[ident]
        for ident in ids
        if isinstance(found.get(ident), list) and len(found[ident]) == DIM
    }
    if len(usable) < COVERAGE * len(ids):
        return {}
    return usable


def _given(value):
    """An explicit encoder: anything with `.encode`, not only a callable object."""
    return value if hasattr(value, "encode") else None


def _cosines(
    patterns: list[dict], text: str, encoder, packs: tuple[str, ...] = ()
) -> dict[str, float]:
    body = str(text or "").strip()
    if not body:
        return {}
    vectors = _vectors([row["id"] for row in patterns], packs)
    if not vectors:
        return {}
    model = _given(encoder)
    if model is None:
        found = embed.encode([body])
    else:
        try:
            found = model.encode([body], convert_to_numpy=True)
        except Exception:
            _warn_once("encode", "patterns: encode failed, using keywords")
            return {}
    if not found:
        return {}
    query = [float(value) for value in found[0]]
    return {ident: embed.cosine(query, vector) for ident, vector in vectors.items()}


def _score(hits: int, similarity: float | None) -> float | None:
    if similarity is None:
        return float(hits)
    if similarity < MIN_SIMILARITY and not hits:
        return None
    return KEY_WEIGHT * similarity + HIT_WEIGHT * min(1.0, hits / 3)


def _should_embed(encoder, ids: list[str], packs: tuple[str, ...] = ()) -> bool:
    """`encoder=None` forces the keyword path; the default uses MiniLM when vectors exist."""
    if encoder is None:
        return False
    return bool(_given(encoder)) or bool(_vectors(ids, packs))


def select_patterns(
    count: int,
    *,
    register: str,
    target: str,
    limits: tuple[int, int],
    messages: list[str],
    packs: tuple[str, ...] = (),
    path: str | None = None,
    registers: tuple[str, ...] = (),
    lexicon: dict[str, tuple[str, ...]] | None = None,
    encoder=_AUTO,
) -> list[str]:
    """Pick the `count` most relevant moves for this turn (deterministic, no sampling)."""
    limit = max(0, min(CAP, int(count)))
    if not limit:
        return []
    room = _target(target)
    allowed = tuple(str(item).strip() for item in registers if str(item).strip())
    rows = load_patterns(packs, path)
    if allowed:
        rows = [row for row in rows if set(row["register"]) & set(allowed)]
    eligible = [row for row in rows if _eligible(row, register, room, limits)]
    if not eligible:
        return []
    body = " ".join(str(item or "") for item in messages[-WINDOW:]).lower()
    hits = {row["id"]: _hits(row["keys"], body) for row in eligible}
    ids = [row["id"] for row in eligible]
    cosines = (
        _cosines(eligible, body, encoder, packs) if _should_embed(encoder, ids, packs) else {}
    )
    scores: dict[str, float] = {}
    for row in eligible:
        value = _score(hits[row["id"]], None if row["always"] else cosines.get(row["id"]))
        if value is not None:
            scores[row["id"]] = value
    if not scores:
        return []
    ranked = sorted(
        (row for row in eligible if row["id"] in scores),
        key=lambda row: (-scores[row["id"]], -row["priority"], row["id"]),
    )
    return [_render(row) for row in ranked[:limit]]


def _render(row: dict) -> str:
    line = row["content"]
    if row["say"]:
        line += " (examples: " + "; ".join(f'"{item}"' for item in row["say"]) + ")"
    return line[:LINE_MAX]
