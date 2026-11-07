"""Command /reload: reload config/soul/role files without a restart."""


from ..access.permissions import command_allowed
from ..i18n import t
from ..registry import Handler
from . import CommandContext


async def reload(ctx: CommandContext) -> str:
    # ADR-0002 §4.9: the highest-priority role must carry `reload`; a lower role cannot grant it.
    if not ctx.roles or not command_allowed(ctx.roles[:1], "reload"):
        return t("agent.command_denied", ctx.lang, name="reload", roles=ctx.role_names()).strip()
    if not ctx.direct:
        return t("command.reload.private_only", ctx.lang).strip()
    try:
        summary = await ctx.agent.reload()
    except Exception as exc:
        return f"error: {type(exc).__name__}: {exc}"
    return t("command.reload.done", ctx.lang, summary=summary).strip()


COMMAND = Handler(
    name="reload",
    description="Reload config/soul/roles from the operator files without a restart.",
    fn=reload,
)
