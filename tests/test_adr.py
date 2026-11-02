"""ADR home gate: `scripts/adr.py` must accept the shipped set and reject real breakage.

The ADR sources are English; `adr/id/` holds the Indonesian copy. The tool is pure text work for
`--check` (no model, no network), so these tests are cheap and deterministic. Every check runs
against a copy of the ADR home so a failure never edits the repository.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "adr.py"
ADR_DIR = ROOT / "docs" / "v0.1.0" / "adr"


def _run(*args: str, root: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _copy_home(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "docs" / "v0.1.0").mkdir(parents=True)
    shutil.copytree(ADR_DIR, root / "docs" / "v0.1.0" / "adr")
    return root


def test_check_passes_on_the_shipped_set():
    result = _run("--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ADR lolos check" in result.stdout
    assert "implemented=" in result.stdout


def test_check_rejects_a_register_synonym_in_a_definition(tmp_path):
    root = _copy_home(tmp_path)
    target = root / "docs" / "v0.1.0" / "adr" / "engine" / "0016-schema-migration.md"
    text = target.read_text(encoding="utf-8")
    broken = text.replace(
        " kind text not null default 'fact',", " facts text not null default 'fact',", 1
    )
    assert broken != text, "fixture did not change; the DDL line moved"
    target.write_text(broken, encoding="utf-8")
    result = _run("--check", root=root)
    assert result.returncode == 1
    assert "terlarang" in result.stdout


def test_check_rejects_a_section_that_is_not_the_last(tmp_path):
    root = _copy_home(tmp_path)
    target = root / "docs" / "v0.1.0" / "adr" / "engine" / "0026-search.md"
    text = target.read_text(encoding="utf-8")
    target.write_text(text + "\n## 11. Extra note\n", encoding="utf-8")
    result = _run("--check", root=root)
    assert result.returncode == 1
    assert "bagian terakhir" in result.stdout


def test_new_scaffold_appends_the_next_number_and_passes(tmp_path):
    root = _copy_home(tmp_path)
    adr = root / "docs" / "v0.1.0" / "adr"
    before = sorted(p.name for p in adr.glob("*.md"))
    highest = max(int(name[:4]) for name in before)
    result = _run("--new", "Scaffold smoke test", root=root)
    assert result.returncode == 0, result.stdout + result.stderr
    created_files = sorted(adr.glob("*-scaffold-smoke-test.md"))
    assert len(created_files) == 1
    created = created_files[0]
    assert created.name.startswith(f"{highest + 1:04d}-")  # next number, in the root folder
    body = created.read_text(encoding="utf-8")
    assert "- **Implementation:** draft" in body
    assert "## 1. Context" in body
    assert _run("--check", root=root).returncode == 0


def test_new_scaffold_goes_into_the_requested_folder(tmp_path):
    root = _copy_home(tmp_path)
    adr = root / "docs" / "v0.1.0" / "adr"
    result = _run("--new", "Web surface smoke test", "--dir", "web", root=root)
    assert result.returncode == 0, result.stdout + result.stderr
    created = list((adr / "web").glob("*-web-surface-smoke-test.md"))
    assert len(created) == 1
    body = created[0].read_text(encoding="utf-8")
    assert f"adr/web/{created[0].name}" in body  # footer keeps the folder
    assert _run("--check", root=root).returncode == 0


def test_duplicate_number_is_refused(tmp_path):
    root = _copy_home(tmp_path)
    adr = root / "docs" / "v0.1.0" / "adr"
    shutil.copy(adr / "engine" / "0026-search.md", adr / "web" / "0026-search.md")
    result = _run("--check", root=root)
    assert result.returncode != 0
    assert "nomor unik global" in result.stdout


def test_dangling_reference_is_refused(tmp_path):
    root = _copy_home(tmp_path)
    adr = root / "docs" / "v0.1.0" / "adr"
    assert _run("--new", "Dangling probe", "--dir", "web", root=root).returncode == 0
    probe = next((adr / "web").glob("*-dangling-probe.md"))
    probe.write_text(
        probe.read_text(encoding="utf-8").replace(
            "## 1. Context", "## 1. Context\n\nSee ADR-0099 §1 for nothing at all.", 1
        ),
        encoding="utf-8",
    )
    result = _run("--check", root=root)
    assert result.returncode != 0
    assert "ADR-0099" in result.stdout


def test_folder_qualified_reference_resolves(tmp_path):
    root = _copy_home(tmp_path)
    adr = root / "docs" / "v0.1.0" / "adr"
    assert _run("--new", "Qualified probe", "--dir", "web", root=root).returncode == 0
    probe = next((adr / "web").glob("*-qualified-probe.md"))
    probe.write_text(
        probe.read_text(encoding="utf-8").replace(
            "## 1. Context", "## 1. Context\n\nSee ADR-0001 §4.3 and ADR-engine/0016 §4.2.", 1
        ),
        encoding="utf-8",
    )
    assert _run("--check", root=root).returncode == 0


def test_translate_writes_the_indonesian_copy_and_detects_staleness(tmp_path):
    root = _copy_home(tmp_path)
    assert _run("--translate", "all", root=root).returncode == 0
    derivative = root / "docs" / "v0.1.0" / "adr" / "engine" / "id" / "0026-search.md"
    assert derivative.is_file()
    head = derivative.read_text(encoding="utf-8").splitlines()[0]
    assert "sumber: engine/0026-search.md" in head
    assert "(sha256: " in head
    assert _run("--translate-check", root=root).returncode == 0
    source = root / "docs" / "v0.1.0" / "adr" / "engine" / "0026-search.md"
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert _run("--translate-check", root=root).returncode == 1


def _load_tool():
    spec = importlib.util.spec_from_file_location("adr_tool", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_split_long_respects_limit_and_order():
    tool = _load_tool()
    text = "Kalimat pertama. Kalimat kedua yang lebih panjang sedikit. " + "y" * 450
    parts = tool.split_long(text, limit=100)
    assert parts and all(len(part) <= 100 for part in parts)
    assert parts[0].startswith("Kalimat pertama.")
    assert any(part.startswith("y") for part in parts)
    assert tool.split_long("  ") == ["  "]


def test_inline_spans_are_masked_and_restored_completely():
    tool = _load_tool()
    text = "Use `scope_key` and **wajib**, see https://example.org/x"
    masked, tokens = tool.protect_inline(text)
    assert "scope_key" not in masked
    assert "@@" not in masked
    restored, complete = tool.restore_inline(masked, tokens)
    assert complete is True
    assert restored == text


def test_bold_markers_are_masked_but_their_text_is_translatable():
    tool = _load_tool()
    masked, tokens = tool.protect_inline("Dua hal yang **tidak** berubah, pakai `x`.")
    assert "tidak" in masked  # the word reaches the translator
    assert "**" not in masked  # the markers do not
    restored, complete = tool.restore_inline(masked, tokens)
    assert complete is True
    assert restored == "Dua hal yang **tidak** berubah, pakai `x`."


def test_incomplete_restore_is_reported_not_guessed():
    tool = _load_tool()
    restored, complete = tool.restore_inline("text [[0]] and [[7]]", ["**", "**"])
    assert complete is False
    assert "[[" not in restored
    assert "@" not in restored


def test_stub_backend_translates_content_and_keeps_structure():
    tool = _load_tool()
    source = (
        "# ADR-9999: Test\n\n- **Status:** accepted\n\n## 1. Context\n\n"
        "A long English paragraph that must be translated.\n"
    )
    seen: list[str] = []

    def stub(pieces: list[str]) -> list[str]:
        seen.extend(pieces)
        return ["ID(" + piece + ")" for piece in pieces]

    out, s_hits, s_total, c_hits, c_total = tool.translate_text(source, stub)
    assert seen == ["[[0]]: Test", "A long English paragraph that must be translated."]
    assert (c_hits, c_total) == (2, 2)
    assert "ADR-9999" in out
    assert "## 1. Konteks" in out
    assert "- **Status:** accepted" in out
    assert (s_hits, s_total) == (1, 1)


def test_header_labels_and_markers_render_in_indonesian():
    tool = _load_tool()
    source = (
        "- **Context:** earlier ADRs set the stage.\n"
        "> **Note** asserted here.\n"
        "1. The first important step.\n"
        "### Phase 1 - structural gate\n"
        "- **Implementation:** partial — the rest is missing\n"
    )
    seen: list[str] = []

    def stub(pieces: list[str]) -> list[str]:
        seen.extend(pieces)
        return ["ID(" + piece + ")" for piece in pieces]

    out, s_hits, s_total, c_hits, c_total = tool.translate_text(source, stub)
    lines = out.splitlines()
    assert lines[0] == "- **Konteks:** ID(earlier ADRs set the stage.)"
    assert lines[1] == "> ID(**Note** asserted here.)"
    assert lines[2] == "1. ID(The first important step.)"
    assert lines[3] == "### ID(Phase 1 - structural gate)"
    assert lines[4] == "- **Implementasi:** sebagian ID(— the rest is missing)"
    assert (c_hits, c_total) == (5, 5)
    assert len(seen) == 5


def test_backend_never_sees_fences_and_tables_keep_columns():
    tool = _load_tool()
    source = (
        "## 1. Context\n\n"
        "| Fact | Evidence |\n|---|---|\n| Messages stored | `x.py:1` |\n\n"
        "```yaml\nengine:\n  turn: 90\n```\n\n"
        "- A first bullet that needs translation.\n"
    )
    seen: list[str] = []

    def stub(pieces: list[str]) -> list[str]:
        seen.extend(pieces)
        return [f"ID({piece})" for piece in pieces]

    out, _s_hits, _s_total, c_hits, c_total = tool.translate_text(source, stub)
    joined = " ".join(seen)
    assert "engine:" not in joined and "turn: 90" not in joined
    assert "```yaml\nengine:\n  turn: 90\n```" in out
    assert out.count("|") == source.count("|")
    assert "ID(Fact)" in out and "ID(Evidence)" in out
    assert "ID(A first bullet that needs translation.)" in out
    assert (c_hits, c_total) == (3, 3)
