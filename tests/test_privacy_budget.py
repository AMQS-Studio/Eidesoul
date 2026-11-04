"""ADR-0038 §5.1: the entity layer fits its memory and disk budget."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = r"""
import json
import sys

from eidesoul.engine.memory.evidence import EntityDetector, load_pack


def status_kb(key: str) -> int:
    with open("/proc/self/status", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith(key):
                return int(line.split()[1])
    raise RuntimeError(f"{key} is missing")


pack = load_pack("id")
before = status_kb("VmRSS:")
evidence = EntityDetector().detect("Nama saya Budi Santoso, tinggal di Jakarta.", pack)
after = status_kb("VmRSS:")
print(json.dumps({
    "before_kb": before,
    "after_kb": after,
    "peak_kb": status_kb("VmHWM:"),
    "evidence": len(evidence),
    "torch": "torch" in sys.modules,
    "transformers": "transformers" in sys.modules,
}))
"""


def test_entity_layer_stays_inside_the_incremental_and_cold_budget() -> None:
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    measured = json.loads(result.stdout.strip().splitlines()[-1])

    assert measured["evidence"] > 0
    assert measured["after_kb"] - measured["before_kb"] <= 200 * 1024
    assert measured["peak_kb"] <= 700 * 1024
    assert measured["torch"] is False
    assert measured["transformers"] is False
