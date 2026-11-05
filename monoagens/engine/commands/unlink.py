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
        address = ""
        if (
            ctx.inbound is not None
            and ctx.gateway is not None
            and gateway == str(getattr(ctx.gateway, "name", "") or "").casefold()
        ):
            # Only the sender's own account on this body has a known address; another gateway's
            # link is removed by the (gateway) key and its own stored addresses.
            address = str(
                ctx.inbound.payload.sender_id or ctx.inbound.payload.sender_address or ""
            )
        removed = ctx.agent.identity.unlink(person, gateway, address)
        key = "command.unlink.done" if removed else "command.unlink.missing"
        return t(key, lang, gateway=gateway)
    return t("command.unlink.usage", lang)


COMMAND = Handler(
    name="unlink",
    description="Detach one gateway account from this identity.",
    fn=unlink,
    aliases=(),
    usage="<gateway> [confirm]",
)
