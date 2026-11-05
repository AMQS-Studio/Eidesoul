"""Natural confirmation for staged actions (ADR-0006 §4.5), kept out of the state machine."""

from typing import TYPE_CHECKING

from .. import io
from ..i18n import t
from .confirmations import confirmation_intent, pending_result

if TYPE_CHECKING:
    from .runtime import TurnRuntime
    from .state import TurnState

class ApprovalFlow:
    """Confirm, reject, or supersede staged actions before the model decides."""

    runtime: TurnRuntime

    async def _resume_approvals(self, state: TurnState) -> str:
        """Natural confirmation before the decision (ADR-0006 §4.5).

        The reply is classified, then the staged rows are decided in Python: a confirm
        re-verifies `scope_proof` and RBAC and runs them, a reject cancels them, and a modify
        supersedes them so the model stages the updated action in this same turn.
        """
        runtime = self.runtime
        if not runtime.config.approval.natural_confirm:
            return ""
        sender = state.sender_id
        rows = await io.run(runtime.approvals.pending, state.thread_id, sender)
        if not rows:
            return ""
        intent = confirmation_intent(
            state.message.text, self.runtime.soul.language
        )
        if not intent:
            return pending_result(rows, "waiting")
        selected = await io.run(runtime.approvals.batch, state.thread_id, sender) or rows
        if intent == "reject":
            for row in selected:
                await io.run(runtime.approvals.mark, row.id, "denied", sender, "natural reject")
            return pending_result(selected, "cancelled")
        if intent == "modify":
            await io.run(
                runtime.approvals.supersede,
                [row.id for row in selected],
                sender,
                "natural modify",
            )
            return pending_result(selected, "superseded")
        receipts: dict[int, str] = {}
        for row in selected:
            text, _ran = await runtime.approve(
                row,
                sender=sender,
                roles=state.role_policy,
                gateway=state.gateway,
                thread_id=state.thread_id,
            )
            receipts[row.id] = text
        return pending_result(selected, "approved", receipts)

    async def _approval_badge(
        self, reply: str, thread_id: str, sender: str, since: float
    ) -> str:
        """The short `/approve <id>` badge under the reply that parked an action."""
        if not self.runtime.config.approval.natural_confirm:
            return reply
        rows = await io.run(self.runtime.approvals.pending, thread_id, sender)
        fresh = [row for row in rows if float(row.created_at) >= since - 2.0]
        if not fresh:
            return reply
        badge = t("agent.approval_badge", self.runtime.soul.language, id=fresh[-1].id)
        return f"{reply}\n\n{badge}" if reply.strip() else badge
