#!/usr/bin/env python3
"""Intent prototype tool: validation and statistics (ADR-0005 §4.6).

Each file under `monoagens/library/intent/` holds one intent's prototype sentences for one
language; the classifier embeds the incoming text once and scores it against both the intent's
prototypes and the negatives. A prototype is a short sentence, never a name, a URL, or a phone
number, and each file needs at least `MIN_PROTOTYPES` entries. The vectors are computed at
runtime, so there is no `--embed` step: nothing is stored.
"""


import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from monoagens.engine.memory.intent import (
    KINDS,
    LIBRARY,
    MIN_PROTOTYPES,
    NEGATIVE,
    stats,
)

MAX_WORDS = 12
URL = re.compile(r"(https?://|www\.|\b[\w-]+\.(?:com|id|net|org|io)\b)", re.IGNORECASE)
LONG_DIGITS = re.compile(r"\d{7,}")
HANDLE = re.compile(r"@\w+")


def files(folder: Path = LIBRARY) -> list[Path]:
    return sorted(Path(folder).glob("*.json"))


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sentences(value: object) -> list[str]:
    return [" ".join(str(item or "").split()) for item in value] if isinstance(value, list) else []


def _sentence_problems(where: str, entries: list[str]) -> list[str]:
    problems: list[str] = []
    for text in entries:
        if not text:
            problems.append(f"{where}: empty prototype")
        elif len(text.split()) > MAX_WORDS:
            problems.append(f"{where}: too many words: {text!r}")
        elif URL.search(text) or HANDLE.search(text) or "@" in text:
            problems.append(f"{where}: no names, URLs, or handles: {text!r}")
        elif LONG_DIGITS.search(text):
            problems.append(f"{where}: no phone-like digits: {text!r}")
    return problems


def check(folder: Path = LIBRARY) -> list[str]:
    """Validate every pack and the language coverage the loader expects."""
    found = files(folder)
    if not found:
        return [f"{folder}: no intent packs"]
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for path in found:
        kind, _, language = path.stem.partition(".")
        language = language.strip()
        if kind not in KINDS:
            problems.append(f"{path.name}: unknown kind {kind!r}")
            continue
        if not language:
            problems.append(f"{path.name}: no language suffix")
            continue
        seen.add((language, kind))
        try:
            body = load(path)
        except (OSError, ValueError) as exc:
            problems.append(f"{path.name}: unreadable: {exc}")
            continue
        if str(body.get("language") or "") != language:
            problems.append(f"{path.name}: language must be {language!r}")
        if not str(body.get("model") or "").strip():
            problems.append(f"{path.name}: model is required")
        for key in ("prototypes", NEGATIVE):
            entries = _sentences(body.get(key))
            if len(entries) < MIN_PROTOTYPES:
                problems.append(f"{path.name}: needs at least {MIN_PROTOTYPES} {key}")
            problems.extend(_sentence_problems(path.name, entries))
    for language in sorted({code for code, _ in seen}):
        missing = [kind for kind in KINDS if (language, kind) not in seen]
        if missing:
            problems.append(f"{language}: missing {', '.join(missing)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Intent prototype packs")
    parser.add_argument("--folder", default=str(LIBRARY), help="pack folder")
    parser.add_argument("--check", action="store_true", help="validate every pack")
    parser.add_argument("--stats", action="store_true", help="count prototypes per kind")
    args = parser.parse_args(argv)
    if args.check:
        problems = check(Path(args.folder))
        for problem in problems:
            print(problem)
        print("OK" if not problems else f"{len(problems)} problems", flush=True)
        return 0 if not problems else 1
    if args.stats:
        for row in stats():
            print(f"{row['language']} {row['kind']}: {row['prototypes']}", flush=True)
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
