"""ADR home tool (ADR-0009): check structure and scaffold new records.

English is the language of all ADRs (ADR-0001 §4.2.1).
Gate: `python -P scripts/adr.py --check`.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SOURCE_LANG = "en"
FOLDERS = ("root", "engine", "web")

CANONICAL = (
    "Context",
    "Decision drivers",
    "Options considered",
    "Decision",
    "Configuration",
    "Metrics and gates",
    "Acceptance criteria",
    "Risks and mitigations",
    "Interaction with other ADRs",
    "Code references",
)
HEADER_KEYS = ("Status", "Date", "Deciders", "Context", "Supersedes", "Related", "Implementation")
IMPLEMENTASI = ("implemented", "partial", "draft")
NAME_RE = re.compile(r"^(\d{4})-([a-z0-9][a-z0-9-]*)\.md$")
HEADING_RE = re.compile(r"^## (\d+)\. (.+)$")
ADR_FULL_RE = re.compile(r"ADR-(?:([a-z]+)/)?(\d{4})(?:\s*\(?§(\d+))?")
BARE_REF_RE = re.compile(r"§(\d+)")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})(.*)$")
FOOTER_RE = re.compile(r"^\*This ADR lives at `([^`]+)`\.\*$")
LEGACY_MARK = re.compile(r"kolom lama|legacy", re.IGNORECASE)
SQL_TABLE_RE = re.compile(
    r"^\s*(?:CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)?([a-z_][a-z0-9_]*)\s*\("
)
YAML_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:")
KEY_RE = re.compile(r"^\s{0,10}([a-z_][a-z0-9_]*)\s*:")
COLUMN_RE = re.compile(
    r"^\s{1,10}([a-z_][a-z0-9_]*)\s+"
    r"(uuid|text|integer|int|bigint|boolean|bool|timestamptz|timestamp|jsonb|json|real|numeric|"
    r"smallint|float|varchar)\b"
)
MARKER_RE = re.compile(r"^(\s*(?:>\s*|[-*]\s+|\d+\.\s+))(.*)$")
HEADING_ANY_RE = re.compile(r"^(#{1,6})\s+(.*)$")
HEADER_LABEL_RE = re.compile(r"^- \*\*([^*]+):\*\* (.*)$")
ALPHA_RE = re.compile(r"[^\W\d_]")
CODE_RE = re.compile(
    r"`[^`]*`|\[[^\]]*\]\([^)]*\)|https?://\S+|ADR-(?:[a-z]+/)?\d{4}(?:[/,]\d{4})*|§\d+(?:\.\d+)*"
)
NUMBERED_HEADING_RE = re.compile(r"^(\d+)\.\s*(.*)$")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
PLACEHOLDER_RE = re.compile(r"\[?\[?\s*(\d+)\s*\]\]*")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def adr_dir(root: Path) -> Path:
    return root / "docs" / "v0.1.0" / "adr"


def adr_files(root: Path) -> list[Path]:
    """All English ADR files across root, engine, and web folders."""
    home = adr_dir(root)
    result: list[Path] = []
    for sub in ("", "engine", "web"):
        folder = home / sub if sub else home
        if folder.is_dir():
            result.extend(sorted(p for p in folder.glob("*.md") if p.is_file()))
    return result


def rel_name(root: Path, path: Path) -> str:
    return path.relative_to(adr_dir(root)).as_posix()


def folder_of(rel: str) -> str:
    if "/" in rel:
        return rel.split("/", 1)[0]
    return "root"


def sections(text: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    for line in text.splitlines():
        fm = FENCE_RE.match(line)
        if fm:
            fence = fm.group(1)
            char = fence[0]
            length = len(fence)
            if not in_fence:
                in_fence = True
                fence_char = char
                fence_len = length
            elif char == fence_char and length >= fence_len:
                in_fence = False
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(line)
        if m:
            result.append((int(m.group(1)), m.group(2).strip()))
    return result


def fence_blocks(text: str) -> list[list[tuple[int, str]]]:
    blocks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    for idx, line in enumerate(text.splitlines(), start=1):
        fm = FENCE_RE.match(line)
        if fm:
            fence = fm.group(1)
            char = fence[0]
            length = len(fence)
            if not in_fence:
                in_fence = True
                fence_char = char
                fence_len = length
                current = [(idx, line)]
            elif char == fence_char and length >= fence_len:
                current.append((idx, line))
                blocks.append(current)
                current = []
                in_fence = False
            continue
        if in_fence:
            current.append((idx, line))
    return blocks


def definition_names(line: str) -> list[str]:
    names: list[str] = []
    m_yaml = YAML_KEY_RE.match(line)
    if m_yaml:
        names.append(m_yaml.group(1))
    m_sql = SQL_TABLE_RE.match(line)
    if m_sql:
        names.append(m_sql.group(1))
    m_col = COLUMN_RE.match(line)
    if m_col:
        names.append(m_col.group(1))
    m_key = KEY_RE.match(line)
    if m_key:
        names.append(m_key.group(1))
    return list(dict.fromkeys(names))


def fence_contexts(block: list[tuple[int, str]]) -> dict[int, str]:
    contexts: dict[int, str] = {}
    current_table: str | None = None
    for idx, line in block:
        m_sql = SQL_TABLE_RE.match(line)
        if m_sql:
            current_table = m_sql.group(1)
            contexts[idx] = f"table:{current_table}"
            continue
        m_col = COLUMN_RE.match(line)
        if m_col:
            contexts[idx] = f"column:{m_col.group(1)} (in {current_table or 'table'})"
            continue
        m_yaml = YAML_KEY_RE.match(line)
        if m_yaml:
            contexts[idx] = f"yaml:{m_yaml.group(1)}"
            continue
        contexts[idx] = "fence"
    return contexts


def fence_report(text: str) -> list[str]:
    problems: list[str] = []
    for block in fence_blocks(text):
        for idx, line in block[1:-1]:
            if HEADING_ANY_RE.match(line):
                problems.append(f"baris {idx}: heading di dalam code fence ({line.strip()})")
    return problems


def _parse_synonym_row(cells: list[str], synonyms: dict[str, list[tuple[str, str]]]) -> None:
    if len(cells) < 4:
        return
    canonical = cells[0].strip("`").strip()
    raw_synonyms = cells[2]
    if not raw_synonyms or raw_synonyms == "—":
        return
    for item in raw_synonyms.split(","):
        item = item.strip()
        if not item:
            continue
        scope = "any"
        if "(" in item and item.endswith(")"):
            item, scope_raw = item[:-1].split("(", 1)
            item = item.strip()
            scope = scope_raw.strip()
        term = item.strip("`").strip()
        if term:
            synonyms.setdefault(canonical, []).append((term, scope))


def register_synonyms(root: Path) -> tuple[dict[str, list[tuple[str, str]]], int]:
    markers = ("Canonical term register", "Register istilah kanonik")
    for path in adr_files(root):
        text = read(path)
        if not any(m in text for m in markers):
            continue
        m_num = NAME_RE.match(path.name)
        owner_num = int(m_num.group(1)) if m_num else 8
        in_table = False
        synonyms: dict[str, list[tuple[str, str]]] = {}
        for line in text.splitlines():
            if any(m in line for m in markers):
                in_table = True
                continue
            if in_table and line.startswith("## "):
                break
            if (
                in_table
                and line.startswith("|")
                and not line.startswith("| Canonical term")
                and not line.startswith("| Istilah kanonik")
                and not line.startswith("|---")
            ):
                cells = [c.strip() for c in line.split("|")[1:-1]]
                _parse_synonym_row(cells, synonyms)
        return synonyms, owner_num
    return {}, 8


def check_numbers(root: Path) -> tuple[dict[int, list[Path]], list[str]]:
    index: dict[int, list[Path]] = {}
    problems: list[str] = []
    for path in adr_files(root):
        m = NAME_RE.match(path.name)
        if not m:
            problems.append(f"{rel_name(root, path)}: nama berkas tidak mengikuti NNNN-slug.md")
            continue
        number = int(m.group(1))
        index.setdefault(number, []).append(path)
    for number, paths in index.items():
        if len(paths) > 1:
            names = ", ".join(rel_name(root, p) for p in paths)
            problems.append(f"nomor {number:04d} duplikat: {names} (nomor unik global)")
    return index, problems


def resolve_ref(
    root: Path, index: dict[int, list[Path]], folder: str, ref_folder: str | None, number: int
) -> tuple[Path | None, str | None]:
    paths = index.get(number)
    if not paths:
        return None, f"rujukan ADR-{number:04d} tidak ada"
    if ref_folder is not None:
        if ref_folder not in FOLDERS:
            return None, f"folder ADR-{ref_folder}/ tidak dikenal"
        for path in paths:
            if folder_of(rel_name(root, path)) == ref_folder:
                return path, None
        return None, f"rujukan ADR-{ref_folder}/{number:04d} tidak ada"
    if len(paths) == 1:
        return paths[0], None
    own = [path for path in paths if folder_of(rel_name(root, path)) == folder]
    if own:
        return own[0], None
    names = ", ".join(rel_name(root, path) for path in paths)
    return None, f"ADR-{number:04d} ambigu ({names}); tulis ADR-<folder>/{number:04d}"


def check_sections(name: str, titles: list[str], seq: list[int]) -> list[str]:
    problems: list[str] = []
    if seq != list(range(1, len(seq) + 1)):
        problems.append(f"{name}: nomor bagian tidak 1..N monoton")
    missing = [c for c in CANONICAL if c not in titles]
    if missing:
        problems.append(f"{name}: bagian kanonik hilang: {', '.join(missing)}")
    if titles and titles[-1] != "Code references":
        problems.append(f"{name}: bagian terakhir bukan Code references")
    anchors = ("Decision drivers", "Decision", "Code references")
    if all(a in titles for a in anchors):
        drivers, decision, last = (titles.index(a) for a in anchors)
        for title in [t for t in titles if t not in CANONICAL]:
            index = titles.index(title)
            if not drivers < index < decision and not decision < index < last:
                problems.append(f"{name}: bagian tambahan '{title}' di luar dua wilayah izin")
    return problems


def check_header(name: str, text: str) -> list[str]:
    problems: list[str] = []
    for key in HEADER_KEYS:
        if f"- **{key}:" not in text:
            problems.append(f"{name}: header '{key}' hilang")
    impl = re.search(r"- \*\*Implementation:\*\* (\w+)", text)
    if not impl or impl.group(1) not in IMPLEMENTASI:
        problems.append(f"{name}: Implementation harus salah satu dari {'|'.join(IMPLEMENTASI)}")
    if re.search(r"^## .*Amendments", text, re.M):
        problems.append(f"{name}: Amendments tidak boleh jadi bagian")
    return problems


def check_refs(
    rel: str, text: str, seq: list[int], index: dict[int, list[Path]], root: Path
) -> list[str]:
    problems: list[str] = []
    stripped = re.sub(r"`[^`]*`", "", text)
    folder = folder_of(rel)
    for m in ADR_FULL_RE.finditer(stripped):
        ref_folder, number, wanted = m.group(1), int(m.group(2)), m.group(3)
        target, err = resolve_ref(root, index, folder, ref_folder, number)
        if err:
            problems.append(f"{rel}: {err}")
            continue
        if target is None:
            continue
        if wanted is not None:
            label = f"{ref_folder}/{number:04d}" if ref_folder else f"{number:04d}"
            if int(wanted) not in [n for n, _ in sections(read(target))]:
                problems.append(f"{rel}: rujukan ADR-{label} §{wanted} tidak ada")
    bare = ADR_FULL_RE.sub("", stripped)
    bare = re.sub(r"\(\s*\)\s*§\d+", "", bare)
    for m in BARE_REF_RE.finditer(bare):
        if int(m.group(1)) not in seq:
            problems.append(f"{rel}: rujukan §{m.group(1)} tidak ada")
    return problems


def check_synonyms(name: str, text: str, synonyms: dict[str, list[tuple[str, str]]]):
    problems: list[str] = []

    def banned(identifier: str, term: str, number: int) -> str:
        return (
            f"{name}: istilah kanonik '{identifier}' "
            f"dilanggar oleh '{term}' (ADR-{number:04d})"
        )

    for block in fence_blocks(text):
        contexts = fence_contexts(block)
        for idx, line in block:
            if LEGACY_MARK.search(line):
                continue
            ctx = contexts.get(idx, "")
            names = definition_names(line)
            if not names:
                continue
            for identifier, banned_list in synonyms.items():
                for term, scope in banned_list:
                    scoped = (
                        "nama role",
                        "role name",
                        "kolom",
                        "column",
                        "in soul.yaml",
                        "dalam soul.yaml",
                    )
                    if scope in scoped and scope not in ctx:
                        continue
                    for defined in names:
                        if defined.lower() == term.lower():
                            problems.append(banned(identifier, term, 8))
    return problems


def check_footer(rel: str, text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return [f"{rel}: berkas kosong"]
    last = lines[-1]
    m = FOOTER_RE.match(last)
    if not m:
        return [f"{rel}: baris penutup tidak cocok format *This ADR lives at `docs/...`.*"]
    expected = f"docs/v0.1.0/adr/{rel}"
    if m.group(1) != expected:
        return [f"{rel}: path footer '{m.group(1)}' tidak sama dengan '{expected}'"]
    return []


def check_doc(
    rel: str,
    path: Path,
    index: dict[int, list[Path]],
    synonyms: dict[str, list[tuple[str, str]]],
    root: Path,
) -> list[str]:
    text = read(path)
    secs = sections(text)
    seq = [n for n, _ in secs]
    titles = [t for _, t in secs]
    problems: list[str] = []
    problems += check_sections(rel, titles, seq)
    problems += check_header(rel, text)
    problems += [f"{rel}: {p}" for p in fence_report(text)]
    problems += check_refs(rel, text, seq, index, root)
    problems += check_synonyms(rel, text, synonyms)
    problems += check_footer(rel, text)
    return problems


def count_implementasi(root: Path) -> dict[str, int]:
    counts = {key: 0 for key in IMPLEMENTASI}
    for path in adr_files(root):
        text = read(path)
        m = re.search(r"- \*\*Implementation:\*\* (\w+)", text)
        if m and m.group(1) in counts:
            counts[m.group(1)] += 1
    return counts


def cmd_check(root: Path) -> int:
    index, problems = check_numbers(root)
    synonyms, _owner_num = register_synonyms(root)
    all_files = adr_files(root)
    by_folder: dict[str, int] = {f: 0 for f in FOLDERS}
    for path in all_files:
        rel = rel_name(root, path)
        by_folder[folder_of(rel)] += 1
        problems += check_doc(rel, path, index, synonyms, root)
    if problems:
        print(f"GAGAL: {len(problems)} masalah pada {len(all_files)} ADR")
        for p in problems:
            print(f" - {p}")
        return 1
    counts = count_implementasi(root)
    summary_parts = [f"{k}={v}" for k, v in counts.items()]
    folder_parts = [f"{k}={v}" for k, v in by_folder.items()]
    print(
        f"OK: {len(all_files)}/{len(all_files)} ADR lolos check; "
        f"folder {', '.join(folder_parts)}; "
        f"{' '.join(summary_parts)}"
    )
    return 0


SCAFFOLD = """# ADR-{number:04d}: {title}

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:**
- **Supersedes:** —
- **Related:**
- **Implementation:** draft

---

## 1. Context

## 2. Decision drivers

## 3. Options considered

## 4. Decision

## 5. Configuration

## 6. Metrics and gates

## 7. Acceptance criteria

## 8. Risks and mitigations

## 9. Interaction with other ADRs

## 10. Code references

---

*This ADR lives at `{filename}`.*
"""


def cmd_new(root: Path, title: str, sub: str = "root") -> int:
    index, problems = check_numbers(root)
    if problems:
        print("GAGAL: perbaiki masalah struktur dulu")
        for p in problems:
            print(f" - {p}")
        return 1
    number = max(index) + 1 if index else 1
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:64]
    base = adr_dir(root) if sub == "root" else adr_dir(root) / sub
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{number:04d}-{slug}.md"
    if path.exists():
        print(f"GAGAL: {path} sudah ada")
        return 1
    rel = f"docs/v0.1.0/adr/{rel_name(root, path)}"
    path.write_text(
        SCAFFOLD.format(
            number=number,
            title=title,
            filename=rel,
        ),
        encoding="utf-8",
    )
    print(f"DIBUAT {path.relative_to(root)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADR home tool (check, new)")
    parser.add_argument("--root", default=str(ROOT), help="repository root (default: project root)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="run the structure gate")
    group.add_argument("--new", metavar="TITLE", help="scaffold the next ADR")
    parser.add_argument(
        "--dir",
        choices=FOLDERS,
        default="root",
        help="folder for --new: root (cross-cutting, default), engine, or web",
    )
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.check:
        return cmd_check(root)
    if args.new:
        return cmd_new(root, args.new, args.dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
