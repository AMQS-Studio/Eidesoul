"""Command /reset: clear this chat's conversation context."""

from __future__ import annotations

from ..i18n import t
from ..registry import Handler
from . import CommandContext


async def reset(ctx: CommandContext) -> str:
    ctx.agent.windows.reset(ctx.chat_id)
    if ctx.agent.history is not None:
        try:
            ctx.agent.history.reset(ctx.chat_id)
        except Exception:
            pass
    return t("command.reset.done", ctx.lang)


COMMAND = Handler(
    name="reset",
    description="🧹 Start a new conversation in this chat (the archive is kept).",
    fn=reset,
    aliases=("r", "clear", "new"),
)
