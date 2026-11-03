#!/usr/bin/env python3
"""Affect prototype tool: validation and statistics.

Each file under `eidesoul/library/affect/` holds the prototype sentences for one language;
the vibe runner scores a message against every label's prototypes on the shared embedder.
A prototype is a short casual sentence, never a name, a URL, or a phone number. Exactly the
eight labels in `EMOTION_VAD` are allowed, and each label needs at least `MIN_PROTOTYPES`.
"""


import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eidesoul.engine.stores.vibe import EMOTION_VAD  # noqa: E402

LIBRARY = ROOT / "eidesoul" / "library" / "affect"
MIN_PROTOTYPES = 6
MAX_WORDS = 8
URL = re.compile(r"(https?://|www\.|\b[\w-]+\.(?:com|id|net|org|io)\b)", re.IGNORECASE)
LONG_DIGITS = re.compile(r"\d{7,}")


def files(folder: Path = LIBRARY) -> list[Path]:
    return sorted(Path(folder).glob("*.json"))


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def rows(body: dict) -> dict[str, list]:
    found = body.get("prototypes") if isinstance(body, dict) else None
    return {
        str(label): list(items)
        for label, items in (found or {}).items()
        if isinstance(items, list)
    }


def _entry_deficits(where: str, label: str, entries: list) -> list[str]:
    problems: list[str] = []
    for entry in entries:
        text = " ".join(str(entry or "").split())
        if not text:
            problems.append(f"{where}: {label} has an empty prototype")
        elif len(text.split()) > MAX_WORDS:
            problems.append(f"{where}: {label}: too many words: {text!r}")
        elif URL.search(text) or "@" in text:
            problems.append(f"{where}: {label}: no names, URLs, or handles: {text!r}")
        elif LONG_DIGITS.search(text):
            problems.append(f"{where}: {label}: no phone-like digits: {text!r}")
    return problems


def _label_deficits(where: str, found: dict[str, list]) -> list[str]:
    problems = [f"{where}: missing label {label}" for label in sorted(EMOTION_VAD - found.keys())]
    for label, entries in found.items():
        if label not in EMOTION_VAD:
            problems.append(f"{where}: unknown label {label}")
            continue
        if len(entries) < MIN_PROTOTYPES:
            problems.append(
                f"{where}: {label} has {len(entries)} prototype(s), need {MIN_PROTOTYPES}"
            )
        problems += _entry_deficits(where, label, entries)
    return problems


def validate(body: dict, *, where: str) -> list[str]:
    """Every problem with one language file; an empty list means it ships."""
    if not isinstance(body, dict):
        return [f"{where}: file must be a JSON object"]
    problems: list[str] = []
    if not str(body.get("language") or "").strip():
        problems.append(f"{where}: language is required")
    if not str(body.get("model") or "").strip():
        problems.append(f"{where}: model is required")
    return problems + _label_deficits(where, rows(body))


def _stats(body: dict) -> list[str]:
    found = rows(body)
    counts = ", ".join(f"{label}={len(entries)}" for label, entries in sorted(found.items()))
    total = sum(len(entries) for entries in found.values())
    return [
        f"language={body.get('language')} model={body.get('model')} labels={len(found)}",
        f"prototypes={total}",
        f"per label: {counts}",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Eidesoul affect prototypes")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="validate every language file")
    group.add_argument("--stats", action="store_true", help="prototype counts per language")
    args = parser.parse_args(argv)

    targets = files()
    if not targets:
        print(f"no prototype files under {LIBRARY}")
        return 1
    if args.stats:
        for path in targets:
            print("\n".join(_stats(load(path))))
        return 0
    problems: list[str] = []
    for path in targets:
        try:
            body = load(path)
        except (OSError, ValueError) as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        problems += validate(body, where=path.name)
    if problems:
        print("\n".join(problems))
        return 1
    print(f"labels={len(EMOTION_VAD)} ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
