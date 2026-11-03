#!/usr/bin/env python3
"""Fact catalog tool: validation, vector computation, and statistics.

A fact is content Eidesoul is allowed to know (not how it talks; that lives in the pattern
packs). Every fact must cite at least one source URL, and `confidence` is 3 when two or more
domains back it. `eidesoul/library/facts/<pack>.json` holds the pack data and
`<pack>_vectors.json` its MiniLM vectors; `--pack` picks the pack under
`eidesoul/library/facts/` (default `indonesia`).
"""


import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
LIBRARY = ROOT / "eidesoul" / "library" / "facts"
PACK = "indonesia"
_PACK = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def pack_paths(pack: str) -> tuple[Path, Path]:
    """Pack and vector paths inside the library; a bad name never escapes it."""
    name = str(pack or "").strip()
    if not _PACK.match(name):
        raise SystemExit(f"invalid pack name: {pack!r}")
    return LIBRARY / f"{name}.json", LIBRARY / f"{name}_vectors.json"


DATA, VECTORS = pack_paths(PACK)
DIM = 384
TEXT_MIN, TEXT_MAX = 10, 220
KEY_MAX, KEY_WORDS = 4, 4
MIN_DOMAINS = 1
ID = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
TOPIC = re.compile(r"^T\d+$")


def load(path: Path | str = DATA) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows(body: dict) -> list[dict]:
    found = body.get("facts") if isinstance(body, dict) else None
    return [row for row in found or [] if isinstance(row, dict)]


def _id_deficits(where: str, row: dict, seen: set[str]) -> list[str]:
    ident = row.get("id")
    if not isinstance(ident, str) or not ID.match(ident):
        return [f"{where}: id must be a lowercase slug, got {ident!r}"]
    if ident in seen:
        return [f"{where}: duplicate id {ident}"]
    seen.add(ident)
    return []


def _text_deficits(where: str, row: dict) -> list[str]:
    text = row.get("text")
    if not isinstance(text, str) or not TEXT_MIN <= len(text) <= TEXT_MAX:
        return [f"{where}: text must be {TEXT_MIN}-{TEXT_MAX} characters"]
    if "\n" in text:
        return [f"{where}: text must be a single line"]
    if text[:1].isalpha() and text[:1].islower():
        return [f"{where}: text must start with an uppercase letter"]
    return []


def _key_deficits(where: str, row: dict) -> list[str]:
    found = row.get("keys")
    if not isinstance(found, list) or not found:
        return [f"{where}: keys must be a non-empty list"]
    if len(found) > KEY_MAX:
        return [f"{where}: keys must contain at most {KEY_MAX}"]
    out: list[str] = []
    seen: set[str] = set()
    for key in found:
        if not isinstance(key, str) or not key.strip() or key != key.lower():
            out.append(f"{where}: key {key!r} must be lowercase")
            continue
        if not 1 <= len(key.split()) <= KEY_WORDS:
            out.append(f"{where}: key {key!r} must be 1-{KEY_WORDS} words")
        if key in seen:
            out.append(f"{where}: duplicate key {key!r}")
        seen.add(key)
    return out


def _source_deficits(where: str, row: dict) -> list[str]:
    out: list[str] = []
    sources = row.get("sources")
    if not isinstance(sources, list) or not sources:
        out.append(f"{where}: sources must be a non-empty list")
    elif any(not isinstance(url, str) or not url.startswith("http") for url in sources):
        out.append(f"{where}: every source must be an http URL")
    domains = row.get("domains")
    if not isinstance(domains, list) or len(domains) < MIN_DOMAINS:
        out.append(f"{where}: domains must be >= {MIN_DOMAINS} (cross-source evidence)")
    if not isinstance(row.get("topic"), str) or not TOPIC.match(str(row.get("topic"))):
        out.append(f"{where}: topic must be a research id like T21")
    confidence = row.get("confidence")
    if not isinstance(confidence, int) or isinstance(confidence, bool) or not 1 <= confidence <= 3:
        out.append(f"{where}: confidence must be an int 1..3")
    return out


def validate(body: dict) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    found = rows(body)
    if not found:
        out.append("no facts")
    for index, row in enumerate(found, start=1):
        where = f"facts[{index}] {row.get('id') or '-'}"
        out += _id_deficits(where, row, seen)
        out += _text_deficits(where, row)
        out += _key_deficits(where, row)
        out += _source_deficits(where, row)
    return out


def vectors_deficits(body: dict, path: Path) -> list[str]:
    if not path.is_file():
        return [f"{path.name}: vector file is missing"]
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return [f"{path.name}: invalid JSON"]
    if str(stored.get("model") or "") != MODEL:
        return [f"{path.name}: model does not match"]
    if int(stored.get("dim") or 0) != DIM:
        return [f"{path.name}: dim must be {DIM}"]
    vectors = stored.get("vectors") if isinstance(stored.get("vectors"), dict) else {}
    missing = [row["id"] for row in rows(body) if not isinstance(vectors.get(row.get("id")), list)]
    return [f"{path.name}: missing vectors for {', '.join(missing)}"] if missing else []


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix=".facts-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _encoder(model: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model, device="cpu")


def embed(body: dict, path: Path = VECTORS) -> dict:
    model = str(body.get("model") or MODEL)
    found = rows(body)
    if not found:
        raise SystemExit("nothing to embed")
    encoder = _encoder(model)
    vectors = encoder.encode(
        [row["text"] for row in found], batch_size=32, normalize_embeddings=True
    )
    built = {
        "version": int(body.get("version") or 1),
        "model": model,
        "dim": int(len(vectors[0])),
        "vectors": {
            row["id"]: [round(float(value), 6) for value in vector]
            for row, vector in zip(found, vectors, strict=True)
        },
    }
    _atomic_write(path, json.dumps(built, ensure_ascii=False) + "\n")
    return built


def _stats(body: dict) -> list[str]:
    found = rows(body)
    topics: dict[str, int] = {}
    for row in found:
        topics[row["topic"]] = topics.get(row["topic"], 0) + 1
    top = ", ".join(
        f"{name}={count}" for name, count in sorted(topics.items(), key=lambda pair: -pair[1])
    )
    keys = sum(len(row.get("keys") or []) for row in found)
    domains = sum(len(row.get("domains") or []) for row in found)
    return [
        f"facts={len(found)} version={body.get('version')} model={body.get('model')}",
        f"topic: {top}",
        f"keys={keys} average={keys / len(found):.1f} | domains={domains}",
    ]


def main(argv: list[str] | None = None) -> int:
    global DATA, VECTORS

    parser = argparse.ArgumentParser(description="Eidesoul fact catalog")
    parser.add_argument(
        "--pack",
        default=PACK,
        help=f"pack name under eidesoul/library/facts (default: {PACK})",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="validate the schema and vectors")
    group.add_argument("--embed", action="store_true", help="recompute the vectors")
    group.add_argument("--stats", action="store_true", help="catalog summary")
    args = parser.parse_args(argv)
    DATA, VECTORS = pack_paths(args.pack)

    body = load()
    if args.embed:
        built = embed(body)
        print(f"vectors={len(built['vectors'])} model={built['model']} dim={built['dim']}")
        return 0
    if args.stats:
        print("\n".join(_stats(body)))
        return 0
    problems = validate(body) + vectors_deficits(body, VECTORS)
    if problems:
        print("\n".join(problems))
        return 1
    print(f"facts={len(rows(body))} ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
