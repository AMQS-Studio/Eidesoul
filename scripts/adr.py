#!/usr/bin/env python3
"""ADR home tool: structure check, scaffold, and the Indonesian derivative.

The ADR set lives in `docs/v<version>/adr/` and is the single live home for decisions (ADR-0009).
English is the source language; each folder keeps its Indonesian copies in its own `id/` subfolder
(`adr/id/`, `adr/engine/id/`, `adr/web/id/`). This script is the gate: it never touches the network
and only reaches for a model when asked.

Checks enforced by `--check` (ADR-0009 §4.4):
  * exactly one `docs/*/adr` directory; an ADR sits at its root (cross-cutting) or under `engine/`
    or `web/` (surface)
  * file names `NNNN-slug.md`; a number is **unique across the whole home** and never reused, so a
    move leaves a gap (`engine/` has no `0009`: the ADR home moved to the root) and the foundations
    ADR is `0000`, the one number below `0001`
  * every `ADR-NNNN` reference resolves; `ADR-<folder>/NNNN` is accepted as an explicit form, and a
    bare number only needs a folder if a collision ever appears
  * canonical English sections present, numbered 1..N, `Code references` last
  * extra sections only between `Decision drivers`/`Decision`, or after `Decision`
  * header block carries Status/Date/Deciders/Context/Supersedes/Related/Implementation
  * `Implementation` is one of implemented|partial|draft
  * `Amendments` lives only as a header line, never as a section
  * balanced fences, no heading inside a fence
  * every `§N` reference resolves; `ADR-XXXX §N` resolves against that ADR
  * register synonyms (ADR-0009 §4.12) never name a YAML key or SQL column they are banned for,
    with the scope taken from the register's own parenthetical hint
  * trailing footer points at the ADR's own path

`--translate-check` verifies that every hand-written Indonesian derivative under `adr/id/` still
carries the hash of its English source. There is no machine translator: the derivative is written by
a person and the hash is the gate; the derivative header records the source hash.
"""


import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SOURCE_LANG = "en"
DERIVATIVE_DIR = "id"
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
DERIVATIVE_RE = re.compile(
    r"^> (?:Terjemahan turunan|Naskah asli Indonesia) — jangan sunting; "
    r"sumber: (\S+) \(sha256: ([0-9a-f]{12})\)$"
)
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

# Structure tables for the Indonesian copy: English source -> Indonesian derivative.
HEADING_ID = {
    "Context": "Konteks",
    "Decision drivers": "Decision drivers",
    "Options considered": "Opsi yang dipertimbangkan",
    "Decision": "Keputusan",
    "Configuration": "Konfigurasi",
    "Metrics and gates": "Metrik dan gate",
    "Risks and mitigations": "Risiko dan mitigasi",
    "Interaction with other ADRs": "Interaksi dengan ADR lain",
    "Code references": "Referensi kode",
    "Definitions": "Definisi",
    "Schema": "Skema",
    "Resume": "Resume",
    "Shutdown": "Shutdown",
    "Notice": "Notice",
    "Backlog": "Backlog",
    "Staged implementation": "Implementasi bertahap",
}
LABEL_ID = {
    "Context": "Konteks",
    "Date": "Tanggal",
    "Related": "Terkait",
    "Amendments": "Amandemen",
    "Location note": "Catatan lokasi",
    "Implementation": "Implementasi",
}
IMPLEMENTASI_ID = {"partial": "sebagian", "draft": "draft", "implemented": "implemented"}

def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def adr_dir(root: Path) -> Path:
    found = sorted(root.glob("docs/*/adr"))
    return found[0] if found else root / "docs" / "v0.1.0" / "adr"


def adr_files(root: Path) -> list[Path]:
    adr = adr_dir(root)
    files = sorted(adr.glob("*.md"))
    for sub in FOLDERS[1:]:
        files += sorted((adr / sub).glob("*.md"))
    return files


def rel_name(root: Path, path: Path) -> str:
    """Path inside the ADR home: `0009-adr-home.md` or `engine/0016-schema-migration.md`."""
    return path.relative_to(adr_dir(root)).as_posix()


def folder_of(rel: str) -> str:
    """Folder namespace of an ADR: `root` for the ADR home root, else the first path segment."""
    return rel.split("/", 1)[0] if "/" in rel else "root"


def derivative_dir(root: Path) -> Path:
    return adr_dir(root) / DERIVATIVE_DIR


def derivative_of(root: Path, source: Path) -> Path:
    """Copy path inside the source folder's own `id/`: `engine/x.md` -> `engine/id/x.md`."""
    rel = source.relative_to(adr_dir(root))
    return adr_dir(root) / rel.parent / DERIVATIVE_DIR / rel.name


def derivative_files(root: Path) -> list[Path]:
    adr = adr_dir(root)
    files = sorted((adr / DERIVATIVE_DIR).glob("*.md"))
    for sub in FOLDERS[1:]:
        files += sorted((adr / sub / DERIVATIVE_DIR).glob("*.md"))
    return files


def source_of_derivative(root: Path, copy: Path) -> Path:
    """`engine/id/x.md` -> `engine/x.md` and `id/x.md` -> `x.md`."""
    rel = copy.relative_to(adr_dir(root))
    parts = list(rel.parts)
    parts.remove(DERIVATIVE_DIR)
    return adr_dir(root) / Path(*parts)


def sections(text: str) -> list[tuple[int, str]]:
    """Top-level numbered sections, fence-aware."""
    out: list[tuple[int, str]] = []
    inside = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            inside = not inside
            continue
        if inside:
            continue
        m = HEADING_RE.match(line)
        if m:
            out.append((int(m.group(1)), m.group(2).strip()))
    return out


def fence_blocks(text: str) -> list[list[tuple[int, str]]]:
    """Fenced blocks as [(line number, line), ...], so definitions report real lines."""
    blocks: list[list[tuple[int, str]]] = []
    inside, buf, fence = False, [], ""
    for number, line in enumerate(text.splitlines(), 1):
        m = FENCE_RE.match(line)
        if m and not inside:
            inside, buf, fence = True, [], m.group(1)
            continue
        if m and inside and line.startswith(fence):
            blocks.append(buf)
            inside = False
            continue
        if inside:
            buf.append((number, line))
    return blocks


def definition_names(line: str) -> list[str]:
    """Identifiers a fence line *defines*: a YAML key or a SQL column. Comments define nothing."""
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", "--", "//", "/*")):
        return []
    names = []
    key = KEY_RE.match(line)
    if key:
        names.append(key.group(1))
    column = COLUMN_RE.match(line)
    if column:
        names.append(column.group(1))
    return names


def fence_contexts(block: list[tuple[int, str]]) -> dict[int, str]:
    """line number -> enclosing definition context (SQL table name or YAML top-level key)."""
    contexts: dict[int, str] = {}
    table = ""
    top = ""
    for number, line in block:
        sql = SQL_TABLE_RE.match(line)
        if sql and not line.startswith((" ", "\t")):
            table = sql.group(1)
        yaml = YAML_KEY_RE.match(line)
        if yaml:
            top = yaml.group(1)
        contexts[number] = table or top
    return contexts


def fence_report(text: str) -> list[str]:
    problems: list[str] = []
    inside, fence = False, ""
    for number, line in enumerate(text.splitlines(), 1):
        m = FENCE_RE.match(line)
        if m:
            if not inside:
                inside, fence = True, m.group(1)
            elif line.startswith(fence):
                inside, fence = False, ""
            continue
        if "```" in line or "~~~" in line:
            problems.append(f"baris {number}: pagar tidak seimbang")
        if inside and re.match(r"^#{1,4} ", line):
            problems.append(f"baris {number}: heading di dalam fence")
    if inside:
        problems.append("fence tidak ditutup")
    return problems


def register_synonyms(root: Path) -> tuple[dict[str, list[tuple[str, str]]], int]:
    """Map forbidden synonym -> [(canonical term, scope)] plus the count of prose-only rows.

    Scope comes from the parenthetical hint in the register's synonym cell: `nama role` -> `roles`
    (only checked inside a `roles:` block), anything mentioning a column -> `sql` (only checked on
    SQL column definitions), no hint -> `fence` (checked on every definition line). Hints that name
    an entity (e.g. "untuk memories") stay prose: the script reports them as ungated instead of
    guessing which direction the ban runs.
    """
    register = adr_dir(root) / "0009-adr-home.md"
    table: dict[str, list[tuple[str, str]]] = {}
    prose_rows = 0
    if not register.is_file():
        return table, prose_rows
    for line in read(register).splitlines():
        if not line.startswith("|") or line.startswith("|---") or "Sinonim terlarang" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4 or "`" not in cells[-1]:
            continue
        terms = re.findall(r"`([^`]+)`", cells[0]) or [cells[0]]
        row_prose = False
        for chunk in re.split(r",\s*", cells[-1]):
            zipped = re.search(r"`([^`]+)`\s*(?:\(([^)]*)\))?", chunk)
            if not zipped:
                continue
            identifier, hint = zipped.group(1), (zipped.group(2) or "").lower()
            words = set(re.findall(r"[a-z_]+", hint))
            if not hint:
                scope = "fence"
            elif "role" in words:
                scope = "roles"
            elif "kolom" in words:
                scope = "sql"
            else:
                scope = "prose"
                row_prose = True
            table.setdefault(identifier, []).append((terms[0], scope))
        prose_rows += 1 if row_prose else 0
    return table, prose_rows


def check_numbers(root: Path) -> tuple[dict[int, list[Path]], list[str]]:
    """Number -> the ADR that carries it (one entry: numbering is global and chronological).

    A number is unique across the whole home and is never reused, so moving an ADR between folders
    leaves a gap on purpose instead of renumbering the rest. `0000` is the foundations ADR: the one
    number below `0001`, so the chronological series still starts at `0001`.
    """
    index: dict[int, list[Path]] = {}
    problems: list[str] = []
    dirs = sorted(root.glob("docs/*/adr"))
    if len(dirs) != 1:
        problems.append(f"harus ada tepat satu docs/*/adr, ditemukan {len(dirs)}")
    files = adr_files(root)
    if not files:
        problems.append("tidak ada ADR di docs/*/adr")
    for path in files:
        rel = rel_name(root, path)
        m = NAME_RE.match(path.name)
        if not m:
            problems.append(f"{rel}: nama harus NNNN-slug.md")
            continue
        number = int(m.group(1))
        for other in index.get(number, []):
            problems.append(
                f"{rel}: nomor {number:04d} sudah dipakai {rel_name(root, other)} "
                "(nomor unik global)"
            )
        index.setdefault(number, []).append(path)
    return index, problems


def resolve_ref(
    root: Path, index: dict[int, list[Path]], folder: str, ref_folder: str | None, number: int
) -> tuple[Path | None, str | None]:
    """Resolve `ADR-NNNN` / `ADR-<folder>/NNNN` to a file, or return the reason it fails."""
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
    # Only reachable if numbering broke its global uniqueness (reported by check_numbers).
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
        return f"{name}: '{identifier}' terlarang ({term}), baris {number}"

    for block in fence_blocks(text):
        contexts = fence_contexts(block)
        for number, line in block:
            if LEGACY_MARK.search(line):
                continue
            context = contexts.get(number, "")
            for identifier in definition_names(line):
                for term, scope in synonyms.get(identifier, ()):
                    sql_column = bool(COLUMN_RE.match(line))
                    role_block = context in ("roles", "role")
                    if scope == "fence" or (scope == "sql" and sql_column) or (
                        scope == "roles" and role_block
                    ):
                        problems.append(banned(identifier, term, number))
    return problems


def check_footer(rel: str, text: str) -> list[str]:
    footers = [line for line in text.splitlines() if FOOTER_RE.match(line)]
    if not footers:
        return [f"{rel}: footer 'This ADR lives at ...' hilang"]
    if len(footers) > 1:
        return [f"{rel}: footer muncul {len(footers)} kali"]
    if f"adr/{rel}" not in footers[0]:
        return [f"{rel}: footer menunjuk path yang salah: {footers[0]}"]
    return []


def check_doc(
    root: Path, path: Path, index: dict[int, list[Path]], synonyms: dict[str, list[tuple[str, str]]]
) -> list[str]:
    rel = rel_name(root, path)
    text = read(path)
    secs = sections(text)
    titles, seq = [t for _, t in secs], [n for n, _ in secs]
    problems = check_sections(rel, titles, seq)
    problems += check_header(rel, text)
    problems += [f"{rel}: {p}" for p in fence_report(text)]
    problems += check_refs(rel, text, seq, index, root)
    problems += check_synonyms(rel, text, synonyms)
    problems += check_footer(rel, text)
    return problems


def count_implementasi(root: Path) -> dict[str, int]:
    counts = {key: 0 for key in IMPLEMENTASI}
    for path in adr_files(root):
        m = re.search(r"- \*\*Implementation:\*\* (\w+)", read(path))
        if m and m.group(1) in counts:
            counts[m.group(1)] += 1
    return counts


def cmd_check(root: Path) -> int:
    index, problems = check_numbers(root)
    synonyms, prose_rows = register_synonyms(root)
    files = adr_files(root)
    for path in files:
        problems += check_doc(root, path, index, synonyms)
    if problems:
        print(f"GAGAL: {len(problems)} masalah pada {len(files)} ADR")
        for p in problems:
            print(f" - {p}")
        return 1
    counts = count_implementasi(root)
    gated = sum(1 for rows in synonyms.values() if any(s != "prose" for _, s in rows))
    summary = " ".join(f"{key}={value}" for key, value in counts.items())
    folders = ", ".join(
        f"{folder}={sum(1 for p in files if folder_of(rel_name(root, p)) == folder)}"
        for folder in FOLDERS
    )
    print(
        f"OK: {len(files)}/{len(files)} ADR lolos check; folder {folders}; {summary}; "
        f"sinonim digerbang={gated} baris prosa={prose_rows}"
    )
    return 0


SCAFFOLD = """# ADR-{number:04d}: {title}

- **Status:** accepted
- **Date:** (decision date, ISO YYYY-MM-DD)
- **Deciders:** (owner and reviewers)
- **Context:** (which ADRs or designs this follows from)
- **Supersedes:** —
- **Related:** (other relevant ADRs)
- **Implementation:** draft — (concrete evidence, or the work that is still missing)
- **Location note:** ADRs live in `{home}/`; cross-cutting decisions at its root, surface decisions
  under `{folder}/`. There is no second copy.

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

*This ADR lives at `{home}/{filename}`.*
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
    path.write_text(
        SCAFFOLD.format(
            number=number,
            title=title,
            filename=rel_name(root, path),
            home=adr_dir(root).relative_to(root).as_posix(),
            folder=sub,
        ),
        encoding="utf-8",
    )
    print(f"DIBUAT {path.relative_to(root)}")
    return 0


def cmd_translate_check(root: Path) -> int:
    problems: list[str] = []
    files = derivative_files(root)
    for path in files:
        text = read(path)
        rel = path.relative_to(adr_dir(root)).as_posix()
        head = text.splitlines()[0] if text else ""
        m = DERIVATIVE_RE.match(head)
        source = source_of_derivative(root, path)
        source_rel = source.relative_to(adr_dir(root)).as_posix()
        if not m or m.group(1) != source_rel or not source.is_file():
            problems.append(f"{rel}: turunan tanpa sumber/hash")
            continue
        digest = hashlib.sha256(read(source).encode("utf-8")).hexdigest()[:12]
        if digest != m.group(2):
            stale = f"{m.group(2)} != {digest}"
            problems.append(f"{rel}: turunan basi ({stale})")
    print(f"TURUNAN {len(files)} berkas, {len(problems)} masalah")
    for p in problems:
        print(f" - {p}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADR home tool (check, new, translate-check)")
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
    group.add_argument("--translate-check", action="store_true", help="verify derivative hashes")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.check:
        return cmd_check(root)
    if args.new:
        return cmd_new(root, args.new, args.dir)
    return cmd_translate_check(root)


if __name__ == "__main__":
    sys.exit(main())
