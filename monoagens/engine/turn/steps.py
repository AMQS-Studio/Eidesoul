"""Turn step bookkeeping: the tool-step writer and the resume lookup.

ADR-0037 §4.3: the writer the model's tool loop uses, and the lookup a resumed turn reads
before it repeats a side-effecting call. The row is opened as `running` *before* the tool runs
and finalized after, so a crash leaves evidence instead of a gap (ADR-0003 §4.3). The service
never imports `agent.py`; the turn store and the tool registry are injected.
"""


import logging
from typing import Any

from ..stores.turns import STEP_EXECUTED, STEP_INTERRUPTED, args_hash

log = logging.getLogger(__name__)


class SideEffectAlreadyRan(RuntimeError):
    """A side-effecting tool already executed; the turn is interrupted, never repeated."""


class StepWriter:
    """The step row of one tool loop: `begin` before the call, the instance itself after it."""

    def __init__(self, store, turn_id: str, step: str) -> None:
        self.store = store
        self.turn_id = str(turn_id)
        self.step = str(step)
        self.index: int | None = None

    def begin(
        self, tool: str, args, *, target: str = "", approval_class: str = "none"
    ) -> int:
        """Open the row as `running`; return its index (-1 when the write failed)."""
        try:
            self.index = int(
                self.store.begin_step(
                    self.turn_id,
                    step=self.step,
                    tool=str(tool),
                    args=args,
                    target=str(target),
                    approval_class=str(approval_class),
                )
            )
        except Exception:
            log.exception("failed to open a tool step turn=%s tool=%s", self.turn_id, tool)
            self.index = None
            return -1
        return self.index

    def __call__(
        self,
        tool,
        args,
        result,
        started,
        finished,
        status: str = STEP_EXECUTED,
        target="",
        approval_class="none",
    ) -> None:
        """Finalize the row the `begin` call opened."""
        if self.index is None or self.index < 0:
            return
        try:
            self.store.finish_step(
                self.turn_id,
                self.index,
                result=result,
                status=str(status or STEP_EXECUTED),
                finished_at=finished,
            )
        except Exception:
            log.exception(
                "failed to record a tool step turn=%s tool=%s", self.turn_id, tool
            )
        finally:
            self.index = None


class TurnSteps:
    """Step bookkeeping for one turn: the writer and the resume lookup.

    The explicit form passes the turn store and the tool registry; the legacy form passes the
    host, whose `turns` and `tools` are read once here instead of on every lookup.
    """

    def __init__(self, turns: Any, tools: Any = None) -> None:
        self.turns = getattr(turns, "turns", turns)
        self.tools = tools if tools is not None else getattr(turns, "tools", None)

    def recorder(self, turn_id: str, step: str) -> StepWriter:
        """The writer one tool loop uses: call `begin` before the tool, then the writer itself."""
        return StepWriter(self.turns, turn_id, step)

    def lookup(self, turn_id: str):
        def lookup(tool, args):
            found = self.turns.find_step(turn_id, str(tool), args_hash(tool, args))
            if found is None:
                return None
            status = str(found.get("status") or "")
            spec = (
                self.tools.by_name.get(str(tool)) if self.tools is not None else None
            )
            side_effecting = str(found.get("approval_class") or "none") != "none" or (
                spec is not None and spec.approval_class != "none"
            )
            if status == STEP_INTERRUPTED:
                if side_effecting:
                    # The tool was mid-call when the worker died; it may have sent already.
                    raise SideEffectAlreadyRan(
                        f"{tool} was running when the turn stopped; refusing to repeat it"
                    )
                return None
            if status != STEP_EXECUTED:
                return None
            if side_effecting:
                raise SideEffectAlreadyRan(
                    f"{tool} already executed in this turn; refusing to repeat it"
                )
            return str(found["result"] or "")

        return lookup
