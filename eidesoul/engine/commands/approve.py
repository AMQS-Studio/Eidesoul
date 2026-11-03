"""Command /approve: run a pending approval without LLM, with the original role permissions."""


import contextlib
import time

from ..access.permissions import command_allowed, tool_allowed
from ..access.relay import MODES, RelayRefused, RelayStore
from ..guard import ActionRequest, recheck_scope, scope_proof
from ..i18n import t
from ..inbound import Address, action_from_json
from ..registry import Handler
from ..tools import execute, resolve_for
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


async def _automation_decision(ctx: CommandContext, approval) -> str:
    """ADR-0014 §4.8: the row is stored only here, by the approval that authorises it."""
    from ..jobs.automation import AutomationStore, ManifestError

    agent = ctx.agent
    lang = ctx.lang
    payload = approval.payload if isinstance(approval.payload, dict) else {}
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict):
        manifest = approval.args.get("manifest") if isinstance(approval.args, dict) else None
    store = AutomationStore(agent.db, agent.config.automations)
    try:
        automation_id = store.create(
            manifest, approval=approval, agent=agent, approved_now=True
        )
    except ManifestError as exc:
        agent.approvals.mark(approval.id, "denied", ctx.sender, str(exc))
        return f"error: {exc}"
    agent.approvals.mark(approval.id, "approved", ctx.sender, f"automation {automation_id}")
    name = str((manifest or {}).get("name") or automation_id)
    return t("automation.approved", lang, name=name, id=automation_id)


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


async def _send_decision(ctx: CommandContext, approval) -> str:
    """Re-dispatch an approved outbound `Action` through the one door."""
    agent = ctx.agent
    lang = ctx.lang
    payload = approval.args if isinstance(approval.args, dict) else {}
    try:
        action = action_from_json(payload.get("action") or {})
        origin_data = payload.get("origin") or {}
        origin = Address(
            gateway=str(origin_data.get("gateway") or ""),
            thread=str(origin_data.get("thread") or ""),
            proof=str(origin_data.get("proof") or ""),
        )
    except (TypeError, ValueError, AttributeError):
        agent.approvals.mark(approval.id, "failed", ctx.sender, "stored action is invalid")
        return t("command.approve.denied", lang, id=approval.id, reason="stored action")
    snapshot = set(approval.roles)
    if snapshot and not {role.name for role in ctx.roles} <= snapshot:
        agent.approvals.mark(approval.id, "denied", ctx.sender, "role changed")
        return t("command.approve.denied", lang, id=approval.id, reason="role changed")
    if not command_allowed(tuple(ctx.roles), "approve"):
        agent.approvals.mark(approval.id, "denied", ctx.sender, "approver role required")
        return t("command.approve.class_denied", lang, id=approval.id)
    agent.approvals.mark(approval.id, "approved", ctx.sender, "send approved")
    receipt = await agent.dispatch(
        ctx.sender,
        action.address,
        action,
        origin=origin,
        reason=str(payload.get("reason") or ""),
    )
    if receipt.state != "sent":
        return t(
            "command.approve.send_failed",
            lang,
            id=approval.id,
            state=receipt.state,
            reason=receipt.reason,
        )
    return t("command.approve.sent", lang, id=approval.id, target=action.address.thread)


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
    if approval.kind == "send":
        # ADR-0032 §4.4: the row already holds the authorization proof; rebuild the message.
        return await _send_decision(ctx, approval)

    current = tuple(ctx.roles)
    snapshot = set(approval.roles)
    if snapshot and not {role.name for role in current} <= snapshot:
        # ADR-0007 §4.2: the stored role is an upper bound, never a source of permission.
        agent.approvals.mark(approval.id, "denied", ctx.sender, "role changed")
        return t("command.approve.denied", lang, id=approval.id, reason="role changed")
    if approval.tool and not tool_allowed(current, approval.tool):
        agent.approvals.mark(approval.id, "denied", ctx.sender, "tool no longer allowed")
        return t(
            "command.approve.denied",
            lang,
            id=approval.id,
            reason=f"tool '{approval.tool}' is no longer allowed for this role",
        )
    tool_ctx = agent.tool_context(
        ctx.thread_id,
        ctx.sender,
        current,
        ctx.gateway,
        approved=True,
        approval_id=str(approval.id),
    )
    resolved: tuple[str, ...] = ()
    raw_path = approval.args.get("path") if isinstance(approval.args, dict) else None
    if isinstance(raw_path, str) and raw_path.strip():
        try:
            resolved = (
                str(resolve_for(agent.config, raw_path, current, ctx.thread_id)),
            )
        except PermissionError:
            resolved = ()
    request = ActionRequest(
        tool=approval.tool or approval.kind,
        args=dict(approval.args),
        thread_id=ctx.thread_id,
        sender=ctx.sender,
        gateway=getattr(ctx.gateway, "name", "") or "",
        roles=tuple(role.name for role in current),
        workspace=str(agent.config.workspace),
        roots=tuple(str(root) for root in tool_ctx.roots),
        paths=resolved,
    )
    proof = scope_proof(request, extra=agent.config.guard.credential_patterns)
    if approval.scope_proof:
        reason = recheck_scope(approval.scope_proof, proof)
        if reason:
            agent.approvals.mark(approval.id, "denied", ctx.sender, reason)
            return t("command.approve.scope_changed", lang, id=approval.id, reason=reason)
    elif approval.kind == "tool":
        agent.approvals.mark(approval.id, "denied", ctx.sender, "no scope proof")
        return t(
            "command.approve.denied",
            lang,
            id=approval.id,
            reason="the approval has no scope proof; request it again",
        )
    if proof["class"] == "authorization" and not command_allowed(current, "approve"):
        agent.approvals.mark(approval.id, "denied", ctx.sender, "approver role required")
        return t("command.approve.class_denied", lang, id=approval.id)
    special = {
        "settings": _settings_decision,
        "automation": _automation_decision,
    }.get(approval.kind)
    if special is not None:
        return await special(ctx, approval)

    specs = agent.tool_index.specs(approval.gateway or (ctx.gateway.name if ctx.gateway else ""))
    if agent.hub is not None:
        specs.update({handler.name: handler for handler in agent.hub.specs(current)})
    result = await execute(specs, approval.tool, approval.args, tool_ctx)
    agent.approvals.mark(approval.id, "approved", ctx.sender, result[:2000])
    with contextlib.suppress(Exception):
        agent.ledger.log_action(
            ctx.thread_id,
            ctx.thread_id,
            "approved",
            f"{approval.tool} approved #{approval.id}",
            source_name=ctx.gateway.chat_name(ctx.thread_id) if ctx.gateway else "",
            target_name=ctx.gateway.chat_name(ctx.thread_id) if ctx.gateway else "",
            request=approval.summary,
        )
    return t("command.approve.ran", lang, id=approval.id, result=result)[:CLIP]


COMMAND = Handler(
    name="approve",
    description="✅ Run a sensitive action that is waiting for approval.",
    fn=_approve,
    usage="[id|last]",
)
