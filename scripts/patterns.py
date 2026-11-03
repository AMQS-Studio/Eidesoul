#!/usr/bin/env python3
"""Pattern catalog tool: validate, compute vectors, and build review workbooks.

`eidesoul/library/patterns/<pack>.json` is the runtime character data for one pack: style moves
of the "if this, then that" kind that `eidesoul/engine/patterns.py` selects every turn. This
script keeps the schema valid, computes MiniLM vectors for semantic selection, and provides a
review workbook that round-trips 1:1 with the pack file. `--pack` picks the pack under
`eidesoul/library/patterns/`: `<pack>.json` plus `<pack>_vectors.json` (default `jawatimur`).

    python scripts/patterns.py --check
    python scripts/patterns.py --embed
    python scripts/patterns.py --stats
    python scripts/patterns.py --build-xlsx workspace/review-patterns.xlsx
    python scripts/patterns.py --apply-xlsx workspace/review-patterns.xlsx

`--embed` needs `sentence-transformers` (extra `.[memory]`); without it the catalog stays
valid and the runtime falls back to keyword selection.
"""


import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import xlsx  # noqa: E402

LIBRARY = ROOT / "eidesoul" / "library" / "patterns"
PACK = "jawatimur"
_PACK = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def pack_paths(pack: str) -> tuple[Path, Path]:
    """Pack and vector paths inside the library; a bad name never escapes it."""
    name = str(pack or "").strip()
    if not _PACK.match(name):
        raise SystemExit(f"invalid pack name: {pack!r}")
    return LIBRARY / f"{name}.json", LIBRARY / f"{name}_vectors.json"


PATTERNS, VECTORS = pack_paths(PACK)
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
VERSION = 2
DIM = 384

REGISTERS = frozenset(
    {
        "soal",
        "planning",
        "comfort",
        "romance",
        "creative",
        "roast",
        "meme",
        "banter",
        "serious",
        "neutral",
    }
)
# Registers `room_register` can actually produce, plus the selector mapping.
REACHABLE = frozenset({"serious", "comfort", "soal", "banter", "meme", "neutral"})
TARGETS = frozenset({"any", "male", "female", "unknown"})
SOURCES = frozenset({"owner_edit", "curated", "derived"})
ID = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")  # lowercase slug; seeds use "_"
SHEET = "pola"
COLUMNS = [
    "id",
    "register",
    "target",
    "level",
    "keys",
    "konten",
    "contoh",
    "hindari",
    "prioritas",
    "komentar",
    "catatan",
    "status",
]
WIDTHS = [22, 26, 9, 13, 34, 70, 40, 30, 10, 30, 22, 8]
CONTENT_MIN, CONTENT_MAX = 10, 200
SAY_MAX, SAY_COUNT = 120, 2
AVOID_MAX, AVOID_COUNT = 40, 3


def load() -> dict:
    return json.loads(PATTERNS.read_text(encoding="utf-8"))


def rows(body: dict) -> list[dict]:
    found = body.get("patterns") if isinstance(body, dict) else None
    return [row for row in found or [] if isinstance(row, dict)]


def _id_deficits(where: str, row: dict, seen: set[str]) -> list[str]:
    ident = row.get("id")
    if not isinstance(ident, str) or not ID.match(ident):
        return [f"{where}: id must be a lowercase slug, got {ident!r}"]
    if ident in seen:
        return [f"{where}: duplicate id {ident}"]
    seen.add(ident)
    return []


def _register_deficits(where: str, row: dict) -> list[str]:
    found = row.get("register")
    if not isinstance(found, list) or not found:
        return [f"{where}: register must be a non-empty list"]
    out = [
        f"{where}: register {item!r} is outside the vocabulary"
        for item in found
        if item not in REGISTERS
    ]
    if not any(item in REACHABLE for item in found):
        out.append(
            f"{where}: register cannot be selected by the selector ({', '.join(map(str, found))})"
        )
    return out


def _key_deficits(where: str, row: dict) -> list[str]:
    found = row.get("keys")
    if not isinstance(found, list):
        return [f"{where}: keys must be a list"]
    if not found and not row.get("always"):
        return [f"{where}: non-always pattern needs at least one key"]
    out: list[str] = []
    seen: set[str] = set()
    for key in found:
        if not isinstance(key, str) or not key.strip() or key != key.lower():
            out.append(f"{where}: key {key!r} must be lowercase")
            continue
        if not 1 <= len(key.split()) <= 4:
            out.append(f"{where}: key {key!r} must be 1-4 words")
        if key in seen:
            out.append(f"{where}: duplicate key {key!r}")
        seen.add(key)
    return out


def _gate_deficits(where: str, row: dict) -> list[str]:
    out: list[str] = []
    if row.get("target") not in TARGETS:
        out.append(
            f"{where}: target {row.get('target')!r} must be any|male|female|unknown"
        )
    low, high = row.get("min_toxicity"), row.get("max_toxicity")
    if not (isinstance(low, int) and isinstance(high, int) and 0 <= low <= high <= 4):
        out.append(f"{where}: min/max toxicity must be ints 0..4 with min<=max")
    adult = row.get("min_adult")
    if not (isinstance(adult, int) and 0 <= adult <= 3):
        out.append(f"{where}: min_adult must be an int 0..3")
    priority = row.get("priority")
    if not (isinstance(priority, int) and 1 <= priority <= 10):
        out.append(f"{where}: priority must be an int 1..10")
    if not isinstance(row.get("always"), bool):
        out.append(f"{where}: always must be a bool")
    if row.get("source") not in SOURCES:
        out.append(
            f"{where}: source {row.get('source')!r} must be owner_edit|curated|derived"
        )
    return out


def _text_deficits(where: str, row: dict, seen: set[str], say_seen: set[str]) -> list[str]:
    out: list[str] = []
    content = row.get("content")
    if not isinstance(content, str) or not CONTENT_MIN <= len(content) <= CONTENT_MAX:
        out.append(f"{where}: content must be {CONTENT_MIN}-{CONTENT_MAX} characters")
    elif "\n" in content:
        out.append(f"{where}: content must be a single line")
    elif content in seen:
        out.append(f"{where}: duplicate content")
    else:
        seen.add(content)
    for field, limit, count in (("say", SAY_MAX, SAY_COUNT), ("avoid", AVOID_MAX, AVOID_COUNT)):
        items = row.get(field)
        if not isinstance(items, list) or len(items) > count:
            out.append(f"{where}: {field} must be a list of at most {count}")
            continue
        out += [
            f"{where}: {field} {item!r} must be at most {limit} characters"
            for item in items
            if not isinstance(item, str) or not item.strip() or len(item) > limit
        ]
    says = row.get("say")
    for item in says if isinstance(says, list) else []:
        if not isinstance(item, str) or not item.strip():
            continue
        if item[:1].isalpha() and item[:1].islower():
            out.append(f"{where}: say must start with an uppercase letter {item!r}")
        if item in say_seen:
            out.append(f"{where}: duplicate say across rows {item!r}")
        say_seen.add(item)
    comment = row.get("komentar")
    if comment is not None and not isinstance(comment, str):
        out.append(f"{where}: komentar must be text")
    return out


def validate(body: dict) -> list[str]:
    out: list[str] = []
    if body.get("version") != VERSION:
        out.append(f"header: version must be {VERSION}")
    if not str(body.get("model") or "").strip():
        out.append("header: model is empty")
    found = rows(body)
    if not found:
        out.append("header: patterns is empty")
    seen_ids: set[str] = set()
    seen_content: set[str] = set()
    seen_say: set[str] = set()
    for index, row in enumerate(found):
        where = f"patterns[{index}]"
        out += _id_deficits(where, row, seen_ids)
        out += _register_deficits(where, row)
        out += _key_deficits(where, row)
        out += _gate_deficits(where, row)
        out += _text_deficits(where, row, seen_content, seen_say)
    return out


def vectors_deficits(body: dict, path: Path) -> list[str]:
    if not path.is_file():
        return [f"{path.name}: missing; run --embed (the runtime falls back to keywords)"]
    try:
        built = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"{path.name}: unreadable"]
    if built.get("model") != body.get("model") or built.get("dim") != DIM:
        return [f"{path.name}: model/dim does not match the catalog"]
    found = built.get("vectors") or {}
    missing = [row["id"] for row in rows(body) if row.get("id") not in found]
    return [f"{path.name}: missing vectors for {', '.join(missing)}"] if missing else []


def _atomic_write(payload: str, *, vectors: bool = False) -> None:
    path = VECTORS if vectors else PATTERNS
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(payload, encoding="utf-8")
    os.replace(temp, path)


def dump(body: dict) -> None:
    _atomic_write(json.dumps(body, ensure_ascii=False, indent=2) + "\n")


def _encoder(model: str):
    from sentence_transformers import SentenceTransformer

    try:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        device = "cpu"
    return SentenceTransformer(model, device=device)


def embed(body: dict) -> dict:
    model = str(body.get("model") or MODEL)
    texts = [f"{row['content']} {' '.join(row.get('keys') or [])}" for row in rows(body)]
    encoder = _encoder(model)
    matrix = encoder.encode(
        texts, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
    )
    vectors = {
        row["id"]: [round(float(value), 4) for value in matrix[index]]
        for index, row in enumerate(rows(body))
    }
    built = {"model": model, "dim": int(matrix.shape[1]), "vectors": vectors}
    _atomic_write(json.dumps(built, ensure_ascii=False) + "\n", vectors=True)
    return built


def _level(row: dict) -> str:
    return (
        f"tox {row.get('min_toxicity')}-{row.get('max_toxicity')} / adult {row.get('min_adult')}+"
    )


def build_xlsx(body: dict, path: Path) -> Path:
    table: list[list] = [COLUMNS]
    for row in rows(body):
        table.append(
            [
                row.get("id", ""),
                ", ".join(row.get("register") or []),
                row.get("target", ""),
                _level(row),
                ", ".join(row.get("keys") or []),
                row.get("content", ""),
                " | ".join(row.get("say") or []),
                " | ".join(row.get("avoid") or []),
                row.get("priority", ""),
                row.get("komentar", ""),
                "always" if row.get("always") else "",
                "aktif" if row.get("always") else "pola",
            ]
        )
    return xlsx.write_workbook(path, [(SHEET, table, WIDTHS)])


def _split(value: str) -> list[str]:
    parts = [part.strip() for part in str(value or "").split("|")]
    return [part for part in parts if part]


def _split_keys(value: str) -> list[str]:
    parts = [part.strip().lower() for part in str(value or "").replace("|", ",").split(",")]
    return [part for part in parts if part]


APPLIED = ("id", "konten", "contoh", "hindari", "keys", "komentar")


def apply_xlsx(body: dict, path: Path) -> tuple[int, list[str]]:
    table = xlsx.read_workbook(path).get(SHEET) or []
    if not table:
        return 0, [f"{path}: sheet {SHEET!r} is empty"]
    header = [str(cell).strip() for cell in table[0]]
    index = {name: position for position, name in enumerate(header)}
    missing = [name for name in APPLIED if name not in index]
    if missing:
        return 0, [f"{path}: missing columns {', '.join(missing)}"]
    known = {row.get("id"): row for row in rows(body)}
    applied, unknown = 0, []
    for line in table[1:]:
        if not any(str(cell).strip() for cell in line):
            continue
        ident = str(line[index["id"]]).strip() if index.get("id", -1) < len(line) else ""
        row = known.get(ident)
        if row is None:
            unknown.append(ident or "<empty>")
            continue
        row["content"] = str(line[index["konten"]]).strip()
        row["say"] = _split(line[index["contoh"]])
        row["avoid"] = _split(line[index["hindari"]])
        row["keys"] = _split_keys(line[index["keys"]])
        comment = str(line[index["komentar"]]).strip()
        if comment:
            row["komentar"] = comment
        else:
            row.pop("komentar", None)
        applied += 1
    return applied, unknown


def _stats(body: dict) -> list[str]:
    found = rows(body)
    out = [f"patterns={len(found)} version={body.get('version')} model={body.get('model')}"]
    counts = {name: 0 for name in sorted(REGISTERS)}
    for row in found:
        for name in row.get("register") or []:
            counts[name] = counts.get(name, 0) + 1
    out.append(
        "register: " + ", ".join(f"{name}={count}" for name, count in counts.items() if count)
    )
    gates = {
        "always": [row for row in found if row.get("always")],
        "male": [row for row in found if row.get("target") == "male"],
        "tox>=3": [row for row in found if row.get("min_toxicity", 0) >= 3],
        "adult>=1": [row for row in found if row.get("min_adult", 0) >= 1],
    }
    out.append("gate: " + ", ".join(f"{name}={len(items)}" for name, items in gates.items()))
    out.append("always: " + ", ".join(row["id"] for row in gates["always"]))
    out.append(
        "source: "
        + ", ".join(
            f"{name}={sum(1 for row in found if row.get('source') == name)}"
            for name in sorted(SOURCES)
        )
    )
    return out


def _run_check() -> int:
    body = load()
    deficits = validate(body)
    if deficits:
        print("\n".join(deficits), file=sys.stderr)
        return 1
    print(f"patterns={len(rows(body))} ok")
    for line in vectors_deficits(body, VECTORS):
        print(line)
    return 0


def _run_embed() -> int:
    body = load()
    deficits = validate(body)
    if deficits:
        print("\n".join(deficits), file=sys.stderr)
        return 1
    try:
        built = embed(body)
    except (ImportError, OSError) as error:
        print(f"embed unavailable: install .[memory] ({error})")
        return 2
    print(f"vectors={len(built['vectors'])} model={built['model']} dim={built['dim']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    global PATTERNS, VECTORS

    parser = argparse.ArgumentParser(description="Eidesoul pattern catalog")
    parser.add_argument(
        "--pack",
        default=PACK,
        help=f"pack name under eidesoul/library/patterns (default: {PACK})",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="validate the schema and vocabulary")
    group.add_argument("--embed", action="store_true", help="compute <pack>_vectors.json")
    group.add_argument("--build-xlsx", metavar="PATH", help="write the review workbook")
    group.add_argument("--apply-xlsx", metavar="PATH", help="apply workbook edits")
    group.add_argument("--stats", action="store_true", help="summarise registers and gates")
    args = parser.parse_args(argv)
    PATTERNS, VECTORS = pack_paths(args.pack)
    if args.check:
        return _run_check()
    if args.embed:
        return _run_embed()
    if args.stats:
        print("\n".join(_stats(load())))
        return 0
    if args.build_xlsx:
        print(build_xlsx(load(), Path(args.build_xlsx)))
        return 0
    body = load()
    applied, unknown = apply_xlsx(body, Path(args.apply_xlsx))
    dump(body)
    print(f"applied={applied} unknown={len(unknown)}")
    for ident in unknown:
        print(f"unknown id: {ident}", file=sys.stderr)
    print("run --check and then --embed when content changes")
    return 1 if unknown else 0


if __name__ == "__main__":
    raise SystemExit(main())
