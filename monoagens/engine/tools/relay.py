"""Tool relay: the content owner's grants for forwarding an answer (ADR-0023).

The tool acts only on the caller's own grants: `grant` lets one room carry the caller's answer
for one scope, `list` shows the active ones, `revoke` stops the next report. A grant never holds
message content, only the decision, the mode, and the scope.
"""


import time

from ..access.relay import DEFAULT_EXPIRES_DAYS, MODES, RelayRefused, RelayStore
from ..registry import Handler
from . import ToolContext

ACTIONS = ("grant", "revoke", "list")


def _until(value: float | None) -> str:
    return (
        time.strftime("%Y-%m-%d %H:%M", time.gmtime(float(value))) if value is not None else "-"
    )


def _days(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return DEFAULT_EXPIRES_DAYS
    if text.lower() in {"never", "forever", "0"}:
        return None
    return float(text)


def _rows(grants) -> str:
    return "\n".join(
        f"- {grant.to_person} · {grant.mode} · {grant.scope_ref or '-'}"
        f" · until {_until(grant.expires_at)}"
        for grant in grants
    )


async def _relay(args: dict, ctx: ToolContext) -> str:
    if ctx.agent is None:
        return "error: the relay store is not available"
    owner = str(ctx.person_id or ctx.sender or "").strip()
    if not owner:
        return "error: no sender identity; a relay grant belongs to a person"
    store = RelayStore(ctx.agent.db)
    action = str(args.get("action") or "list").strip().lower()
    if action not in ACTIONS:
        return f"error: action must be one of: {', '.join(ACTIONS)}"
    if action == "list":
        grants = store.listing(from_person=owner)
        return _rows(grants) if grants else "No relay grant from this person yet."
    target = str(args.get("to") or "").strip()
    if not target:
        return "error: to is required"
    scope = str(args.get("scope") or ctx.thread_id or "").strip()
    try:
        if action == "revoke":
            revoked = store.revoke(
                task_id=f"scope:{scope}",
                from_person=owner,
                to_person=target,
                by=owner,
            )
            return (
                f"ok: relay grant to {target} in {scope or '-'} revoked"
                if revoked
                else f"no active relay grant to {target} in {scope or '-'}"
            )
        grant = store.grant(
            from_person=owner,
            to_person=target,
            by=owner,
            mode=str(args.get("mode") or "summary"),
            scope_ref=scope,
            days=_days(args.get("days")),
        )
    except RelayRefused as exc:
        return f"error: {exc}"
    except ValueError as exc:
        return f"error: {exc}"
    return (
        f"ok: relay granted to {grant.to_person} for {grant.scope_ref or '-'} "
        f"({grant.mode}, until {_until(grant.expires_at)})"
    )


TOOL = Handler(
    name="relay",
    family="write",
    target="room",
    approval_class="consent",
    max_output_chars=4000,
    description=(
        "Manage the caller's relay grants: grant one room the right to carry the caller's "
        f"answer for one scope, list the active grants, or revoke one. Modes: {', '.join(MODES)}. "
        "Only the content owner can grant or revoke; a grant expires."
    ),
    fn=_relay,
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(ACTIONS),
                "description": "grant, revoke, or list.",
            },
            "to": {
                "type": "string",
                "description": "Person id whose room will carry the answer (grant/revoke).",
            },
            "mode": {
                "type": "string",
                "enum": list(MODES),
                "description": "What may be forwarded; default summary.",
            },
            "scope": {
                "type": "string",
                "description": "Room the grant covers; default the active thread.",
            },
            "days": {
                "type": "string",
                "description": "Validity in days, or 'never'; default 30.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    },
)
