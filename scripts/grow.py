#!/usr/bin/env python3
"""Catalog grower: research corpus -> new facts or new patterns.

The JSONL corpus from `scripts/research.py` (one item per line: `text`, `kind`, `topic`,
`evidence.sources`) is grouped semantically, and every cluster that is not yet represented
becomes one declarative fact or one style move. Rows are written to the library packs picked
with `--pack`: `eidesoul/library/facts/<pack>.json` plus `<pack>_vectors.json`, or
`eidesoul/library/patterns/<pack>.json` plus `<pack>_vectors.json`.

    python scripts/grow.py --facts --dry-run
    python scripts/grow.py --facts
    python scripts/grow.py --patterns --dry-run
    python scripts/grow.py --patterns

Clusters already covered by the current catalog are skipped, so the command is idempotent:
running it twice does not duplicate rows. Without Muse (`.env`/missing key, 429, 5xx) facts of the
`context`/`description`/`idiom` kind still land as they are, while pure style clusters are skipped.
"""


import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import fakta
import patterns as cli
import research
from _paths import safe_path

from eidesoul.engine.config import load_config

CORPUS = Path.home() / "eidesoul-research" / "20261031-korpus" / "items.jsonl"
CORPUS_ROOT = CORPUS.parent.parent
MODEL = fakta.MODEL
GROW_SIMILARITY = 0.82
GROW_SHARED_WORDS = 2
COVERED_FACT = 0.60
COVERED_PATTERN = 0.80
TEXT_MAX, KEYS_MAX, SOURCES_MAX = 220, 4, 3
DF_MAX = 80
MUSE_CALLS = 4  # parallel model calls
ESCALATION = 1500  # planning models need this budget before they write JSON
FACT_KINDS = frozenset({"context", "description", "idiom"})
STYLE_KINDS = frozenset({"line", "comment", "meme"})
REACHABLE = frozenset({"serious", "comfort", "soal", "banter", "meme", "neutral"})
STOP = frozenset(
    [
        "yang",
        "dan",
        "di",
        "ke",
        "dari",
        "untuk",
        "pada",
        "itu",
        "ini",
        "dengan",
        "atau",
        "juga",
        "karena",
        "ada",
        "tidak",
        "gak",
        "nya",
        "dalam",
        "sudah",
        "akan",
        "bisa",
        "lebih",
        "kalo",
        "kalau",
        "jadi",
        "tapi",
        "sama",
        "nang",
        "sing",
        "iku",
        "iki",
        "arek",
        "rek",
        "cok",
        "yo",
        "ae",
    ]
)
FACTIVE = re.compile(
    r"\b(wajib|harus|adalah|merupakan|disebut|dipakai|berarti|artinya|yaitu|termasuk|biasanya|"
    r"dihitung|dibuka|dilaksanakan|berfungsi|fokus|target|seleksi|syarat|biaya|tarif|jadwal|"
    r"rutin|resmi)\b|\d",
    re.I,
)
WORD = re.compile(r"[a-z]{4,}")
CAPITALISED = re.compile(r"\b[A-Z][a-z]{3,}\b")
ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{1,4}\b")  # UKM, BEM, UKT: campus topics often use acronyms
MID_CAPITAL = re.compile(r"(?<=\s)[A-Z][a-z]{2,}")
OBJECT = re.compile(r"\{.*\}", re.DOTALL)
RETRY = "\n\nReply with JSON only."

FACT_PROMPT = """You are a knowledge-note writer, not a code writer.
Turn the conversation snippet below into ONE concise, neutral fact sentence written in Indonesian.
Rules: at most 180 characters, one line, starts with a capital letter, no person names, no phone
numbers, no URLs, no new opinions, and do not add facts that are not in the snippet.
Reply with ONLY JSON: {"fact": "..."}"""

MOVE_PROMPT = """You are the style-guide writer for an Indonesian chat character's way of speaking.
From the conversation examples below, write ONE imperative style move (not an example sentence)
that explains how to reply in that situation: at most 160 characters, one line, starts with a
capital letter, no person names, no URLs, no added facts.
Reply with ONLY JSON: {"move": "...", "keys": ["...", "..."], "register": "..."}
register must be one of: serious, comfort, soal, banter, meme, neutral.
keys are 2-4 lowercase keywords."""


def _read(path: Path) -> list[dict]:
    path = safe_path(CORPUS_ROOT, path)
    if not path.is_file():
        raise SystemExit(f"corpus not found: {path}")
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            out.append(item)
    if not out:
        raise SystemExit(f"corpus is empty: {path}")
    return out


def _tokens(text) -> list[str]:
    return [token for token in WORD.findall(str(text or "").lower()) if token not in STOP]


def _words(text) -> set[str]:
    return set(_tokens(text))


def _cut(text, limit: int = TEXT_MAX) -> str:
    """One line, spaces collapsed, cut at a word boundary."""
    body = " ".join(str(text or "").split())
    if len(body) <= limit:
        return body
    head = body[:limit]
    return head[: head.rfind(" ")].strip() if " " in head else head


def _sentence(text, limit: int = TEXT_MAX) -> str:
    body = _cut(text, limit)
    return body[:1].upper() + body[1:] if body[:1].isalpha() else body


def _shortest(texts: list[str], count: int = 3) -> list[str]:
    return sorted(dict.fromkeys(texts), key=len)[:count]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "")).strip("-")


def _id(topic: str, keys: list[str], taken: set[str]) -> str:
    base = f"{str(topic or 'T1').lower()}-{_slug('-'.join(keys[:2]))}"
    ident, index = base, 1
    while ident in taken:
        index += 1
        ident = f"{base}-{index}"
    taken.add(ident)
    return ident


def _domains(sources: list[str]) -> list[str]:
    out: list[str] = []
    for url in sources:
        host = urlparse(url).netloc.lower()
        if host and host not in out:
            out.append(host)
    return out


def _sources(item: dict) -> list[str]:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    found = evidence.get("sources") or []
    return [url for url in found if isinstance(url, str) and url.startswith("http")]


def _encoder():
    return fakta._encoder(MODEL)


def _embed(encoder, texts: list[str]) -> np.ndarray:
    matrix = encoder.encode(
        [str(text) for text in texts],
        batch_size=64,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return np.asarray(matrix, dtype="float32")


def _extend(encoder, matrix: np.ndarray | None, texts: list[str]) -> np.ndarray | None:
    found = [text for text in texts if text]
    if not found:
        return matrix
    block = _embed(encoder, found)
    return block if matrix is None else np.vstack([matrix, block])


def _covered(encoder, matrix: np.ndarray | None, texts: list[str], threshold: float) -> list[bool]:
    """True when every text has a match in `matrix` with cosine >= the threshold."""
    if matrix is None or not len(matrix):
        return [False] * len(texts)
    found = _embed(encoder, texts) @ matrix.T
    return [bool(found[index].max() >= threshold) for index in range(len(texts))]


def _clusters(items: list[dict], vectors: np.ndarray) -> list[dict]:
    """Greedy, file order: join the first cluster whose anchor is similar, otherwise start one."""
    found: list[dict] = []
    for index, item in enumerate(items):
        words = _words(item["text"])
        for cluster in found:
            anchor = cluster["items"][0]
            if len(words & cluster["words"]) < GROW_SHARED_WORDS:
                continue
            if float(vectors[index] @ vectors[anchor]) < GROW_SIMILARITY:
                continue
            cluster["items"].append(index)
            break
        else:
            found.append({"items": [index], "words": words})
    return found


def _summary(items: list[dict], cluster: dict) -> dict:
    members = [items[index] for index in cluster["items"]]
    texts = [str(item.get("text") or "").strip() for item in members]
    kinds = Counter(str(item.get("kind") or "") for item in members)
    topics = list(dict.fromkeys(str(item.get("topic") or "T1") for item in members))
    sources = sorted({url for item in members for url in _sources(item)})
    return {
        "size": len(members),
        "texts": texts,
        "kind": kinds.most_common(1)[0][0] if kinds else "",
        "topics": topics,
        "sources": sources,
        "domains": _domains(sources),
        "text": _cut(max(texts, key=len)),
    }


def _boost(token: str, caps: set[str], acronyms: list[str]) -> int:
    """Acronyms (UKM/BEM/UKT) rank highest, then proper names; the rest by low df."""
    if token in acronyms:
        return 4
    return 3 if token in caps else 0


def _keys(text: str, df: dict[str, int]) -> list[str]:
    """Keys: content words of 4+ letters plus acronyms, ordered by capitals/proper names then df."""
    body = str(text or "")
    found: list[str] = []
    for token in _tokens(body):
        if token not in found and df.get(token, 0) <= DF_MAX:
            found.append(token)
    acronyms = [word.lower() for word in ACRONYM.findall(body)]
    for token in acronyms:
        if token not in found:  # acronyms always pass: UKM/BEM/UKT mark the topic
            found.append(token)
    caps = {word.lower() for word in CAPITALISED.findall(body)}
    head = CAPITALISED.match(body)
    if head:  # a capital at the start of the sentence is not a proper name
        caps.discard(head.group(0).lower())
    scored = [
        (-(_boost(token, caps, acronyms) + 1 / (1 + df.get(token, 0))), index)
        for index, token in enumerate(found)
    ]
    order = [index for _, index in sorted(scored)]
    return [found[index] for index in order[:KEYS_MAX]]


def _df(items: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        for token in set(_tokens(item["text"])):
            out[token] = out.get(token, 0) + 1
    return out


def _json(raw: str) -> dict:
    found = OBJECT.search(str(raw or ""))
    if not found:
        return {}
    try:
        body = json.loads(found.group(0))
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _snippets(texts: list[str]) -> str:
    return "Snippets:\n" + "\n".join(f"- {text}" for text in _shortest(texts))


class Muse:
    """Muse `LLM` wrapper: an empty reply falls back to the repo default model; an error stops
    the style mode."""

    def __init__(self) -> None:
        self.llm = None
        self.primary = None
        self.fallback = None
        self.dead = False
        self.misses = 0

    def _client(self):
        if self.llm is None:
            research._env()
            config = load_config(ROOT / "config.yaml")
            self.primary = research.client(config)
            self.fallback = research.LLM(config)
            self.llm = self.primary
        return self.llm

    async def _once(self, system: str, user: str, session: str, max_tokens: int) -> str:
        return await self.llm.complete(
            system,
            user,
            max_tokens=max_tokens,
            temperature=0.2,
            session=session,
        )

    async def ask(self, system: str, user: str, session: str, max_tokens: int) -> str:
        if self.dead:
            return ""
        try:
            self._client()
            for budget in (max_tokens, ESCALATION, ESCALATION // 2):
                text = await self._once(system, user, session, budget)
                if text:
                    self.misses = 0
                    return text
                if self.llm is self.primary:
                    fallback_model = self.fallback.config.llm.model
                    print(f"muse reply empty, switching to the default model {fallback_model}")
                    self.llm = self.fallback
        except Exception as exc:  # missing key, 429/5xx, timeout: stop the style mode
            print(f"muse unavailable: {type(exc).__name__}: {exc}")
            self.dead = True
            return ""
        self.misses += 1
        if self.misses >= 3:
            print("muse unavailable: repeated empty replies")
            self.dead = True
        return ""

    async def json(self, system: str, user: str, session: str, max_tokens: int) -> dict:
        body = _json(await self.ask(system, user, session, max_tokens))
        if body:
            return body
        return _json(await self.ask(system, user + RETRY, session, max_tokens))


async def _answers(
    muse: Muse, summaries: list[dict], indexes: list[int], prompt: str, session: str, budget: int
) -> dict[int, dict]:
    """Muse JSON answers for `indexes`, run in parallel; result order still follows `indexes`."""
    gate = asyncio.Semaphore(MUSE_CALLS)

    async def one(index: int) -> dict:
        async with gate:
            return await muse.json(prompt, _snippets(summaries[index]["texts"]), session, budget)

    found = await asyncio.gather(*(one(index) for index in indexes))
    return {index: body for index, body in zip(indexes, found, strict=True) if body}


def _declarative(summary: dict) -> bool:
    return bool(
        summary["kind"] in FACT_KINDS
        or FACTIVE.search(summary["text"])
        or MID_CAPITAL.search(summary["text"])
    )


def _fact_row(
    summary: dict, keys: list[str], text: str, taken: set[str]
) -> dict | None:
    domains, size = summary["domains"], summary["size"]
    row = {
        "id": _id(summary["topics"][0], keys, taken),
        "topic": summary["topics"][0],
        "keys": keys,
        "text": text,
        "sources": summary["sources"][:SOURCES_MAX],
        "domains": domains,
        "confidence": 3 if len(domains) >= 2 and size >= 2 else 2 if len(domains) >= 2 else 1,
        "varian": [],
    }
    problems = fakta.validate({"facts": [row]})
    if problems:
        print(f"rejected {row['id']}: {problems[0]}")
        return None
    return row


async def grow_facts(items: list[dict], *, dry_run: bool, rewrite: bool) -> int:
    body = fakta.load()
    known = fakta.rows(body)
    encoder = _encoder()
    vectors = _embed(encoder, [str(item.get("text") or "") for item in items])
    summaries = [_summary(items, cluster) for cluster in _clusters(items, vectors)]
    df = _df(items)
    matrix = _extend(encoder, None, [str(row.get("text") or "") for row in known])
    wanted = [
        index
        for index, summary in enumerate(summaries)
        if _declarative(summary)
        and summary["kind"] in STYLE_KINDS
        and rewrite
        and not _covered(encoder, matrix, [summary["text"]], COVERED_FACT)[0]
    ]
    answers = await _answers(Muse(), summaries, wanted, FACT_PROMPT, "eidesoul-grow-facts", 200)
    taken = {str(row.get("id") or "") for row in known}
    fresh: list[dict] = []
    skipped = 0
    for index, summary in enumerate(summaries):
        style = summary["kind"] in STYLE_KINDS
        text = (
            _sentence(str(answers.get(index, {}).get("fact") or ""))
            if style
            else _sentence(summary["text"])
        )
        if not _declarative(summary) or (style and not text):
            skipped += 1
            continue
        if _covered(encoder, matrix, [text], COVERED_FACT)[0]:
            skipped += 1
            continue
        keys = _keys(text, df) or _keys(summary["text"], df)
        row = _fact_row(summary, keys, text, taken) if keys else None
        if row is None:
            skipped += 1
            continue
        fresh.append(row)
        matrix = _extend(encoder, matrix, [row["text"]])
    print(f"clusters={len(summaries)} added={len(fresh)} skipped={skipped}")
    if dry_run or not fresh:
        return 0
    merged = {**body, "facts": [*known, *fresh]}
    problems = fakta.validate(merged)
    if problems:
        print("\n".join(problems))
        return 1
    fakta._atomic_write(fakta.DATA, json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
    return 0


def _register(value) -> list[str]:
    found = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    out = [str(item).strip().lower() for item in found]
    out = [item for item in dict.fromkeys(out) if item in REACHABLE]
    return out or ["neutral"]


def _move_keys(value, fallback: list[str]) -> list[str]:
    out: list[str] = []
    for item in value if isinstance(value, list) else []:
        text = str(item or "").strip().lower()
        if text and 1 <= len(text.split()) <= 4 and text not in out:
            out.append(text)
    return out[:KEYS_MAX] or fallback


def _say(texts: list[str], used: set[str]) -> list[str]:
    out: list[str] = []
    for text in sorted(dict.fromkeys(texts), key=len):
        line = _sentence(text, cli.SAY_MAX)
        if line and line not in used and line not in out:
            out.append(line)
        if len(out) >= cli.SAY_COUNT:
            break
    return out


def _pattern_row(
    body: dict,
    summary: dict,
    answer: dict,
    df: dict[str, int],
    used_say: set[str],
    taken: set[str],
) -> dict | None:
    content = _sentence(str(answer.get("move") or ""), cli.CONTENT_MAX)
    if not cli.CONTENT_MIN <= len(content) <= cli.CONTENT_MAX:
        return None
    fallback = _keys(summary["text"], df)
    keys = _move_keys(answer.get("keys"), fallback)
    say = _say(summary["texts"], used_say)
    if not keys or not say:
        return None
    row = {
        "id": _id(summary["topics"][0], keys, taken),
        "register": _register(answer.get("register")),
        "keys": keys,
        "target": "any",
        "min_toxicity": 0,
        "max_toxicity": 4,
        "min_adult": 0,
        "content": content,
        "say": say,
        "avoid": [],
        "priority": 3,
        "always": False,
        "source": "derived",
        "komentar": f"grow corpus {summary['topics'][0]}",
    }
    problems = cli.validate({**body, "patterns": [row]})
    if problems:
        print(f"rejected {row['id']}: {problems[0]}")
        return None
    return row


async def grow_patterns(items: list[dict], *, dry_run: bool) -> int:
    body = cli.load()
    known = cli.rows(body)
    encoder = _encoder()
    vectors = _embed(encoder, [str(item.get("text") or "") for item in items])
    summaries = [_summary(items, cluster) for cluster in _clusters(items, vectors)]
    df = _df(items)
    references = [
        text
        for row in known
        for text in [str(row.get("content") or ""), *(row.get("say") or [])]
        if str(text or "").strip()
    ]
    matrix = _extend(encoder, None, references)
    used_say = {str(line) for row in known for line in (row.get("say") or [])}
    free = [
        index
        for index, summary in enumerate(summaries)
        if summary["kind"] in STYLE_KINDS
        and summary["size"] >= 2
        and not _covered(encoder, matrix, _shortest(summary["texts"]), COVERED_PATTERN)[0]
    ]
    answers = await _answers(Muse(), summaries, free, MOVE_PROMPT, "eidesoul-grow-patterns", 300)
    taken = {str(row.get("id") or "") for row in known}
    fresh: list[dict] = []
    for index in free:
        answer = answers.get(index)
        if not answer:
            continue
        content = _sentence(str(answer.get("move") or ""), cli.CONTENT_MAX)
        if _covered(encoder, matrix, [content], COVERED_PATTERN)[0]:
            continue
        row = _pattern_row(body, summaries[index], answer, df, used_say, taken)
        if row is None:
            continue
        fresh.append(row)
        used_say.update(row["say"])
        matrix = _extend(encoder, matrix, [row["content"], *row["say"]])
    print(f"clusters={len(summaries)} added={len(fresh)} skipped={len(summaries) - len(fresh)}")
    if free and not fresh and not answers:
        print("muse unavailable")
    if dry_run or not fresh:
        return 0
    merged = {**body, "patterns": [*known, *fresh]}
    problems = cli.validate(merged)
    if problems:
        print("\n".join(problems))
        return 1
    cli.dump(merged)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grow facts/patterns from the research corpus")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--facts", action="store_true", help="add facts from new clusters")
    mode.add_argument("--patterns", action="store_true", help="add patterns from style clusters")
    parser.add_argument("--corpus", type=Path, default=CORPUS, help="corpus JSONL file")
    parser.add_argument(
        "--pack",
        default="",
        help=f"target pack (default: {cli.PACK} for patterns, {fakta.PACK} for facts)",
    )
    parser.add_argument("--dry-run", action="store_true", help="count only, write nothing")
    parser.add_argument("--no-rewrite", action="store_true", help="skip clusters that need Muse")
    args = parser.parse_args(argv)

    items = _read(safe_path(CORPUS_ROOT, args.corpus))
    if args.facts:
        fakta.DATA, fakta.VECTORS = fakta.pack_paths(args.pack or fakta.PACK)
        return asyncio.run(grow_facts(items, dry_run=args.dry_run, rewrite=not args.no_rewrite))
    cli.PATTERNS, cli.VECTORS = cli.pack_paths(args.pack or cli.PACK)
    return asyncio.run(grow_patterns(items, dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
