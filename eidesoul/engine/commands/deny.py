"""Command /deny: cancel a pending approval without running it."""

from __future__ import annotations

from ..i18n import t
from ..registry import Handler
from . import CommandContext


async def _deny(ctx: CommandContext) -> str:
    agent = ctx.agent
    lang = ctx.lang
    raw = ctx.args.strip()
    key, _, reason = raw.partition(" ")
    if key.lower() in {"last", "terakhir", ""}:
        approval = agent.approvals.latest(ctx.thread_id, ctx.sender)
    else:
        approval = agent.approvals.get(key.lstrip("#"))
    if approval is None:
        return t("command.deny.none", lang)
    if approval.thread_id != ctx.thread_id or approval.sender != ctx.sender:
        return t("command.deny.foreign", lang)
    if approval.status != "pending":
        return t("command.deny.already", lang, id=approval.id, status=approval.status)
    agent.approvals.mark(approval.id, "denied", ctx.sender, reason.strip())
    return t("command.deny.done", lang, id=approval.id)


COMMAND = Handler(
    name="deny",
    description="🚫 Cancel an action that is waiting for approval.",
    fn=_deny,
    usage="[id|last] [reason]",
)
