"""Command /inspect: show the harness task and the actions its last turn produced."""

from __future__ import annotations

from ....engine.commands import CommandContext
from ....engine.i18n import t
from ....engine.registry import Handler


async def _inspect(ctx: CommandContext) -> str:
    gateway = ctx.gateway
    task = str(getattr(gateway, "task", "") or "")
    if not task:
        return t("gateway.harness.no_task", ctx.lang)
    actions = list(getattr(gateway, "actions", ()) or ())
    lines = [
        t(
            "gateway.harness.inspect",
            ctx.lang,
            task=task,
            thread=ctx.thread_id,
            count=len(actions),
        )
    ]
    for action in actions:
        lines.append(f"- {action.capability} -> {action.target}: {str(action.payload)[:200]}")
    return "\n".join(lines)


COMMAND = Handler(
    name="inspect",
    description="Show the harness task and the actions of the last turn.",
    fn=_inspect,
)
