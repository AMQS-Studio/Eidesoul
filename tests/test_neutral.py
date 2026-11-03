"""The neutral-core gate runs in the suite (ADR-0033 §6)."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_core_is_neutral() -> None:
    result = subprocess.run(
        [sys.executable, "-P", str(ROOT / "scripts" / "neutral.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
