"""Neutral-core gate (ADR-0033 §6): culture, transport, and vendor stay out of the mechanism.

Run with `--check`. Every rule has one reason to exist; the allowlist names why a token is
there and which part of the core owns it. A new entry is a reviewed change, never a silent
exception.
"""

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "eidesoul" / "engine"
ADMIN = ROOT / "eidesoul" / "admin"
LANG = ROOT / "eidesoul" / "lang"
SAFETY = ROOT / "eidesoul" / "library" / "safety"

# §4.12: spine components may be named in core. Each entry carries its ADR.
SPINE = {
    "postgresql": "ADR-0001: the store of record",
    "psycopg": "ADR-0001: the Postgres driver",
    "mem0": "ADR-0001: semantic memory behind the port",
    "minilm": "ADR-0031: the shared embedder",
    "openai": "ADR-0031: the wire client",
    "pgvector": "ADR-0001: the vector column",
}
# Optional dependencies a core tool needs to exist at all; they degrade when absent.
OPTIONAL = {
    "eidesoul/engine/tools/web_search.py": {
        "ddgs": "search dependency of the core web_search tool; no backend is named in config",
        "searx": "self-hosted SearXNG endpoint the operator may configure",
    }
}
VENDORS = re.compile(r"\b(google|whatsapp|neonize|telegram|slack|discord|anthropic|gemini)\b")


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _scope() -> list[Path]:
    files = list(ENGINE.rglob("*.py")) + list(ADMIN.rglob("*.py"))
    files.append(ROOT / "eidesoul" / "__main__.py")
    files.append(ROOT / "Dockerfile")
    return [path for path in files if path.is_file()]


BANNED_WORDS = re.compile(
    r"(?<![\w.])(yang|dan|atau|dengan|untuk|tidak|sudah|belum|karena|juga|kalau|saja|bisa|harus|"
    r"dari|itu|ini|kamu|aku|dia|kita|mereka|tetapi|sangat|sekali|banget|gitu|gini|wkwk|"
    r"anjir|goblok|tolong|silakan|maaf|terima|kasih)(?![\w.])",
    re.IGNORECASE,
)
DEFAULTS = re.compile(
    r"(language\s*[:=]\s*[\"']id[\"'])|(\bor\s+[\"']id[\"'])|(id-ID)|(Asia/)|(TZ=)"
)
CLOCK = re.compile(r"(date\.today\(|time\.localtime\(|time\.gmtime\()")
TABLES = re.compile(
    r"\b(FAMILIES|AUTHORIZATION_TOOLS|DESTRUCTIVE_ACTIONS|PROVIDES_KEYS|SIDE_EFFECTING)\b"
)
NON_ASCII = re.compile(r"[^\x00-\x7f]")


def _is_letter(char: str) -> bool:
    import unicodedata

    return unicodedata.category(char).startswith("L")


@dataclass
class Hit:
    rule: int
    path: str
    line: int
    message: str


def scan() -> list[Hit]:
    hits: list[Hit] = []
    for path in _scope():
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = _rel(path)
        for number, line in enumerate(text.splitlines(), 1):
            if any(_is_letter(char) for char in line if ord(char) > 127):
                hits.append(Hit(1, rel, number, "non-ASCII letter"))
            if BANNED_WORDS.search(line) and not line.lstrip().startswith("#"):
                hits.append(Hit(2, rel, number, "banned culture word"))
            if DEFAULTS.search(line):
                hits.append(Hit(4, rel, number, "language/locale/zone default"))
            if CLOCK.search(line):
                hits.append(Hit(8, rel, number, "clock read outside clock.py"))
            if TABLES.search(line):
                hits.append(Hit(6, rel, number, "closed name table"))
            for match in VENDORS.finditer(line):
                allowed = SPINE.get(match.group(1).lower(), "")
                optional = OPTIONAL.get(rel, {}).get(match.group(1).lower(), "")
                if not allowed and not optional:
                    hits.append(Hit(3, rel, number, f"vendor name '{match.group(1)}'"))

    for catalog in sorted(LANG.glob("*.yaml")):
        if not (SAFETY / f"{catalog.stem}.json").is_file():
            hits.append(Hit(5, _rel(catalog), 0, "no safety pack"))

    templates = (ADMIN / "templates.py").read_text(encoding="utf-8")
    if re.search(r'raw\["plugins"\]\s*=\s*\{', templates) and not re.search(
        r'raw\["plugins"\]\s*=\s*\{\}\s*$', templates, re.MULTILINE
    ):
        hits.append(Hit(7, "eidesoul/admin/templates.py", 0, "template ships a plugin map"))
    for handler in _handlers():
        missing = [name for name in ("target", "approval_class") if name not in handler]
        if missing:
            hits.append(
                Hit(6, _rel(_handler_file(handler["name"])), 0, f"handler lacks {missing}")
            )
    return hits


_HANDLER_FILES: dict[str, Path] = {}


def _handler_file(name: str) -> Path:
    return _HANDLER_FILES.get(name, ENGINE / "tools" / f"{name}.py")


def _handlers() -> list[dict]:
    found: list[dict] = []
    for path in (ENGINE / "tools").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign) or node.targets[0].__class__ is not ast.Name:
                continue
            if not (isinstance(node.targets[0], ast.Name) and node.targets[0].id == "TOOL"):
                continue
            call = node.value
            if not isinstance(call, ast.Call):
                continue
            keys = {kw.arg for kw in call.keywords if kw.arg}
            name = next(
                (kw.value.value for kw in call.keywords if kw.arg == "name"
                 and isinstance(kw.value, ast.Constant)),
                path.stem,
            )
            _HANDLER_FILES[str(name)] = path
            found.append({"name": str(name), **{key: True for key in keys}})
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="neutral-core gate (ADR-0033 §6)")
    parser.add_argument("--check", action="store_true", help="exit 1 on any hit")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    hits = scan()
    if args.json:
        print(json.dumps([hit.__dict__ for hit in hits], indent=2))
    else:
        for hit in hits:
            print(f"{hit.path}:{hit.line}: rule {hit.rule}: {hit.message}")
        print(f"neutral: {len(hits)} hit(s)")
    return 1 if hits and args.check else 0


if __name__ == "__main__":
    sys.exit(main())
