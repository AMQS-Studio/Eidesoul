"""Knowledge layer: public researched facts selected semantically per turn.

Separate from the character patterns: patterns shape how the soul talks, facts shape what
it may know. Facts without vectors are still selectable through keywords; a broken file
means an empty list, not a crash. The prompt scaffolding is English, the fact content is
Indonesian per pack. Packs live under `library/facts` and are chosen by soul.yaml.
"""


import json
import logging
import re
from functools import cache
from pathlib import Path

from . import embed

log = logging.getLogger(__name__)

MODEL = embed.MODEL
LIBRARY = Path(__file__).resolve().parent.parent / "library" / "facts"
PACK = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
DIM = embed.DIM
CAP = 6
MIN_SIMILARITY = 0.55  # cosine strong enough to select without a keyword hit
KEY_WEIGHT, HIT_WEIGHT = 0.7, 0.3
COVERAGE = 0.8
LINE_MAX = 220
_AUTO = object()
_warned: set[str] = set()

WORD = re.compile(r"[^\W_]+", re.UNICODE)


def _warn_once(reason: str, message: str, *args) -> None:
    if reason in _warned:
        return
    _warned.add(reason)
    log.warning(message, *args)


def _clean(row: dict) -> dict | None:
    ident = str(row.get("id") or "").strip()
    text = str(row.get("text") or "").strip()
    if not ident or not text:
        return None
    return {
        "id": ident,
        "text": text,
        "keys": [str(key).lower() for key in row.get("keys") or [] if str(key).strip()],
        "topic": str(row.get("topic") or ""),
        "domains": [str(item) for item in row.get("domains") or []],
        "sources": [str(item) for item in row.get("sources") or []],
    }


@cache
def load_pack(name: str) -> dict:
    """One fact pack; an invalid or missing name yields an empty dict plus a warning."""
    if not isinstance(name, str) or not PACK.match(name):
        _warn_once(f"pack:{name}", "facts: invalid pack name %r", name)
        return {}
    target = LIBRARY / f"{name}.json"
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _warn_once(f"pack:{name}", "facts: %s is not readable", target)
        return {}
    return body if isinstance(body, dict) else {}


@cache
def load_facts(packs: tuple[str, ...] = (), path: str | None = None) -> list[dict]:
    """Rows from the selected packs; `path` keeps the single-file script behaviour."""
    if path is not None:
        target = Path(path)
        try:
            body = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _warn_once(f"facts:{target}", "facts: %s is not readable", target)
            return []
        found = body.get("facts") if isinstance(body, dict) else None
        if not isinstance(found, list):
            return []
        return [row for row in (_clean(item) for item in found if isinstance(item, dict)) if row]
    rows: list[dict] = []
    seen: set[str] = set()
    for name in tuple(packs):
        found = load_pack(name).get("facts")
        if not isinstance(found, list):
            continue
        for item in found:
            if not isinstance(item, dict):
                continue
            row = _clean(item)
            if row is None or row["id"] in seen:
                continue
            seen.add(row["id"])
            rows.append(row)
    return rows


@cache
def _load_vectors(pack: str) -> dict:
    target = LIBRARY / f"{pack}_vectors.json"
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if str(body.get("model") or "") != MODEL or int(body.get("dim") or 0) != DIM:
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
    return usable if len(usable) >= COVERAGE * len(ids) else {}


def _given(value):
    """An explicit encoder: anything with `.encode`, not only a callable object."""
    return value if hasattr(value, "encode") else None


def _cosines(
    facts: list[dict], text: str, encoder, packs: tuple[str, ...] = ()
) -> dict[str, float]:
    body = str(text or "").strip()
    if not body:
        return {}
    vectors = _vectors([row["id"] for row in facts], packs)
    if not vectors:
        return {}
    model = _given(encoder)
    if model is None:
        found = embed.encode([body])
    else:
        try:
            found = model.encode([body], convert_to_numpy=True)
        except Exception:
            _warn_once("encode", "facts: encode failed, using keywords")
            return {}
    if not found:
        return {}
    query = [float(value) for value in found[0]]
    return {ident: embed.cosine(query, vector) for ident, vector in vectors.items()}


def _hits(keys: list[str], body: str, tokens: set[str]) -> int:
    return sum(1 for key in keys if key in tokens or key in body)


def _score(hits: int, similarity: float | None) -> float | None:
    """One distinctive keyword is enough; cosine alone needs a close match."""
    if similarity is None:
        return KEY_WEIGHT * min(1.0, hits / 2) if hits else None
    if not hits and similarity < MIN_SIMILARITY:
        return None
    return KEY_WEIGHT * similarity + HIT_WEIGHT * min(1.0, hits / 2)


def _render(row: dict) -> str:
    return row["text"][:LINE_MAX]


def select_facts(
    count: int,
    *,
    messages: list[str],
    packs: tuple[str, ...] = (),
    path: str | None = None,
    encoder=_AUTO,
) -> list[str]:
    """Pick the `count` most relevant facts for this turn (deterministic, no sampling)."""
    limit = max(0, min(CAP, int(count)))
    if not limit:
        return []
    facts = load_facts(packs, path)
    if not facts:
        return []
    body = " ".join(str(item or "") for item in messages[-8:]).lower()
    tokens = set(WORD.findall(body))
    vectors = _vectors([row["id"] for row in facts], packs) if encoder is not None else {}
    cosines = _cosines(facts, body, encoder, packs) if vectors else {}
    ranked: list[tuple[float, dict]] = []
    for row in facts:
        hits = _hits(row["keys"], body, tokens)
        score = _score(hits, cosines.get(row["id"]))
        if score is None:
            continue
        ranked.append((score, row))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    return [_render(row) for _, row in ranked[:limit]]
