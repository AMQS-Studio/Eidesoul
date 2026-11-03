"""Command /approve: run a pending approval without LLM, with the original role permissions."""

from __future__ import annotations

import time

from ..i18n import t
from ..registry import Handler
from ..relay import MODES, RelayRefused, RelayStore
from ..tools import execute
from . import CommandContext

CLIP = 8000


def _relay_decision(ctx: CommandContext, approval, extra: str) -> str:
    """ADR-0023: the owner's chosen mode becomes the grant; the question itself is the consent."""
    agent = ctx.agent
    lang = ctx.lang
    mode = extra.lower()
    if mode not in MODES:
        return t("command.approve.relay_mode", lang, modes=", ".join(MODES))
    try:
        grant = RelayStore(agent.db).decide(approval=approval, mode=mode, by=ctx.sender)
    except (RelayRefused, ValueError) as exc:
        return f"error: {exc}"
    agent.approvals.mark(
        approval.id, "approved", ctx.sender, f"relay {grant.id} mode={grant.mode}"
    )
    return t("command.approve.relay_done", lang, id=approval.id, mode=grant.mode)


async def _settings_decision(ctx: CommandContext, approval) -> str:
    agent = ctx.agent
    lang = ctx.lang
    try:
        summary = await agent.apply_settings_payload(approval.payload)
    except Exception as exc:
        agent.approvals.mark(approval.id, "failed", ctx.sender, str(exc))
        return f"error: {type(exc).__name__}: {exc}"
    agent.approvals.mark(approval.id, "approved", ctx.sender, str(summary)[:2000])
    return t("command.approve.applied", lang, id=approval.id, summary=summary)[:CLIP]


async def _approve(ctx: CommandContext) -> str:
    agent = ctx.agent
    lang = ctx.lang
    parts = ctx.args.strip().lstrip("#").split()
    raw = parts[0] if parts else ""
    extra = " ".join(parts[1:]).strip()
    agent.approvals.expire()
    if raw and raw.lower() not in {"last", "terakhir"}:
        approval = agent.approvals.get(raw)
        if approval is None:
            return t("command.approve.not_found", lang, id=raw)
        if approval.thread_id != ctx.thread_id or approval.sender != ctx.sender:
            return t("command.approve.foreign", lang)
        if approval.status != "pending":
            return t("command.approve.already", lang, id=approval.id, status=approval.status)
    else:
        approval = agent.approvals.latest(ctx.thread_id, ctx.sender)
        if approval is None:
            return t("command.approve.none", lang)
    if approval.expires_at < time.time():
        agent.approvals.mark(approval.id, "expired", ctx.sender)
        return t("command.approve.expired", lang, id=approval.id)

    if approval.tool == "relay":
        return _relay_decision(ctx, approval, extra)

    roles = tuple(
        role for name in approval.roles if (role := agent.config.roles.get(name)) is not None
    )
    if not roles:
        agent.approvals.mark(approval.id, "denied", ctx.sender, "role missing")
        return t("command.approve.role_missing", lang)
    if approval.kind == "settings":
        return await _settings_decision(ctx, approval)

    specs = {handler.name: handler for handler in agent.tools.by_name.values()}
    if agent.hub is not None:
        specs.update({handler.name: handler for handler in agent.hub.specs(roles)})
    tool_ctx = agent.tool_context(
        ctx.thread_id,
        ctx.sender,
        roles,
        ctx.gateway,
        approved=True,
        approval_id=str(approval.id),
    )
    result = await execute(specs, approval.tool, approval.args, tool_ctx)
    agent.approvals.mark(approval.id, "approved", ctx.sender, result[:2000])
    try:
        agent.ledger.log_action(
            ctx.thread_id,
            ctx.thread_id,
            "approved",
            f"{approval.tool} approved #{approval.id}",
            source_name=ctx.gateway.chat_name(ctx.thread_id) if ctx.gateway else "",
            target_name=ctx.gateway.chat_name(ctx.thread_id) if ctx.gateway else "",
            request=approval.summary,
        )
    except Exception:
        pass
    return t("command.approve.ran", lang, id=approval.id, result=result)[:CLIP]


COMMAND = Handler(
    name="approve",
    description="✅ Run a sensitive action that is waiting for approval.",
    fn=_approve,
    usage="[id|last]",
)
