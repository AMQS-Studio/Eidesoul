"""Per-directory pyright ratchet (ADR-0031 §4.7).

The baseline records how many type errors each directory carries today; the gate fails only when a
directory grows. Lowering a baseline is a commit of its own. The baseline is empty, so nothing is
allowed: any diagnostic fails the gate.

Run it as `python -P scripts/types.py --check`: `-P` keeps this file's directory off `sys.path`,
so a module named `types` cannot shadow the standard library one that `argparse` imports.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).resolve().parent / "types-baseline.json"


def key_of(relative: str) -> str:
    """`monoagens/engine/agent.py` -> `monoagens/engine`; `scripts/types.py` -> `scripts`."""
    parts = Path(relative).parts
    if len(parts) >= 2 and parts[0] == "monoagens":
        return "/".join(parts[:2])
    return parts[0] if parts else ""


def collect() -> dict[str, int]:
    """Error count per key from one `pyright --outputjson` run."""
    binary = Path(sys.executable).parent / "pyright"
    completed = subprocess.run(
        [str(binary), "--outputjson"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout or "{}")
    counts: dict[str, int] = {}
    for diagnostic in payload.get("generalDiagnostics", []):
        if str(diagnostic.get("severity") or "") != "error":
            continue
        raw = str(diagnostic.get("file") or "")
        try:
            relative = Path(raw).resolve().relative_to(ROOT).as_posix()
        except (ValueError, OSError):
            relative = Path(raw).as_posix()
        key = key_of(relative)
        counts[key] = counts.get(key, 0) + 1
    return counts


def write_baseline(counts: dict[str, int]) -> None:
    BASELINE.write_text(
        json.dumps(dict(sorted(counts.items())), indent=2) + "\n", encoding="utf-8"
    )


def read_baseline() -> dict[str, int]:
    if not BASELINE.is_file():
        return {}
    loaded = json.loads(BASELINE.read_text(encoding="utf-8"))
    return {str(key): int(value) for key, value in loaded.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare pyright errors against the committed per-directory baseline."
    )
    parser.add_argument("--check", action="store_true", help="fail when a key grows (default)")
    parser.add_argument("--update", action="store_true", help="rewrite the baseline")
    args = parser.parse_args(argv)

    counts = collect()
    if args.update:
        write_baseline(counts)
        for key in sorted(counts):
            print(f"{key} {counts[key]} (baseline {counts[key]})", flush=True)
        print(f"baseline written: {BASELINE.relative_to(ROOT)}", flush=True)
        return 0

    baseline = read_baseline()
    failed = False
    for key in sorted(set(baseline) | set(counts)):
        count = counts.get(key, 0)
        allowed = baseline.get(key, 0)
        if count > allowed:
            print(f"INCREASE {key} {count} (baseline {allowed})", flush=True)
            failed = True
            continue
        print(f"{key} {count} (baseline {allowed})", flush=True)
    total = sum(counts.values())
    print(f"pyright: {total} {'error' if total == 1 else 'errors'}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
