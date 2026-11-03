"""Self-knowledge guard: `behavior:<id>`, config/role/registry references, forbidden tokens.

Run it from the repo root::

    python scripts/selfcheck.py --check

Exit code 0 means every `behavior:<id>` resolves, every topic stays under its limit and leaks no
runtime fact, and the engine imports no gateway (ADR-0015 §4.8). `eidesoul selftest` calls the
same `check()` so a stale manifest fails the shipped smoke test too.
"""


import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eidesoul.engine.access.roles import RoleRegistry
from eidesoul.engine.config import (
    ApprovalConfig,
    Config,
    DatabaseConfig,
    LLMConfig,
    Paths,
    SandboxConfig,
    SelfKnowledgeConfig,
)
from eidesoul.engine.self_knowledge import SelfKnowledge, check
from eidesoul.engine.soul import Soul

ENGINE = ROOT / "eidesoul" / "engine"
TOP_LEVEL_CAP = 15
# ADR-0031 §4.1 caps. The allowlist is the four modules this ADR does not split.
LINE_CAPS = {
    "eidesoul/engine/agent.py": 1_800,
    "eidesoul/engine/jobs/automation.py": 2_000,
    "eidesoul/engine/memory/store.py": 2_400,
}
OVER_CAP_ALLOWED = {
    "eidesoul/engine/config.py",
    "eidesoul/engine/stores/affect.py",
    "eidesoul/engine/jobs/cognition.py",
    "eidesoul/engine/schema.py",
}
MODULE_CAP = 800


def size_problems() -> list[str]:
    """ADR-0031 §6: the top level, the three concentrated modules, and the 800-line ceiling."""
    problems: list[str] = []
    top = sorted(path.name for path in ENGINE.glob("*.py"))
    if len(top) > TOP_LEVEL_CAP:
        problems.append(
            f"engine top level holds {len(top)} modules (cap {TOP_LEVEL_CAP}): {', '.join(top)}"
        )
    for relative, cap in LINE_CAPS.items():
        path = ROOT / relative
        if not path.is_file():
            problems.append(f"{relative} is missing (ADR-0031 §4.2 target)")
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > cap:
            problems.append(f"{relative} is {lines} lines (cap {cap})")
    for path in sorted(ENGINE.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative in LINE_CAPS or relative in OVER_CAP_ALLOWED:
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > MODULE_CAP:
            problems.append(f"{relative} is {lines} lines (module cap {MODULE_CAP})")
    return problems


def sample_config() -> Config:
    """A default Config with no real secret: enough for the generators and the token scan."""
    return Config(
        paths=Paths(
            config=ROOT / "config.yaml",
            roles=ROOT / "roles.yaml",
            soul_yaml=ROOT / "soul.yaml",
            workspace=ROOT / ".eidesoul" / "workspace",
        ),
        database=DatabaseConfig(url="postgresql://localhost/eidesoul", url_env=""),
        documents=None,
        llm=LLMConfig(
            provider="custom",
            base_url="http://127.0.0.1:9/v1",
            model="selfcheck-model",
            api_key_env="SELFCHECK_KEY",
        ),
        workspace=ROOT / ".eidesoul" / "workspace",
        approval=ApprovalConfig(),
        roles=RoleRegistry(by_name={}, default="member"),
        default_role="member",
        gateways={},
        soul=Soul(name="Eidesoul", language="en"),
        sandbox=SandboxConfig(),
        mcp={},
        self_knowledge=SelfKnowledgeConfig(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADR-0015 self-knowledge guard")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify behavior ids, references, forbidden tokens, and topology",
    )
    args = parser.parse_args(argv)
    if not args.check:
        parser.print_help()
        return 0
    config = sample_config()
    problems = [*check(config), *size_problems()]
    manifest = SelfKnowledge(config)
    topics = ", ".join(manifest.topics())
    if problems:
        print(f"selfcheck: {len(problems)} problem(s)")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print(
        f"selfcheck: ok — behaviors={len(manifest.behavior_ids())} "
        f"topics={topics} engine_modules={len(manifest.topology())}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
