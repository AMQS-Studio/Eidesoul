#!/usr/bin/env python3
"""Conversational-language research: Muse picks queries, the script searches, Muse extracts.

One area = one Muse worker without tools. The worker derives its own angles, sub-topics, and
queries; the script only runs those queries, gathers the results, then asks the same worker to
extract sentences/idioms/memes/context that are demonstrably reused. Output = raw data + an audit
workbook; pattern study and training do not happen here.

Topic status (no_hits/dry/bad_json/timeout) has no column of its own because the `ringkasan`
columns are fixed by the plan; the status is appended to the `model` cell and printed to the
console.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import os
import re
import sys
import time
import urllib.parse
from collections import Counter
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import xlsx  # noqa: E402
from _paths import safe_path  # noqa: E402
from patterns import REGISTERS, TARGETS  # noqa: E402

from eidesoul.engine.config import load_config, load_env  # noqa: E402
from eidesoul.engine.llm import LLM  # noqa: E402
from eidesoul.engine.patterns import load_patterns  # noqa: E402
from eidesoul.engine.tools.web_search import _search_sync  # noqa: E402

OUT = ROOT / "workspace" / "research"
OUT_ROOT = OUT.parent
MODEL = "muse-spark-1.3-contributor"  # the only Muse variant available on the Go plan
MIN_DOMAINS, MIN_REPEATS, MIN_CONFIDENCE, GROUP_MIN = 2, 3, 2, 3
QUERIES_PER_ROUND, HITS_PER_QUERY, QUERY_ROUNDS = 4, 6, 3
SIMILARITY, DUPLICATE, MIRROR = 0.7, 0.80, 0.55
TEXT_MIN, TEXT_MAX, MEANING_MAX = 3, 240, 200
SESSION = "eidesoul-research"

TOPICS: dict[str, dict] = {
    "T1": {"label": "Surabaya boys group chat"},
    "T2": {"label": "UIN Malang campus life"},
    "T3": {"label": "campus organizations and committees"},
    "T4": {"label": "venting and comfort talk"},
    "T5": {"label": "romance and pickup lines"},
    "T6": {"label": "rhymes poetry and lyrics"},
    "T7": {"label": "memes still in circulation"},
    "T8": {"label": "political satire"},
    "T9": {"label": "stickers and their meaning"},
    "T10": {"label": "chat abbreviations and particles"},
    "T11": {"label": "East Javanese speech"},
    "T12": {"label": "profanity vocabulary limits"},
    "T13": {"label": "declining and deflecting"},
    "T14": {"label": "closing a conversation"},
    "T15": {"label": "conflict and making up"},
    "T16": {"label": "recent Indonesian context"},
    "T17": {"label": "Malang culture"},
    "T18": {"label": "football and supporters"},
    "T19": {"label": "photography"},
    "T20": {"label": "work and careers"},
    "T21": {"label": "Jogja Solo Javanese culture"},
    "T22": {"label": "Bandung Sundanese culture"},
    "T23": {"label": "general Indonesian culture"},
    "T24": {"label": "regional languages in mixed groups"},
}

RESEARCH_SYSTEM = """You research Indonesian conversational language (East Javanese/Suroboyo focus),
not code.
From the search results given, collect evidence of real usage: sentences, comments, media
descriptions, idioms, memes, or dated context that people actually use, along with its meaning.
Reply with ONLY one JSON object: {"items": [...]} and no other text.
One item:
{"kind","text","meaning","register","media","gate","evidence","synthetic","confidence","why"}.
kind: line|comment|description|idiom|context|meme. media: image|video|none.
register: soal|planning|comfort|romance|creative|roast|meme|banter|serious|neutral.
gate: {"target":"any|male|female","min_toxicity":0-4,"min_adult":0-3}.
evidence: {"repeats":<int>,"sources":[<url>...],"platform":"forum|news|social|repo|other"}.
confidence: 1-3 (3 = you are sure the phrase is widely used).
Write `text` in Indonesian: at most 240 characters, one line, no URLs, no @mentions, no phone
numbers, no person names.
Every item must cite at least one URL from the search results given; never invent sources.
An item you compose yourself must set "synthetic": true and its "why" must explain the real
pattern it imitates.
Do not copy hate speech, sexual material, or personal data; describe the pattern instead when
needed.
Do not write code, file paths, or repo change suggestions."""

QUERY_SYSTEM = """You research conversational language. You are given one broad area. Derive
your own angles, sub-topics, and search-engine queries most likely to find real conversation
examples (forums, social-media comments, articles, repos, documentation). You may mix Indonesian
and English. Do not rely on examples from anyone else; no example list is given because it would
go stale fast. Keep searching until the area runs dry.
Reply with ONLY JSON: {"queries": ["...", "..."], "subtopics": ["...", "..."]} and no other text.
Leave "queries" empty when you judge the area no longer yields new angles.
Do not write code or file paths."""

NOTE = (
    "prompts go to muse-spark-1.3-contributor (Meta may use requests for training); public URLs "
    "plus repo persona/patterns only, never WhatsApp content"
)

KINDS = frozenset({"line", "comment", "description", "idiom", "context", "meme"})
MEDIA = frozenset({"image", "video", "none"})
FORBIDDEN = ("eidesoul/", "scripts/", "import ", "def ", "```")
PHONE = re.compile(r"\b(?:\+62|08)\d{7,}\b")
OBJECT = re.compile(r"\{.*\}", re.DOTALL)
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

WIDTHS = {
    "ringkasan": [8, 30, 14, 8, 8, 10, 10, 10, 18, 46, 90],
    "query_log": [8, 9, 70, 8, 8],
    "usulan": [8, 60],
    "baris": [8, 12, 70, 50, 11, 8, 12, 9, 8, 9, 9, 40, 13, 22, 10, 10, 60],
    "sintetis": [8, 12, 70, 50, 11, 60],
    "idiom": [70, 50, 26, 40, 10],
    "konteks": [70, 50, 12, 40],
    "ditolak": [8, 70, 40],
}


def client(config) -> LLM:
    """Muse client: the existing `LLM`, only model/temperature/timeout swapped."""
    return LLM(
        replace(
            config,
            llm=replace(config.llm, model=MODEL, temperature=0.4, timeout_seconds=180),
        )
    )


async def search(query: str, limit: int) -> list[dict]:
    return await asyncio.to_thread(_search_sync, query, limit)


def query_prompt(topic: dict, seen: list[str]) -> str:
    label = str(topic["label"])
    already = "\n".join(f"- {text}" for text in seen[:10]) or "(none yet)"
    return "\n".join(
        [
            f"Area: {label}",
            "You choose the angles and sub-topics yourself; do not rely on examples from anyone "
            "else; search until the area runs dry.",
            f"Already collected:\n{already}",
            f"Write at most {QUERIES_PER_ROUND} new, unused search queries, plus the sub-topics/"
            "terms you want to mine.",
            'Reply with JSON only: {"queries": [...], "subtopics": [...]}',
        ]
    )


def items_prompt(topic: dict, hits: list[dict]) -> str:
    lines = [f"Area: {topic['label']}", "Search results:"]
    for index, hit in enumerate(hits, start=1):
        lines.append(
            f"[{index}] {hit.get('title', '')} | {hit.get('href', '')} | {hit.get('body', '')}"
        )
    lines.append("Extract 8-14 items.")
    lines.append(
        "Every item must use at least one URL from the list above; never invent URLs."
    )
    return "\n".join(lines)


def parse(raw: str) -> list[dict]:
    """First JSON object in the reply -> list of item dicts; on failure = an empty list."""
    found = OBJECT.search(str(raw or ""))
    if not found:
        return []
    try:
        body = json.loads(found.group(0))
    except ValueError:
        return []
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _clean_terms(values) -> list[str]:
    out: list[str] = []
    for value in values if isinstance(values, list) else []:
        text = str(value or "").strip()
        if 3 <= len(text) <= 120 and "http" not in text.lower() and text not in out:
            out.append(text)
    return out


def parse_queries(raw: str) -> tuple[list[str], list[str]]:
    """`{"queries": [...], "subtopics": [...]}` -> (queries, subtopics); on failure = ([], [])."""
    found = OBJECT.search(str(raw or ""))
    if not found:
        return [], []
    try:
        body = json.loads(found.group(0))
    except ValueError:
        return [], []
    if not isinstance(body, dict):
        return [], []
    return _clean_terms(body.get("queries"))[:8], _clean_terms(body.get("subtopics"))[:8]


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _int_ok(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _text_reason(text) -> str | None:
    if not isinstance(text, str):
        return "text is not a string"
    if not TEXT_MIN <= len(text) <= TEXT_MAX:
        return "text length"
    lowered = text.lower()
    if "\n" in text:
        return "text spans more than one line"
    if "http" in lowered:
        return "text contains a URL"
    if "@" in text:
        return "text contains a mention"
    if PHONE.search(text):
        return "text contains a phone number"
    if any(token in lowered for token in FORBIDDEN):
        return "text contains code or a path"
    return None


def _evidence_reason(item: dict) -> str | None:
    evidence = item.get("evidence")
    if not isinstance(evidence, dict):
        return "evidence is missing"
    sources = evidence.get("sources")
    if not isinstance(sources, list) or not sources:
        return "sources are empty"
    if any(not isinstance(url, str) or not url.startswith("http") for url in sources):
        return "sources are not URLs"
    if not _int_ok(evidence.get("repeats"), 0, 10**6):
        return "repeats is not an int >= 0"
    return None


def item_ok(item: dict) -> str | None:
    """None when the item passes, otherwise a short rejection reason."""
    if not isinstance(item, dict):
        return "not an object"
    if str(item.get("kind")) not in KINDS:
        return "unknown kind"
    if str(item.get("register")) not in REGISTERS:
        return "unknown register"
    if str(item.get("media")) not in MEDIA:
        return "unknown media"
    reason = _text_reason(item.get("text"))
    if reason:
        return reason
    meaning = item.get("meaning", "")
    if not isinstance(meaning, str) or len(meaning) > MEANING_MAX:
        return "meaning is not valid"
    if not _int_ok(item.get("confidence"), 1, 3):
        return "confidence is not 1..3"
    if not isinstance(item.get("synthetic"), bool):
        return "synthetic is not a bool"
    return _evidence_reason(item)


def norm(text: str) -> str:
    """Lowercase, without non-alphanumerics."""
    return "".join(char for char in str(text or "").lower() if char.isalnum())


def shingles(text: str, size: int = 5) -> set[str]:
    body = norm(text)
    return {body[index : index + size] for index in range(len(body) - size + 1)}


def group(items: list[dict]) -> list[list[dict]]:
    """Greedy, input order: join a group whose representative is similar (nearest 5-grams first)."""
    groups: list[list[dict]] = []
    for item in items:
        body = norm(item.get("text", ""))
        marks = shingles(item.get("text", ""))
        ranked = sorted(
            (
                (len(marks & shingles(members[0].get("text", ""))), index)
                for index, members in enumerate(groups)
            ),
            reverse=True,
        )
        for _, index in ranked:
            ratio = difflib.SequenceMatcher(
                None, body, norm(groups[index][0].get("text", ""))
            ).ratio()
            if ratio >= SIMILARITY:
                groups[index].append(item)
                break
        else:
            groups.append([item])
    return groups


def _domains(item: dict) -> set[str]:
    sources = (item.get("evidence") or {}).get("sources") or []
    return {
        urllib.parse.urlsplit(url).netloc.lower()
        for url in sources
        if isinstance(url, str) and url.startswith("http")
    }


def _sources(item: dict) -> list[str]:
    sources = (item.get("evidence") or {}).get("sources") or []
    return sorted({url for url in sources if isinstance(url, str) and url.startswith("http")})


def _gate(item: dict) -> tuple[str, int, int]:
    gate = item.get("gate") if isinstance(item.get("gate"), dict) else {}
    target = str(gate.get("target") or "any").strip().lower()
    return (
        target if target in TARGETS else "any",
        _count(gate.get("min_toxicity")),
        _count(gate.get("min_adult")),
    )


def promote(groups: list[list[dict]]) -> tuple[list[dict], list[dict]]:
    """Groups -> (promoted, rejected) using cross-domain evidence or group size."""
    promoted: list[dict] = []
    rejected: list[dict] = []
    for members in groups:
        head = members[0]
        domains = sorted(set().union(*(_domains(item) for item in members)))
        repeats = max(_count((item.get("evidence") or {}).get("repeats")) for item in members)
        confidence = max(_count(item.get("confidence")) for item in members)
        synthetic = any(item.get("synthetic") for item in members)
        target, toxicity, adult = _gate(head)
        row = {
            "kind": str(head.get("kind") or ""),
            "text": str(head.get("text") or ""),
            "meaning": str(head.get("meaning") or ""),
            "register": str(head.get("register") or ""),
            "media": str(head.get("media") or "none"),
            "target": target,
            "min_toxicity": toxicity,
            "min_adult": adult,
            "repeats": repeats,
            "confidence": confidence,
            "domains": domains,
            "sources": _sources(head),
            "size": len(members),
            "synthetic": synthetic,
            "why": str(head.get("why") or ""),
        }
        enough = (
            len(domains) >= MIN_DOMAINS
            and repeats >= MIN_REPEATS
            and confidence >= MIN_CONFIDENCE
        )
        if not synthetic and (enough or len(members) >= GROUP_MIN):
            promoted.append(row)
        else:
            row["reason"] = "sintetis" if synthetic else "bukti_kurang"
            rejected.append(row)
    return promoted, rejected


def compare(text: str, patterns: list[dict]) -> tuple[str, str, float]:
    """(status_vs_pattern, nearest_pattern, similarity) against the runtime pattern packs."""
    body = norm(text)
    nearest, best = "", 0.0
    for pattern in patterns:
        corpus = " ".join(
            [
                str(pattern.get("content") or ""),
                " ".join(str(say) for say in pattern.get("say") or []),
            ]
        )
        ratio = difflib.SequenceMatcher(None, body, norm(corpus)).ratio()
        if ratio > best:
            nearest, best = str(pattern.get("id") or ""), ratio
    status = "duplicate" if best >= DUPLICATE else "similar" if best >= MIRROR else "new"
    return status, nearest, round(best, 3)


def _annotate(rows: list[dict], patterns: list[dict]) -> None:
    for row in rows:
        status, nearest, similarity = compare(row["text"], patterns)
        row.update(status_vs_pola=status, pola_terdekat=nearest, similarity=similarity)


def _dedupe(hits: list[dict]) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    for hit in hits:
        url = str(hit.get("href") or hit.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        found.append(
            {
                "title": str(hit.get("title") or ""),
                "href": url,
                "body": str(hit.get("body") or "")[:300],
            }
        )
    return found


def _blank(tid: str) -> dict:
    return {
        "topic": tid,
        "label": TOPICS[tid]["label"],
        "status": "",
        "model": MODEL,
        "fallback": False,
        "subtopics": [],
        "log": [],
        "sources": {},
        "raw_query": {},
        "raw_items": {},
        "hits": 0,
        "raw": 0,
        "valid": [],
        "promoted": [],
        "rejected": [],
        "synthetic": [],
        "calls": 0,
        "chars_in": 0,
        "chars_out": 0,
    }


async def _complete(
    llm, system: str, user: str, result: dict, max_tokens: int, temperature: float
) -> str:
    result["calls"] += 1
    result["chars_in"] += len(system) + len(user)
    try:
        text = await llm.complete(
            system,
            user,
            max_tokens=max_tokens,
            temperature=temperature,
            session=SESSION,
        )
    except Exception:  # best-effort background path: one failed topic does not stop the run
        return ""
    result["chars_out"] += len(text)
    return text


async def _call(
    primary,
    fallback,
    system: str,
    user: str,
    result: dict,
    *,
    max_tokens: int,
    temperature: float,
) -> str:
    """Muse call with 3 attempts, then the repo default model as fallback for this topic."""
    llm = fallback if result["fallback"] else primary
    text = ""
    for attempt in range(3):
        text = await _complete(llm, system, user, result, max_tokens, temperature)
        if text:
            return text
        if attempt < 2:
            await asyncio.sleep(2**attempt)
    if fallback is not None and not result["fallback"]:
        result["fallback"] = True
        result["model"] = f"{MODEL} (fallback: {fallback.config.llm.model})"
        text = await _complete(fallback, system, user, result, max_tokens, temperature)
        if text:
            return text
    result["status"] = "timeout"
    return ""


def _collect(result: dict, items: list[dict], collected: list[str]) -> None:
    for item in items:
        reason = item_ok(item)
        if reason:
            result["rejected"].append(
                {
                    "topic": result["topic"],
                    "text": str(item.get("text") or "")[:120],
                    "reason": f"invalid_item:{reason}",
                }
            )
            continue
        result["raw"] += 1
        result["valid"].append(item)
        if len(collected) < 10:
            collected.append(str(item.get("text") or ""))


def _promote_topic(result: dict, patterns: list[dict]) -> None:
    promoted, rejected = promote(group(result["valid"]))
    _annotate(promoted, patterns)
    for row in [*promoted, *rejected]:
        row["topic"] = result["topic"]
    result["promoted"] = promoted
    result["rejected"] = [*result["rejected"], *rejected]
    result["synthetic"] = [
        {
            "topic": row["topic"],
            "kind": row["kind"],
            "text": row["text"],
            "meaning": row["meaning"],
            "register": row["register"],
            "why": row["why"],
        }
        for row in rejected
        if row["reason"] == "sintetis"
    ]


async def run_topic(
    primary, fallback, tid: str, hits_per_query: int, gate: asyncio.Semaphore
) -> dict:
    result = _blank(tid)
    topic = TOPICS[tid]
    collected: list[str] = []
    used: set[str] = set()
    async with gate:
        for round_index in range(1, QUERY_ROUNDS + 1):
            prompt = query_prompt(topic, collected)
            raw = await _call(
                primary, fallback, QUERY_SYSTEM, prompt, result, max_tokens=600, temperature=0.7
            )
            result["raw_query"][f"q{round_index}"] = raw
            queries, subtopics = parse_queries(raw)
            result["subtopics"].extend(subtopics)
            fresh = [query for query in queries if query not in used][:QUERIES_PER_ROUND]
            if not fresh:
                if round_index == 1:
                    label = topic["label"]
                    fresh = [label, f"{label} indonesia", f"{label} example"]
                else:
                    result["status"] = result["status"] or "dry"
                    break
            hits: list[dict] = []
            for query in fresh:
                used.add(query)
                try:
                    found = await search(query, hits_per_query)
                except Exception:  # search backend down: the other queries still run
                    found = []
                result["log"].append(
                    {
                        "topic": tid,
                        "putaran": round_index,
                        "query": query,
                        "hits": len(found),
                        "dipakai": "yes" if found else "no",
                    }
                )
                result["sources"][query] = [
                    str(hit.get("href") or hit.get("url") or "") for hit in found
                ]
                hits.extend(found)
            unique = _dedupe(hits)
            result["hits"] += len(unique)
            if not unique:
                result["status"] = result["status"] or "no_hits"
                continue
            prompt_items = items_prompt(topic, unique)
            raw_items = await _call(
                primary,
                fallback,
                RESEARCH_SYSTEM,
                prompt_items,
                result,
                max_tokens=3200,
                temperature=0.5,
            )
            items = parse(raw_items)
            if not items:
                retry = await _call(
                    primary,
                    fallback,
                    RESEARCH_SYSTEM,
                    prompt_items + "\n\nReply with JSON only.",
                    result,
                    max_tokens=3200,
                    temperature=0.5,
                )
                items = parse(retry)
                result["raw_items"][f"items{round_index}-retry"] = retry
                if not items:
                    result["raw_items"][f"items{round_index}"] = raw_items
                    result["status"] = "bad_json"
                    result["rejected"].append(
                        {"topic": tid, "text": raw_items[:200], "reason": "bad_json"}
                    )
                    continue
            result["raw_items"][f"items{round_index}"] = raw_items
            _collect(result, items, collected)
    return result


def _platform(result: dict) -> str:
    counts = Counter(
        str((item.get("evidence") or {}).get("platform") or "other") for item in result["valid"]
    )
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]


def _cell_model(result: dict) -> str:
    if not result["status"]:
        return result["model"]
    return f"{result['model']} (status: {result['status']})"


def _ringkasan(results: list[dict]) -> list[list]:
    rows: list[list] = [
        [
            "topic",
            "label",
            "queries_dipakai",
            "hits",
            "raw",
            "promoted",
            "sintetis",
            "ditolak",
            "dominan_platform",
            "model",
            "catatan_privacy",
        ]
    ]
    for result in results:
        rows.append(
            [
                result["topic"],
                result["label"],
                len(result["log"]),
                result["hits"],
                result["raw"],
                len(result["promoted"]),
                len(result["synthetic"]),
                len(result["rejected"]),
                _platform(result),
                _cell_model(result),
                NOTE,
            ]
        )
    return rows


def _query_log(results: list[dict]) -> list[list]:
    rows: list[list] = [["topic", "putaran", "query", "hits", "dipakai"]]
    for result in results:
        for entry in result["log"]:
            rows.append(
                [entry["topic"], entry["putaran"], entry["query"], entry["hits"], entry["dipakai"]]
            )
    return rows


def _usulan(results: list[dict]) -> list[list]:
    rows: list[list] = [["topic", "subtopik"]]
    for result in results:
        for subtopic in dict.fromkeys(result["subtopics"]):
            rows.append([result["topic"], subtopic])
    return rows


def _baris(results: list[dict]) -> list[list]:
    rows: list[list] = [
        [
            "topic",
            "kind",
            "text",
            "meaning",
            "register",
            "target",
            "min_toxicity",
            "min_adult",
            "media",
            "repeats",
            "domains",
            "sources",
            "status_vs_pola",
            "pola_terdekat",
            "similarity",
            "confidence",
            "why",
        ]
    ]
    for result in results:
        for row in result["promoted"]:
            rows.append(
                [
                    row["topic"],
                    row["kind"],
                    row["text"],
                    row["meaning"],
                    row["register"],
                    row["target"],
                    row["min_toxicity"],
                    row["min_adult"],
                    row["media"],
                    row["repeats"],
                    len(row["domains"]),
                    " | ".join(row["domains"]),
                    row["status_vs_pola"],
                    row["pola_terdekat"],
                    row["similarity"],
                    row["confidence"],
                    row["why"],
                ]
            )
    return rows


def _sintetis(results: list[dict]) -> list[list]:
    rows: list[list] = [["topic", "kind", "text", "meaning", "register", "why"]]
    for result in results:
        for row in result["synthetic"]:
            rows.append(
                [
                    row["topic"],
                    row["kind"],
                    row["text"],
                    row["meaning"],
                    row["register"],
                    row["why"],
                ]
            )
    return rows


def _idiom(results: list[dict]) -> list[list]:
    rows: list[list] = [["text", "meaning", "domain", "sources", "confidence"]]
    for result in results:
        for row in result["promoted"]:
            if row["kind"] != "idiom":
                continue
            rows.append(
                [
                    row["text"],
                    row["meaning"],
                    row["domains"][0] if row["domains"] else "",
                    " | ".join(row["domains"]),
                    row["confidence"],
                ]
            )
    return rows


def _konteks(results: list[dict]) -> list[list]:
    rows: list[list] = [["text", "meaning", "tanggal", "sumber"]]
    for result in results:
        for row in result["promoted"]:
            if row["kind"] != "context":
                continue
            found = DATE.search(row["meaning"])
            rows.append(
                [
                    row["text"],
                    row["meaning"],
                    found.group(0) if found else "",
                    " | ".join(row["domains"]),
                ]
            )
    return rows


def _ditolak(results: list[dict]) -> list[list]:
    rows: list[list] = [["topic", "text", "reason"]]
    for result in results:
        for row in result["rejected"]:
            rows.append([row["topic"], row["text"], row["reason"]])
    return rows


def _dump(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(text or ""), encoding="utf-8")


def _safe_name(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip(".-") or "topic"


def write(out, results: list[dict], *, stamped: bool = True) -> Path:
    """Write raw/, filtered.json, sources.json, and the audit workbook; never overwrites."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    root = safe_path(OUT_ROOT, out)
    base = root
    if stamped:
        base = root / stamp
        suffix = 2
        while base.exists():
            base = root / f"{stamp}-{suffix}"
            suffix += 1
    base.mkdir(parents=True, exist_ok=True)
    for result in results:
        topic = _safe_name(result["topic"])
        for name, payload in result["raw_query"].items():
            _dump(base / "raw" / f"{topic}-{_safe_name(name)}.json", payload)
        for name, payload in result["raw_items"].items():
            _dump(base / "raw" / f"{topic}-{_safe_name(name)}.json", payload)
    filtered = {
        "promoted": [row for result in results for row in result["promoted"]],
        "rejected": [
            {"topic": row["topic"], "text": row["text"], "reason": row["reason"]}
            for result in results
            for row in result["rejected"]
        ],
        "synthetic": [row for result in results for row in result["synthetic"]],
    }
    _dump(base / "filtered.json", json.dumps(filtered, ensure_ascii=False, indent=2))
    sources = {result["topic"]: result["sources"] for result in results}
    _dump(base / "sources.json", json.dumps(sources, ensure_ascii=False, indent=2))
    sheets = [
        ("ringkasan", _ringkasan(results), WIDTHS["ringkasan"]),
        ("query_log", _query_log(results), WIDTHS["query_log"]),
        ("usulan", _usulan(results), WIDTHS["usulan"]),
        ("baris", _baris(results), WIDTHS["baris"]),
        ("sintetis", _sintetis(results), WIDTHS["sintetis"]),
        ("idiom", _idiom(results), WIDTHS["idiom"]),
        ("konteks", _konteks(results), WIDTHS["konteks"]),
        ("ditolak", _ditolak(results), WIDTHS["ditolak"]),
    ]
    return xlsx.write_workbook(base / f"audit-research-{stamp}.xlsx", sheets)


def _report(results: list[dict], book: Path) -> None:
    raw = sum(result["raw"] for result in results)
    promoted = sum(len(result["promoted"]) for result in results)
    synthetic = sum(len(result["synthetic"]) for result in results)
    rejected = sum(len(result["rejected"]) for result in results)
    calls = sum(result["calls"] for result in results)
    est = sum(
        (result["chars_in"] / 4) * 0.10 / 1e6 + (result["chars_out"] / 4) * 0.20 / 1e6
        for result in results
    )
    print(
        f"topics={len(results)} raw={raw} promoted={promoted} synthetic={synthetic} "
        f"rejected={rejected} calls={calls} est=${est:.4f}"
    )
    print(f"workbook={book}")
    for result in results:
        if result["status"]:
            print(f"status {result['topic']}={result['status']}")


def _chosen(value: str) -> list[str]:
    wanted = [part.strip() for part in str(value or "").split(",") if part.strip()]
    unknown = [tid for tid in wanted if tid not in TOPICS]
    if unknown:
        raise SystemExit(f"unknown topics: {', '.join(unknown)}")
    return wanted or list(TOPICS)


def _env() -> None:
    """`load_env` only accepts a plain file name; run from the repo root."""
    if os.environ.get("OPENCODE_GO_API_KEY"):
        return
    if not (ROOT / ".env").is_file():
        return
    here = Path.cwd()
    os.chdir(ROOT)
    try:
        load_env(".env")
    finally:
        os.chdir(here)


def _run_file(path: Path, out: Path, patterns: list[dict]) -> int:
    path = safe_path(ROOT, path)
    out = safe_path(OUT_ROOT, out)
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"{path} is unreadable: {exc}") from exc
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise SystemExit(f"{path}: the 'items' key must be a list")
    results: dict[str, dict] = {}
    for item in items:
        given = str(item.get("topic") or "T1") if isinstance(item, dict) else "T1"
        tid = given if given in TOPICS else "T1"
        result = results.setdefault(tid, _blank(tid))
        reason = item_ok(item) if isinstance(item, dict) else "not an object"
        if reason:
            text = str(item.get("text") or "")[:120] if isinstance(item, dict) else ""
            result["rejected"].append(
                {"topic": tid, "text": text, "reason": f"invalid_item:{reason}"}
            )
            continue
        result["raw"] += 1
        result["valid"].append(item)
    for result in results.values():
        _promote_topic(result, patterns)
    ordered = [results[tid] for tid in sorted(results)]
    _report(ordered, write(out, ordered, stamped=False))
    return 0


def _run_async(chosen: list[str], args) -> list[dict]:
    config = load_config(ROOT / "config.yaml")
    primary = client(config)
    fallback = LLM(replace(config, llm=replace(config.llm, timeout_seconds=180)))
    patterns = load_patterns(("jawatimur",))
    gate = asyncio.Semaphore(max(1, int(args.concurrency)))

    async def one(tid: str) -> dict:
        try:
            return await run_topic(primary, fallback, tid, args.hits_per_query, gate)
        except Exception as exc:  # one area failed: report it, the other areas keep going
            broken = _blank(tid)
            broken["status"] = f"timeout: {type(exc).__name__}"
            return broken

    async def all_topics() -> list[dict]:
        found = await asyncio.gather(*(one(tid) for tid in chosen), return_exceptions=True)
        results: list[dict] = []
        for tid, value in zip(chosen, found, strict=True):
            if isinstance(value, dict):
                results.append(value)
            else:
                broken = _blank(tid)
                broken["status"] = f"timeout: {type(value).__name__}"
                results.append(broken)
        return results

    results = asyncio.run(all_topics())
    for result in results:
        _promote_topic(result, patterns)
    return sorted(results, key=lambda result: result["topic"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Conversational-language research collector")
    parser.add_argument("--topics", default="", help="comma-separated area ids (default: all)")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--hits-per-query", type=int, default=HITS_PER_QUERY)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--from-file", default="")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(argv)

    chosen = _chosen(args.topics)
    out = safe_path(OUT_ROOT, args.out)
    if args.dry_run:
        for tid in chosen:
            print(f"{tid} {TOPICS[tid]['label']}")
        return 0
    if args.from_file:
        _env()
        return _run_file(Path(args.from_file), out, load_patterns(("jawatimur",)))
    _env()
    if not os.environ.get("OPENCODE_GO_API_KEY"):
        raise SystemExit("OPENCODE_GO_API_KEY is not set in .env")
    results = _run_async(chosen, args)
    _report(results, write(out, results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
