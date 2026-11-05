"""Task facade: `/task` and the `task` tool speak here (P3.4 §3).

One task is still one `automations` row: the facade translates a task request into the
manifest the automation engine already validates, and delegates creation, pause/resume/stop,
and every run to it. Nothing in this module materializes an occurrence, claims a run, counts
a budget, applies quiet hours, or delivers a message; `AutomationWorker` stays the one
scheduler, and `automation_runs.step_index` stays the one step log.

`schedule_kind` is the only vocabulary this facade adds: `cron` is a clock schedule, `at` is a
single future moment, and `goal` is a repeated check whose condition decides whether a run
notifies. When `engine.approval.confirm_tasks` is true, creating a task stages the ordinary
automation approval (`/approve <id>`) instead of storing the row.
"""

import time
from datetime import datetime
from types import SimpleNamespace

from ..config import AutomationConfig
from ..i18n import t
from ..jobs.automation_manifest import TASK_SCHEDULE_KINDS, task_manifest
from ..jobs.automation_store import AutomationStore
from ..jobs.schedule import ManifestError, zone

CREATED = "created"
STAGED = "staged"
PENDING_STATES = ("active", "paused")


class JobStore:
    """Read and write tasks; the automation store and worker do the scheduling."""

    def __init__(self, db, config: AutomationConfig) -> None:
        self.db = db
        self.config = config
        self.automations = AutomationStore(db, config)

    # ----------------------------------------------------------------- read

    def rows(self, owner: str, *, states: tuple[str, ...] = ()) -> list[dict]:
        found = self.automations.by_owner(str(owner), states=states)
        return [task_row(row) for row in found]

    def pending(self, owner: str) -> list[dict]:
        return self.rows(owner, states=PENDING_STATES)

    def get(self, task_id: int | str) -> dict | None:
        row = self.automations.get(task_id)
        return task_row(row) if row is not None else None

    # ---------------------------------------------------------------- write

    async def create(
        self,
        agent,
        spec: dict,
        *,
        thread_id: str,
        gateway=None,
        now: float | None = None,
    ) -> dict:
        """Create one task, or stage the approval that must exist first.

        The returned mapping says which happened: `staged` carries the approval id and the
        summary a chat shows, `created` carries the stored row.
        """
        moment = time.time() if now is None else float(now)
        manifest = task_manifest(
            spec,
            self.config,
            now=moment,
            timezone=str(agent.config.soul.timezone),
        )
        if self.confirm(agent):
            approval, summary = await self.automations.propose(
                agent, manifest, thread_id=str(thread_id), gateway=gateway
            )
            return {
                "state": STAGED,
                "task": None,
                "approval_id": int(approval.id),
                "summary": summary,
                "manifest": manifest,
            }
        created = self.automations.create(
            manifest,
            approval=_auto_grant(manifest["owner"]),
            agent=agent,
            now=moment,
        )
        stored = self.automations.get(created)
        if stored is None:  # pragma: no cover - the row was inserted one statement ago
            raise ManifestError("the task row could not be read back")
        return {
            "state": CREATED,
            "task": task_row(stored),
            "approval_id": 0,
            "summary": "",
            "manifest": manifest,
        }

    def pause(self, task_id: int | str, *, actor: str, now: float | None = None) -> dict:
        return self._set(task_id, "paused", actor=actor, now=now, reason="paused by owner")

    def resume(self, task_id: int | str, *, actor: str, now: float | None = None) -> dict:
        return self._set(task_id, "active", actor=actor, now=now, reason="resumed by owner")

    def cancel(self, task_id: int | str, *, actor: str, now: float | None = None) -> dict:
        return self._set(task_id, "stopped", actor=actor, now=now, reason="stopped by owner")

    def _set(
        self,
        task_id: int | str,
        state: str,
        *,
        actor: str,
        now: float | None,
        reason: str,
    ) -> dict:
        row = self.automations.get(task_id)
        if row is None:
            raise ManifestError(f"task #{task_id} does not exist")
        if str(row["owner"]) != str(actor):
            raise ManifestError("only the owner may change this task")
        updated = self.automations.set_state(
            task_id, state, reason=reason, actor=str(actor), now=now
        )
        if updated is None:  # pragma: no cover - the row was read one statement ago
            raise ManifestError(f"task #{task_id} does not exist")
        return task_row(updated)

    def confirm(self, agent) -> bool:
        """`engine.approval.confirm_tasks`: a task is a wide-reaching schedule, so it stages."""
        approval = getattr(getattr(agent, "config", None), "approval", None)
        return bool(getattr(approval, "confirm_tasks", False))


def _auto_grant(owner: str):
    """The approval-shaped grant used when the operator already trusted task creation.

    Nothing is written: the row records `approval_id = 0`, which is exactly the fact that no
    approval was needed.
    """
    return SimpleNamespace(id=0, kind="automation", sender=str(owner), status="approved")


def schedule_kind(event: dict) -> str:
    """The task vocabulary of a stored row; older rows derive it from their shape."""
    marker = str(event.get("schedule_kind") or "").strip().lower()
    if marker in TASK_SCHEDULE_KINDS:
        return marker
    if str(event.get("kind") or "") == "message":
        return "goal"
    if int(event.get("count") or 0) == 1:
        return "at"
    return "cron"


def task_row(row: dict) -> dict:
    """One stored automation rendered as a task: no new state, only a named view."""
    event: dict = row["event"] if isinstance(row.get("event"), dict) else {}
    condition: dict = row["condition"] if isinstance(row.get("condition"), dict) else {}
    policy: dict = row["policy"] if isinstance(row.get("policy"), dict) else {}
    steps: list = row["steps"] if isinstance(row.get("steps"), list) else []
    return {
        "id": int(row["id"]),
        "owner": str(row["owner"]),
        "name": str(row.get("name") or ""),
        "state": str(row.get("state") or ""),
        "schedule_kind": schedule_kind(event),
        "event": event,
        "condition": condition,
        "steps": steps,
        "budget_per_day": int(policy.get("budget_per_day") or 0),
        "quiet_hours": str(policy.get("quiet_hours") or ""),
        "quiet": str(policy.get("quiet") or ""),
        "next_occurrence": float(row.get("next_occurrence") or 0.0),
    }


def when_text(row: dict) -> str:
    """The next occurrence in the task's own zone; `-` when the schedule is finished."""
    moment = float(row.get("next_occurrence") or 0.0)
    if not moment:
        return "-"
    event: dict = row["event"] if isinstance(row.get("event"), dict) else {}
    try:
        return datetime.fromtimestamp(moment, zone(event.get("tz"))).strftime(
            "%Y-%m-%d %H:%M"
        )
    except (ValueError, KeyError, OSError):
        return "-"


def render(rows: list[dict], lang: str) -> str:
    """The one row rendering `/task list` and the `task` tool both return."""
    if not rows:
        return t("command.task.none", lang)
    return "\n".join(
        t(
            "command.task.row",
            lang,
            id=row["id"],
            state=row["state"],
            kind=row["schedule_kind"],
            when=when_text(row),
            budget=row["budget_per_day"],
            name=row["name"] or "-",
        )
        for row in rows
    )
