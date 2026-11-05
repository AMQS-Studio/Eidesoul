"""Runtime reload: validate the files, swap the hot snapshot, report the rest (ADR-0046 §4.9).

The agent is the host; this module owns the one reload path so `agent.py` stays under the module
cap and `/reload`, the settings tool, and `/language` share the same behaviour.
"""


import logging
from dataclasses import replace

from ..config import Config, load_config
from ..guard import SandboxGuard, build_safety
from ..i18n import reload_catalogs
from ..llm import LLM
from ..memory import memory_status

log = logging.getLogger(__name__)


def restart_tokens(config: Config) -> dict[str, object]:
    """Values that a running process cannot swap without a restart."""
    memory = config.memory
    return {
        "workspace": config.workspace,
        "gateways": config.gateways,
        "sandbox": config.sandbox,
        "web_search": config.web_search,
        "mcp": config.mcp,
        "database": config.database,
        "paths": config.paths,
        "llm.api_key_env": config.llm.api_key_env,
        "memory.collection": memory.collection,
        "memory.extraction_model": memory.extraction_model,
        "voice.enabled": config.soul.voice.enabled,
        "voice.affect.enabled": config.soul.voice.affect.enabled,
        "voice.style.enabled": config.soul.voice.style.enabled,
        "voice.sampling": config.soul.voice.sampling,
        "voice.safety": config.soul.voice.safety,
        "ambient.enabled": config.ambient.enabled,
    }


def _merged(agent, loaded: Config, changed: list[str]) -> Config:
    """Keep the old value for every restart-only field; swap everything else."""
    hot = loaded
    if "workspace" in changed:
        hot = replace(hot, workspace=agent.config.workspace)
    if "database" in changed:
        hot = replace(hot, database=agent.config.database)
    if "paths" in changed:
        hot = replace(hot, paths=agent.config.paths)
    if "gateways" in changed:
        hot = replace(hot, gateways=agent.config.gateways)
    if "sandbox" in changed:
        hot = replace(hot, sandbox=agent.config.sandbox)
    if "web_search" in changed:
        hot = replace(hot, web_search=agent.config.web_search)
    if "mcp" in changed:
        hot = replace(hot, mcp=agent.config.mcp)
    if "llm.api_key_env" in changed:
        hot = replace(hot, llm=agent.config.llm)
    if "memory.collection" in changed or "memory.extraction_model" in changed:
        hot = replace(
            hot,
            memory=replace(
                hot.memory,
                collection=agent.config.memory.collection,
                extraction_model=agent.config.memory.extraction_model,
            ),
        )
    if any(key.startswith("voice.") for key in changed):
        hot = replace(hot, soul=agent.config.soul)
    return hot


def _apply(agent, hot: Config) -> None:
    old_llm = agent.config.llm
    agent.config = hot
    agent.soul = hot.soul
    if agent.llm is not None and hot.llm != old_llm:
        agent.llm = LLM(hot, db=agent.db)
    if agent.skills is not None:
        agent.skills.config = hot.skills
    if agent.style is not None:
        agent.style.config = hot.soul.voice.style
    if agent.affect is not None:
        agent.affect.config = hot.soul.voice.affect
    if agent.vibe is not None:
        agent.vibe.config = hot.soul.voice.affect.vibe
    configure = getattr(agent.memory, "configure_limits", None)
    if callable(configure):
        configure(hot.memory)
    agent.approvals.configure(hot.approval)
    agent.safety = build_safety(hot.approval)
    agent.sandbox_guard = SandboxGuard(
        hot.guard, hot.approval.sensitive_commands, agent.llm
    )
    agent.privacy.configure(hot.memory.privacy)
    if agent.ambient is not None:
        agent.ambient.config = hot
        agent.ambient.settings = hot.ambient
        agent.ambient.llm = agent.llm
    agent.runtime.paths = hot.paths
    agent.runtime.memory_status = memory_status(agent.memory)
    agent._sync_soul_rules()


async def reload_runtime(agent) -> str:
    """ADR-0046 §4.9: validate the three files, swap the hot runtime, report the rest.

    The cache is refreshed only after validation succeeds; an invalid file raises before
    anything is swapped, so the old runtime keeps serving.
    """
    reload_catalogs()
    loaded = load_config(agent.config.paths.config, require_key=False)
    before = restart_tokens(agent.config)
    after = restart_tokens(loaded)
    changed = sorted(key for key in before if before[key] != after[key])
    hot = _merged(agent, loaded, changed)
    _apply(agent, hot)
    if agent.documents is not None:
        try:
            agent.documents.sync()
        except Exception:
            log.warning("document cache refresh failed after reload", exc_info=True)
    agent.reload_generation += 1
    summary = "config/soul/roles reloaded"
    if changed:
        summary += "; restart required: " + ", ".join(changed)
    return summary
