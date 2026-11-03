"""Self-knowledge guard: `behavior:<id>`, config/role/registry references, forbidden tokens.

Run it from the repo root::

    python scripts/selfcheck.py --check

Exit code 0 means every `behavior:<id>` resolves, every topic stays under its limit and leaks no
runtime fact, and the engine imports no gateway (ADR-0015 §4.8). `eidesoul selftest` calls the
same `check()` so a stale manifest fails the shipped smoke test too.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eidesoul.engine.config import (  # noqa: E402
    ApprovalConfig,
    Config,
    DatabaseConfig,
    LLMConfig,
    Paths,
    SandboxConfig,
    SelfKnowledgeConfig,
)
from eidesoul.engine.roles import RoleRegistry  # noqa: E402
from eidesoul.engine.self_knowledge import SelfKnowledge, check  # noqa: E402
from eidesoul.engine.soul import Soul  # noqa: E402


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
    problems = check(config)
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
