"""Deployment invariants that only show up on a real container boot (runbook)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_hf_cache_is_not_shadowed_by_the_data_dir_bind_mount() -> None:
    """compose binds ./.monoagens over /app/.monoagens; HF_HOME must live outside it."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "./.monoagens:/app/.monoagens" in compose
    match = re.search(r"HF_HOME=([^\s\\]+)", dockerfile)
    assert match is not None, "Dockerfile must set HF_HOME explicitly"
    assert not match.group(1).startswith("/app/.monoagens"), (
        "the baked-in model cache would be hidden by the bind mount"
    )
