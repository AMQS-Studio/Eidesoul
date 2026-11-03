"""Command /freeze: ask this instance to go standby; another instance can take over."""


from ..i18n import t
from ..registry import Handler
from . import CommandContext


async def freeze(ctx: CommandContext) -> str:
    agent = ctx.agent
    if agent.lease is None:
        return t("command.freeze.no_lease", ctx.lang)
    agent.request_freeze()
    return t("command.freeze.requested", ctx.lang)


COMMAND = Handler(
    name="freeze",
    description="🧊 Freeze this instance (owner/admin); another instance can take over.",
    fn=freeze,
)
