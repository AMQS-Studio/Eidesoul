"""ADR-0038 §5.1: the default privacy stack fits its memory and disk budget."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = r"""
import json
import sys

from monoagens.engine import embed
from monoagens.engine.memory.evidence import EntityDetector, SemanticDetector, load_pack


def status_kb(key: str) -> int:
    with open("/proc/self/status", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith(key):
                return int(line.split()[1])
    raise RuntimeError(f"{key} is missing")


pack = load_pack("en")
before = status_kb("VmRSS:")
entity = EntityDetector().detect("Email me at budi@example.com or call +1 555 0100.", pack)
mid = status_kb("VmRSS:")
entity_torch = "torch" in sys.modules
entity_transformers = "transformers" in sys.modules
semantic = SemanticDetector().detect("I stole money from the store", pack)
after = status_kb("VmRSS:")
print(json.dumps({
    "before_kb": before,
    "entity_kb": mid,
    "after_kb": after,
    "peak_kb": status_kb("VmHWM:"),
    "entity_evidence": len(entity),
    "semantic_evidence": len(semantic),
    "entity_torch": entity_torch,
    "entity_transformers": entity_transformers,
    "model": embed.encoder() is not None,
}))
"""


def test_default_privacy_stack_stays_inside_the_runtime_and_incremental_budget() -> None:
    env = os.environ.copy()
    env.update(
        {
            "HF_HUB_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    measured = json.loads(result.stdout.strip().splitlines()[-1])

    assert measured["entity_evidence"] > 0
    assert measured["semantic_evidence"] > 0
    assert measured["model"] is True
    assert measured["entity_kb"] - measured["before_kb"] <= 200 * 1024
    assert measured["entity_torch"] is False
    assert measured["entity_transformers"] is False
    assert measured["peak_kb"] <= 1536 * 1024
