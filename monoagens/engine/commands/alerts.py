"""Command /alerts: the failure-alerting state, read-only (ADR-0047)."""

from ..access.roles import PROTECTED, privileged
from ..i18n import t
from ..registry import Handler
from . import CommandContext

SOURCE_NAMES = ("database", "llm", "gateway", "plugin", "mcp")


def _sources(config) -> str:
    return ", ".join(
        f"{name}={'on' if getattr(config.alerts.sources, name) else 'off'}"
        for name in SOURCE_NAMES
    )


async def alerts_command(ctx: CommandContext) -> str:
    lang = ctx.lang
    if not any(role.name == PROTECTED or privileged(role) for role in ctx.roles):
        return t("command.alerts.denied", lang)
    config = ctx.agent.config
    return t(
        "command.alerts.status",
        lang,
        state="on" if config.alerts.enabled else "off",
        threshold=int(config.alerts.threshold),
        cooldown=int(config.alerts.cooldown_seconds),
        sources=_sources(config),
    )


COMMAND = Handler(
    name="alerts",
    description="Show the failure-alerting state (administrator).",
    fn=alerts_command,
    aliases=(),
    usage="",
)
