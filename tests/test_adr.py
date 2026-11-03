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
    (adr / "web").mkdir(exist_ok=True)  # an empty folder is not tracked by git
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


def test_translate_check_detects_a_stale_or_missing_hash(tmp_path):
    root = _copy_home(tmp_path)
    source = root / "docs" / "v0.1.0" / "adr" / "engine" / "0026-search.md"
    derivative = root / "docs" / "v0.1.0" / "adr" / "engine" / "id" / "0026-search.md"
    import hashlib

    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    derivative.parent.mkdir(parents=True, exist_ok=True)
    derivative.write_text(
        f"> Terjemahan turunan — jangan sunting; sumber: engine/0026-search.md "
        f"(sha256: {digest})\n> Ditulis tangan.\n\n## 1. Konteks\n",
        encoding="utf-8",
    )
    assert _run("--translate-check", root=root).returncode == 0
    derivative.write_text(
        derivative.read_text(encoding="utf-8").replace(digest, "000000000000", 1),
        encoding="utf-8",
    )
    assert _run("--translate-check", root=root).returncode == 1


def test_there_is_no_machine_translator():
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"--translator"' not in source
    assert "opus-mt" not in source
    assert "def translate_text" not in source
    assert "def cmd_translate(" not in source
