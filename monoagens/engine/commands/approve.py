"""Command /approve: run a pending approval without LLM, with the original role permissions."""


import contextlib
import time

from ..access.permissions import command_allowed, tool_allowed
from ..access.relay import MODES, RelayRefused, RelayStore
from ..access.roles import privileged
from ..guard import ActionRequest, recheck_scope, scope_proof
from ..guard.policy import grant_scope, reset_scope
from ..i18n import t
from ..inbound import Address, action_from_json
from ..registry import Handler
from ..tools import execute, resolve_for
from . import CommandContext

CLIP = 8000
SEND_TOOLS = ("send_to", "send")


def _rooms(approval) -> set[str]:
    """The target room and the room that asked for the action (ADR-0004 §4.2)."""
    found = {str(approval.thread_id or ""), str(approval.origin_thread or "")}
    return {value for value in found if value}


def target_authority(approval, roles) -> bool:
    """Does this role reach the target the approval names? (fail closed)"""
    if any(privileged(role) for role in roles):
        return True
    if approval.kind == "send":
        return any(tool_allowed(roles, name) for name in SEND_TOOLS)
    if approval.tool:
        return tool_allowed(roles, approval.tool)
    return False


def decision_refusal(approval, *, thread_id: str, sender: str, roles) -> str:
    """`""` when this person may decide the row, `"foreign"` otherwise.

    ADR-0004 §4.2: the requester decides their own row from the origin room or the target
    room; anyone else needs the `approve` command *and* authority over the target.
    """
    if any(privileged(role) for role in roles):
        return ""
    if str(thread_id or "") not in _rooms(approval):
        return "foreign"
    if str(sender or "") == str(approval.sender or ""):
        return ""
    if command_allowed(tuple(roles), "approve") and target_authority(approval, roles):
        return ""
    return "foreign"


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


async def _automation_decision(agent, approval, *, sender: str, lang: str) -> str:
    """ADR-0014 §4.8: the row is stored only here, by the approval that authorises it."""
    from ..jobs.automation_store import AutomationStore
    from ..jobs.schedule import ManifestError

    payload = approval.payload if isinstance(approval.payload, dict) else {}
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict):
        manifest = approval.args.get("manifest") if isinstance(approval.args, dict) else None
    store = AutomationStore(agent.db, agent.config.automations)
    try:
        if manifest is None:
            raise ManifestError("the automation must be a mapping")
        automation_id = store.create(
            manifest, approval=approval, agent=agent, approved_now=True
        )
    except ManifestError as exc:
        agent.approvals.mark(approval.id, "denied", sender, str(exc))
        return f"error: {exc}"
    agent.approvals.mark(approval.id, "approved", sender, f"automation {automation_id}")
    name = str((manifest or {}).get("name") or automation_id)
    return t("automation.approved", lang, name=name, id=automation_id)


async def _settings_decision(agent, approval, *, sender: str, lang: str) -> tuple[str, bool]:
    try:
        summary = await agent.apply_settings_payload(approval.payload)
    except Exception as exc:
        agent.approvals.mark(approval.id, "failed", sender, str(exc))
        return f"error: {type(exc).__name__}: {exc}", False
    agent.approvals.mark(approval.id, "approved", sender, str(summary)[:2000])
    text = t("command.approve.applied", lang, id=approval.id, summary=summary)[:CLIP]
    return text, True


async def _send_approval(agent, approval, *, sender: str, roles, lang: str):
    """Re-dispatch an approved outbound `Action` through the one door."""
    payload = approval.args if isinstance(approval.args, dict) else {}
    try:
        action = action_from_json(payload.get("action") or {})
        origin_data = payload.get("origin") or {}
        origin = Address(
            gateway=str(approval.origin_gateway or origin_data.get("gateway") or ""),
            thread=str(approval.origin_thread or origin_data.get("thread") or ""),
            proof=str(origin_data.get("proof") or ""),
        )
    except (TypeError, ValueError, AttributeError):
        agent.approvals.mark(approval.id, "failed", sender, "stored action is invalid")
        return t("command.approve.denied", lang, id=approval.id, reason="stored action"), False
    if str(sender or "") == str(approval.sender or ""):
        snapshot = set(approval.roles)
        if snapshot and not {role.name for role in roles} <= snapshot:
            agent.approvals.mark(approval.id, "denied", sender, "role changed")
            return t("command.approve.denied", lang, id=approval.id, reason="role changed"), False
    if approval.approval_class != "consent" and not command_allowed(tuple(roles), "approve"):
        agent.approvals.mark(approval.id, "denied", sender, "approver role required")
        return t("command.approve.class_denied", lang, id=approval.id), False
    agent.approvals.mark(approval.id, "approved", sender, "send approved")
    receipt = await agent.dispatch(
        sender,
        action.address,
        action,
        origin=origin,
        reason=str(payload.get("reason") or ""),
    )
    if receipt.state != "sent":
        return (
            t(
                "command.approve.send_failed",
                lang,
                id=approval.id,
                state=receipt.state,
                reason=receipt.reason,
            ),
            False,
        )
    return t("command.approve.sent", lang, id=approval.id, target=action.address.thread), True


async def _tool_approval(
    agent, approval, *, sender: str, roles, gateway, thread_id: str, lang: str
):
    """Re-verify the stored proof and the approver's RBAC, then run the tool."""
    current = tuple(roles)
    thread = str(thread_id or approval.thread_id or "")
    if str(sender or "") == str(approval.sender or ""):
        snapshot = set(approval.roles)
        if snapshot and not {role.name for role in current} <= snapshot:
            # ADR-0007 §4.2: the stored role is an upper bound, never a source of permission.
            agent.approvals.mark(approval.id, "denied", sender, "role changed")
            return t("command.approve.denied", lang, id=approval.id, reason="role changed"), False
    if approval.tool and not tool_allowed(current, approval.tool):
        agent.approvals.mark(approval.id, "denied", sender, "tool no longer allowed")
        return (
            t(
                "command.approve.denied",
                lang,
                id=approval.id,
                reason=f"tool '{approval.tool}' is no longer allowed for this role",
            ),
            False,
        )
    tool_ctx = agent.tool_context(
        thread,
        sender,
        current,
        gateway,
        approved=True,
        approval_id=str(approval.id),
    )
    spec = None
    index = getattr(agent, "tool_index", None)
    if index is not None:
        spec = index.get(str(approval.tool or approval.kind), str(approval.gateway or ""))
    if spec is None and getattr(agent, "tools", None) is not None:
        spec = agent.tools.by_name.get(str(approval.tool or approval.kind))
    resolved: tuple[str, ...] = ()
    raw_path = (
        approval.args.get(spec.path_arg)
        if spec is not None and spec.path_arg and isinstance(approval.args, dict)
        else None
    )
    if isinstance(raw_path, str) and raw_path.strip():
        try:
            resolved = (str(resolve_for(agent.config, raw_path, current, thread)),)
        except PermissionError:
            resolved = ()
    request = ActionRequest(
        tool=approval.tool or approval.kind,
        args=dict(approval.args),
        thread_id=thread,
        sender=sender,
        gateway=str(approval.gateway or getattr(gateway, "name", "") or ""),
        roles=tuple(role.name for role in current),
        workspace=str(agent.config.workspace),
        roots=tuple(str(root) for root in tool_ctx.roots),
        paths=resolved,
        target=spec.target if spec is not None else "",
        declared_class=spec.approval_class if spec is not None else "authorization",
        destructive=spec.destructive if spec is not None else (),
        path_arg=spec.path_arg if spec is not None else "",
        scan=spec.scan if spec is not None else (),
    )
    proof = scope_proof(request, extra=agent.config.guard.credential_patterns)
    if approval.scope_proof:
        reason = recheck_scope(approval.scope_proof, proof)
        if reason:
            agent.approvals.mark(approval.id, "denied", sender, reason)
            return t("command.approve.scope_changed", lang, id=approval.id, reason=reason), False
    elif approval.kind == "tool":
        agent.approvals.mark(approval.id, "denied", sender, "no scope proof")
        return (
            t(
                "command.approve.denied",
                lang,
                id=approval.id,
                reason="the approval has no scope proof; request it again",
            ),
            False,
        )
    if proof["class"] == "authorization" and not command_allowed(current, "approve"):
        agent.approvals.mark(approval.id, "denied", sender, "approver role required")
        return t("command.approve.class_denied", lang, id=approval.id), False
    if approval.kind == "settings":
        return await _settings_decision(agent, approval, sender=sender, lang=lang)
    if approval.kind == "automation":
        return await _automation_decision(agent, approval, sender=sender, lang=lang), True
    specs = agent.tool_index.specs(approval.gateway or (getattr(gateway, "name", "") or ""))
    if agent.hub is not None:
        specs.update({handler.name: handler for handler in agent.hub.specs(current)})
    token = grant_scope(proof)
    try:
        result = await execute(specs, approval.tool, approval.args, tool_ctx)
    finally:
        reset_scope(token)
    agent.approvals.mark(approval.id, "approved", sender, result[:2000])
    with contextlib.suppress(Exception):
        agent.ledger.log_action(
            thread,
            thread,
            "approved",
            f"{approval.tool} approved #{approval.id}",
            source_name=gateway.chat_name(thread) if gateway else "",
            target_name=gateway.chat_name(thread) if gateway else "",
            request=approval.summary,
        )
    return t("command.approve.ran", lang, id=approval.id, result=result)[:CLIP], True


async def run_approval(
    agent,
    approval,
    *,
    sender: str,
    roles,
    gateway,
    thread_id: str = "",
) -> tuple[str, bool]:
    """Decide one pending row deterministically: `(chat text, did it run)`.

    Shared by `/approve` and the natural-confirmation path, so both re-check the stored scope
    proof and the current RBAC instead of trusting the conversation.
    """
    lang = str(agent.soul.language)
    if approval.kind == "send":
        return await _send_approval(agent, approval, sender=sender, roles=roles, lang=lang)
    return await _tool_approval(
        agent,
        approval,
        sender=sender,
        roles=roles,
        gateway=gateway,
        thread_id=thread_id,
        lang=lang,
    )


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
        if decision_refusal(
            approval, thread_id=ctx.thread_id, sender=ctx.sender, roles=ctx.roles
        ):
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
    text, _ran = await run_approval(
        agent,
        approval,
        sender=ctx.sender,
        roles=ctx.roles,
        gateway=ctx.gateway,
        thread_id=ctx.thread_id,
    )
    return text


COMMAND = Handler(
    name="approve",
    description="Run a sensitive action that is waiting for approval.",
    fn=_approve,
    usage="[id|last]",
)
