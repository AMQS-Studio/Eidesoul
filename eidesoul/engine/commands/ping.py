"""Command /ping: check that the bot is alive."""


from ..i18n import t
from ..registry import Handler
from . import CommandContext


async def ping(ctx: CommandContext) -> str:
    return t("command.ping.pong", ctx.lang)


COMMAND = Handler(
    name="ping",
    description="🏓 Check that Eidesoul is alive.",
    fn=ping,
    aliases=("p",),
)
