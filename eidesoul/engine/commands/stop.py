"""Command /stop: cancel the turn running in this room (ADR-0013 §7.1).

`/stop` is a command, so it is read outside the per-thread lock while a turn holds it.
It cancels only the task in this room, marks that turn `interrupted(user_stop)`, and
answers with one catalog notice. It does not change `mode`/the lease (that is `/freeze`),
does not install a quiet period, and does not delete anything from the backlog.
"""

from __future__ import annotations

from ..i18n import t
from ..registry import Handler
from . import CommandContext


async def stop(ctx: CommandContext) -> str:
    lang = ctx.lang
    if ctx.args.strip():
        return t("command.stop.usage", lang)
    if not await ctx.agent.cancel_turn(ctx.thread_id, reason="user_stop"):
        return t("command.stop.none", lang)
    return t("command.stop.cancelled", lang)


COMMAND = Handler(
    name="stop",
    description="🛑 Cancel the turn running in this chat.",
    fn=stop,
    usage="/stop",
)
