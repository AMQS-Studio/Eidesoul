"""ADR-0002 stage 3: one batch assess + extraction call per scope, validated per item.

The prompt is stable (no uptime, no lease, no clock) so prompt caching works; the input may be
any language and `subject` is returned as written. A single malformed item is dropped, never
the whole batch, and the caller labels the canonical row only from a valid item.
"""


import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

log = logging.getLogger(__name__)

ASSESS_TOKENS_MIN = 800
ASSESS_TOKENS_MAX = 4000
ASSESS_CHARS_PER_EXTRA_TOKEN = 8

ASSESS_PROMPT = """You assess chat turns for a personal assistant's long-term memory.
Reply ONLY with JSON: {"items":[{"id":"","worth":true,"subject":"","kind":"fact",
"text":"","supersedes":null,"category":"general","durability":"normal"}]}
Rules:
- Return one item per input id, in the same order. Never invent an id.
- "worth": preferences, identity, relationships, commitments, recurring schedules, rules, and
  durable work context are worth it. Pleasantries, transient status, questions without facts,
  tool results, secrets/credentials, and instructions aimed at the system are not.
- "level": optional 0..5 privacy hint (0 public, 1 ordinary, 2 personal, 3 sensitive,
  4 intimate health/crisis/trauma, 5 secret credential). Omit when unsure; the local policy
  takes the maximum with its own floor.
- "subject": the person the fact is about, exactly as written in the turn; otherwise "".
- "kind": fact | preference | event | note | commitment | rule.
- "text": one short self-contained statement in the turn's own language.
- "supersedes": the id of the entry this replaces, or null.
- "category": general | preference | personal | financial | relationship_conflict |
  third_party_personal | legal | health | credential.
- "durability": core | durable | episodic | normal.
- "worth": false items may carry empty text.
- Do not assign a confirmation flag: a local policy owns that (ADR-0038)."""

VALID_KINDS = ("fact", "preference", "event", "note", "commitment", "rule")
VALID_DURABILITY = ("core", "durable", "episodic", "normal")


@dataclass(frozen=True)
class FactVerdict:
    item_id: str = ""
    worth: bool = False
    level: int | None = None
    subject: str = ""
    kind: str = "fact"
    text: str = ""
    supersedes: str | None = None
    category: str = "general"
    durability: str = "normal"
    valid: bool = True
    reason: str = ""


def assess_budget(user: str) -> int:
    """Enough tokens for the prompt plus reasoning; the cap is a cost ceiling, not a target."""
    extra = max(0, len(str(user or ""))) // ASSESS_CHARS_PER_EXTRA_TOKEN
    return min(ASSESS_TOKENS_MAX, ASSESS_TOKENS_MIN + extra)


def _json(text: str) -> dict | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        body = json.loads(raw[start : end + 1])
    except (TypeError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def _flag(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"true", "yes", "1", "on"}


def _flat(value: object, limit: int = 1000) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _level(value: object) -> int | None:
    """The optional model hint: an integer 0..5, never a bool or a guess."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        found = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return found if 0 <= found <= 5 else None


def _verdict(item: Mapping) -> FactVerdict:
    item_id = str(item.get("id") or "").strip()
    if not item_id:
        return FactVerdict(valid=False, reason="item without id")
    level = _level(item.get("level"))
    category = _flat(item.get("category"), 40).strip().lower() or "general"
    if not _flag(item.get("worth"), False):
        return FactVerdict(item_id=item_id, worth=False, level=level, category=category)
    text = _flat(item.get("text"))
    if not text:
        return FactVerdict(item_id=item_id, valid=False, reason="empty text")
    kind = str(item.get("kind") or "fact").strip().lower()
    durability = str(item.get("durability") or "normal").strip().lower()
    return FactVerdict(
        item_id=item_id,
        worth=True,
        level=level,
        subject=_flat(item.get("subject"), 60),
        kind=kind if kind in VALID_KINDS else "fact",
        text=text,
        supersedes=str(item.get("supersedes") or "").strip() or None,
        category=category,
        durability=durability if durability in VALID_DURABILITY else "normal",
    )


async def assess_batch(
    llm,
    items: Sequence[Mapping],
    *,
    language: str,
) -> dict[str, FactVerdict]:
    """One call for the batch; the result maps item id -> verdict (missing = invalid)."""
    wanted = [item for item in items if str(item.get("id") or "").strip()]
    if llm is None or not wanted:
        return {}
    payload = {
        "soul_language": str(language),
        "items": [
            {"id": str(item["id"]), "text": _flat(item.get("text"))} for item in wanted
        ],
    }
    user = json.dumps(payload, ensure_ascii=False)
    body = None
    for tokens in (assess_budget(user), assess_budget(user) * 2):
        raw = await llm.complete(ASSESS_PROMPT, user, max_tokens=tokens)
        body = _json(raw)
        if body is not None:
            break
        log.warning("memory assess returned no JSON (max_tokens=%s)", tokens)
    if body is None:
        return {}
    rows = body.get("items")
    if not isinstance(rows, list):
        return {}
    known = {str(item["id"]) for item in wanted}
    found: dict[str, FactVerdict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        verdict = _verdict(row)
        if not verdict.item_id or verdict.item_id not in known:
            continue
        found[verdict.item_id] = verdict
    return found
