"""Command /unlink: detach one gateway account from this identity."""


from ..i18n import t
from ..registry import Handler
from . import CommandContext


async def unlink(ctx: CommandContext) -> str:
    lang = ctx.lang
    parts = ctx.args.split()
    if not parts:
        return t("command.unlink.usage", lang)
    gateway = parts[0].casefold()
    if len(parts) == 1:
        return t("command.unlink.ask", lang, gateway=gateway)
    if len(parts) == 2 and parts[1] == "confirm":
        person = ctx.inbound.actor if ctx.inbound is not None else ""
        removed = ctx.agent.identity.unlink(person, gateway)
        key = "command.unlink.done" if removed else "command.unlink.missing"
        return t(key, lang, gateway=gateway)
    return t("command.unlink.usage", lang)


COMMAND = Handler(
    name="unlink",
    description="🔓 Detach one gateway account from this identity.",
    fn=unlink,
    aliases=("lepas",),
    usage="<gateway> [confirm]",
)
