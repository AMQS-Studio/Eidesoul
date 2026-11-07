"""Tool send_message: send text to this room, another room, or another body (ADR-0001 §4.2).

The core owns the capability: the target is resolved through the target body's directory, the
delivery goes through the one door (origin, reason, and the exit policy), and the outbound row is
keyed by the deterministic id of the tool step, so a resume never sends the same text twice.
"""


import logging
from typing import Any

from ..access.relay import RelayStore
from ..inbound import (
    AUTHORIZATION,
    CONSENT,
    Action,
    Address,
    Directory,
    GatewayError,
    SendRefused,
    StandbyError,
    tool_action_id,
)
from ..registry import Handler
from ..turn.delivery import MAX_TEXT
from ..turn.reply import split_text
from . import ToolContext, body_of, record_send, resolve_address

log = logging.getLogger(__name__)


def _id_kwargs(found: str) -> dict[str, Any]:
    """An explicit action id only when an approval row already named one."""
    return {"id": found} if found else {}


def _origin(ctx: ToolContext) -> Address:
    return Address(str(getattr(ctx.gateway, "name", "") or ""), str(ctx.thread_id))


def _action(
    address: Address, text: str, approval_class: str, part: int = -1
) -> Action:
    found = tool_action_id(f"chunk:{part}") if part >= 0 else tool_action_id()
    return Action(
        address=address,
        capability="send_text",
        payload=text,
        approval_class=approval_class,
        **_id_kwargs(found),
    )


def _relay(ctx: ToolContext, thread_id: str, args: dict, text: str):
    """`(grant, "")` for a permitted relay, `(None, "")` for a plain send, `(None, error)`.

    ADR-0004: forwarding another person's answer needs that person's grant for this scope; the
    grant is checked here, at the send, and the ledger records the grant, never the content.
    """
    task = str(args.get("relay_task") or "").strip()
    source = str(args.get("relay_from") or "").strip()
    if not task and not source:
        return None, ""
    if not task or not source:
        return None, "error: a relay needs both relay_task and relay_from"
    if ctx.agent is None:
        return None, "error: the relay store is not available"
    store = RelayStore(ctx.agent.db)
    grants = store.by_task(task_id=task, from_person=source, active_only=False)
    if not grants:
        return None, "error: relay refused: no relay grant for this task"
    members = (
        ctx.gateway.participants(thread_id) if isinstance(ctx.gateway, Directory) else None
    )
    if members is not None:
        grants = [item for item in grants if item.to_person in members]
        if not grants:
            return None, "error: this body cannot prove the relay recipient; the relay is refused"
    grant, reason = store.check(
        task_id=task, from_person=source, to_person=grants[0].to_person
    )
    if grant is None:
        return None, f"error: relay refused: {reason}"
    if text and grant.mode == "anonymous":
        person = ctx.agent.identity.person(source) or {}
        name = str(person.get("name") or "").strip()
        if name and name.casefold() in text.casefold():
            return None, "error: an anonymous relay must not name its source"
    return grant, ""


def _record_relay(ctx: ToolContext, grant, ok: bool) -> None:
    """The ledger keeps the grant and the mode, never the message content (ADR-0004 §4.3)."""
    if ctx.ledger is None:
        return
    try:
        ctx.ledger.log_action(
            ctx.thread_id,
            ctx.thread_id,
            "relay",
            f"relay {grant.id} mode={grant.mode} from={grant.from_person}"
            f"{'' if ok else ' failed'}",
            source_name=str(getattr(ctx.gateway, "name", "") or ""),
            request=ctx.request,
        )
    except Exception:
        log.exception("failed to log the relay grant %s", grant.id)


def _label(body, address: Address) -> str:
    name = str(getattr(body, "chat_name", lambda thread: "")(address.thread) or "")
    return name or address.thread


def _prepare(ctx: ToolContext, args: dict):
    """`(body, address, text, grant, reason, refusal)` for one send request."""
    text = str(args.get("text") or "").strip()
    if not text:
        return None, None, "", None, "", "error: empty text"
    body = body_of(ctx, str(args.get("gateway") or ""))
    if body is None:
        name = str(args.get("gateway") or "")
        why = f"error: unknown body '{name}'" if name else "error: this body is not available"
        return None, None, "", None, "", why
    address, refusal = resolve_address(ctx, body, str(args.get("chat") or ""))
    if address is None:
        return None, None, "", None, "", refusal
    reason = str(args.get("reason") or "").strip()
    if not address.proof and not reason:
        return (
            None,
            None,
            "",
            None,
            "",
            "denied: an archived target needs no reason, a direct address does; "
            "pass the requester's reason",
        )
    grant, relay_refusal = _relay(ctx, address.thread, args, text)
    if relay_refusal:
        return None, None, "", None, "", relay_refusal
    if grant is not None and not reason:
        # The door needs a stated reason for any crossing; the grant is that reason.
        reason = f"relay {grant.id} mode={grant.mode}"
    return body, address, text, grant, reason, ""


async def _send_message(args: dict, ctx: ToolContext) -> str:
    if ctx.agent is None or ctx.gateway is None:
        return "error: sending is not available"
    body, address, text, grant, reason, refusal = _prepare(ctx, args)
    if address is None:
        return refusal
    # ADR-0007 §4.7: a direct target the archive does not know is outside the requester's scope.
    declared = CONSENT if address.proof else AUTHORIZATION
    render = getattr(body, "render", None)
    rendered = str(render(text)) if callable(render) else text
    chunks = split_text(rendered, limit=MAX_TEXT) or [rendered]
    multi = len(chunks) > 1
    for index, chunk in enumerate(chunks):
        stop = await _deliver(ctx, address, chunk, declared, reason, index if multi else -1)
        if stop:
            return stop
    label = _label(body, address)
    if grant is not None:
        _record_relay(ctx, grant, True)
        ctx.sent.append(
            {
                "tool": "send_message",
                "target": address.thread,
                "kind": "relay",
                "summary": f"relay {grant.id} mode={grant.mode}",
            }
        )
        return f"ok: text sent to {label}"
    record_send(ctx, address.thread, label, "text", text)
    ctx.sent.append(
        {"tool": "send_message", "target": address.thread, "kind": "text", "summary": text[:200]}
    )
    suffix = f" ({len(chunks)} parts)" if len(chunks) > 1 else ""
    return f"ok: text sent to {label}{suffix}"


async def _deliver(
    ctx: ToolContext,
    address: Address,
    chunk: str,
    declared: str,
    reason: str,
    part: int = -1,
) -> str:
    """One bubble to the door; "" on success, the error text when it did not go."""
    action = _action(address, chunk, declared, part)
    try:
        receipt = await ctx.agent.dispatch(
            str(ctx.person_id or ctx.sender),
            address,
            action,
            origin=_origin(ctx),
            reason=reason,
        )
    except StandbyError:
        return "error: delivery refused"
    except SendRefused as exc:
        return f"error: {exc}"
    except GatewayError as exc:
        return f"error: failed to send text: {exc}"
    if receipt.state == "sent":
        return ""
    record_send(ctx, address.thread, address.thread, "text", str(receipt.reason), ok=False)
    if receipt.state == "pending":
        return f"error: delivery needs approval ({receipt.reason})"
    return f"error: {receipt.reason or f'delivery is {receipt.state}'}"

TOOL = Handler(
    name="send_message",
    family="write",
    target="room",
    approval_class="consent",
    description=(
        "Send text to the active room, another archived room, or a room on another body. "
        "chat = room/contact name (a fragment is fine) or the body's address; gateway = the "
        "body name (empty = this body). A target the archive does not know is accepted only "
        "with a reason from the requester and is evaluated as authorization. Delivery is "
        "idempotent per turn step: a resumed turn never sends the same text twice."
    ),
    fn=_send_message,
    aliases=("send_text",),
    parameters={
        "type": "object",
        "properties": {
            "chat": {
                "type": "string",
                "description": "Room/contact name, partial name, or a direct address.",
            },
            "text": {"type": "string", "description": "Text to send."},
            "gateway": {
                "type": "string",
                "description": "Target body name; empty = this body.",
            },
            "reason": {
                "type": "string",
                "description": (
                    "Why the requester wants this sent; required for a direct unarchived "
                    "target and for a cross-room send."
                ),
            },
            "relay_task": {
                "type": "string",
                "description": "Relay task id when forwarding another person's answer.",
            },
            "relay_from": {
                "type": "string",
                "description": "Person id whose answer is relayed; only they can grant it.",
            },
        },
        "required": ["chat", "text"],
        "additionalProperties": False,
    },
)
