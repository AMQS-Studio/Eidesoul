"""Tool task: the same verbs as `/task`, for a conversational request (P3.4 §3).

The tool and the command share `JobStore`, so a listing returned to the model and a listing
returned to a chat are byte-identical rows. `schedule_kind` decides what the clock means:
`cron` repeats, `at` names one future moment, and `goal` checks the world and notifies only
when its condition holds. Nothing here schedules a run; the automation worker owns that.
"""

from ..i18n import t
from ..jobs.schedule import ManifestError
from ..registry import Handler
from ..stores.jobs import JobStore, render
from . import ToolContext

VERBS = frozenset({"new", "list", "pause", "resume", "cancel", "stop"})
SPEC_KEYS = (
    "name",
    "schedule_kind",
    "at",
    "days",
    "every",
    "timezone",
    "condition",
    "budget_per_day",
    "quiet_hours",
    "quiet",
    "steps",
)


def _spec(args: dict) -> dict:
    spec: dict = {}
    for key in SPEC_KEYS:
        value = args.get(key)
        if value not in (None, "", [], {}):
            spec[key] = value
    return spec


async def _change(
    store: JobStore, action: str, args: dict, ctx: ToolContext, lang: str
) -> str:
    task_id = args.get("task_id")
    if not isinstance(task_id, int) or isinstance(task_id, bool):
        raise ManifestError("task_id is required for pause, resume, and cancel")
    if action == "pause":
        row = store.pause(task_id, actor=ctx.sender)
        return t("command.task.paused", lang, id=row["id"])
    if action == "resume":
        row = store.resume(task_id, actor=ctx.sender)
        return t("command.task.resumed", lang, id=row["id"])
    row = store.cancel(task_id, actor=ctx.sender)
    return t("command.task.cancelled", lang, id=row["id"])


async def _task(args: dict, ctx: ToolContext) -> str:
    if ctx.agent is None:
        return "error: tasks need a running agent"
    store = JobStore(ctx.agent.db, ctx.config.automations)
    lang = str(ctx.config.soul.language)
    action = str(args.get("action") or "list").strip().lower()
    try:
        if action in ("list", "ls"):
            return render(store.rows(ctx.sender), lang)
        if action == "new":
            spec = _spec(args)
            spec["owner"] = ctx.sender
            result = await store.create(
                ctx.agent, spec, thread_id=ctx.thread_id, gateway=ctx.gateway
            )
            if result["state"] == "staged":
                return t(
                    "command.task.staged",
                    lang,
                    id=result["approval_id"],
                    summary=result["summary"],
                )
            row = result["task"]
            return t("command.task.created", lang, id=row["id"], name=row["name"] or "-")
        if action in VERBS:
            return await _change(store, action, args, ctx, lang)
        return "error: action must be list, new, pause, resume, or cancel"
    except (ManifestError, ValueError, TypeError, OSError) as exc:
        return f"error: {exc}"


TOOL = Handler(
    name="task",
    target="",
    approval_class="consent",
    destructive=("cancel",),
    family="write",
    description=(
        "Tasks: list, create, pause, resume, or cancel a scheduled reminder, a one-off "
        "moment, or a goal check that notifies only when a condition holds. A goal task "
        "needs a condition; use schedule_kind cron for a plain schedule."
    ),
    fn=_task,
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "new", "pause", "resume", "cancel", "stop"],
                "description": "list, new, pause, resume, cancel (or stop).",
            },
            "task_id": {
                "type": "integer",
                "description": "Task id, required to pause, resume, or cancel.",
            },
            "name": {"type": "string", "description": "Short task name."},
            "schedule_kind": {
                "type": "string",
                "enum": ["cron", "at", "goal"],
                "description": "cron repeats, at is one future moment, goal checks then notifies.",
            },
            "at": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "HH:MM times (for cron, or next occurrence of HH:MM for at), "
                    "or YYYY-MM-DD HH:MM."
                ),
            },
            "days": {
                "type": "string",
                "description": "daily, a day list (mon,wed), or every:N.",
            },
            "every": {
                "type": "string",
                "description": "Interval for cron/goal (at least 1h, e.g. 1h, 2h, or 1d).",
            },
            "timezone": {"type": "string", "description": "IANA zone; defaults to the soul."},
            "condition": {
                "type": "string",
                "description": "For goal: notify only when this check holds, in plain words.",
            },
            "budget_per_day": {
                "type": "integer",
                "minimum": 1,
                "description": "Runs allowed per day; defaults to a safe cap.",
            },
            "quiet_hours": {
                "type": "string",
                "description": "HH:MM-HH:MM window that never notifies.",
            },
            "quiet": {
                "type": "string",
                "enum": ["skip", "defer"],
                "description": "skip a quiet occurrence, or defer it to the window end.",
            },
            "steps": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Advanced: override the default compose then send steps.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    },
)
