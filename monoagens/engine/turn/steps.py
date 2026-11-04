"""Turn step bookkeeping: the tool-step recorder and the resume lookup.

ADR-0037 §4.3: the recorder the model's tool loop writes, and the lookup a resumed turn reads
before it repeats a side-effecting call. The service never imports `agent.py`; the host is the
`Agent` it is bound to.
"""


import logging

from ..stores.turns import args_hash

log = logging.getLogger(__name__)


class SideEffectAlreadyRan(RuntimeError):
    """A side-effecting tool already executed; the turn is interrupted, never repeated."""


class TurnSteps:
    """Step bookkeeping for one turn; the host is the `Agent`."""

    def __init__(self, agent) -> None:
        self.agent = agent

    def __getattr__(self, name: str):
        return getattr(self.agent, name)

    def recorder(self, turn_id: str, step: str):
        def record(
            tool,
            args,
            result,
            started,
            finished,
            status: str = "executed",
            target="",
            approval_class="none",
        ):
            try:
                self.turns.record_step(
                    turn_id,
                    step=step,
                    tool=str(tool),
                    args=args,
                    result=result,
                    target=str(target),
                    approval_class=str(approval_class),
                    status=str(status or "executed"),
                    started_at=started,
                    finished_at=finished,
                )
            except Exception:
                log.exception("failed to record a tool step turn=%s tool=%s", turn_id, tool)

        return record

    def lookup(self, turn_id: str):
        def lookup(tool, args):
            found = self.turns.find_step(turn_id, str(tool), args_hash(tool, args))
            if found is None:
                return None
            if found["status"] != "executed":
                return None
            spec = None
            if getattr(self, "tools", None) is not None:
                spec = self.tools.by_name.get(str(tool))
            if found.get("approval_class", "none") != "none" or (
                spec is not None and spec.approval_class != "none"
            ):
                raise SideEffectAlreadyRan(
                    f"{tool} already executed in this turn; refusing to repeat it"
                )
            return str(found["result"] or "")

        return lookup

