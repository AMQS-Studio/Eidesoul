"""Command /task: schedule, inspect, and stop tasks (P3.4 §3).

One task is one automation manifest plus a `schedule_kind`: `cron` repeats on the clock, `at`
names one future moment, and `goal` checks the world on an interval and notifies only when its
condition holds. The command translates words into that manifest and never schedules anything;
quiet hours, daily budgets, and the step log belong to the automation worker.

When `engine.approval.confirm_tasks` is true, `new` stages the ordinary automation approval and
`/approve <id>` stores the row. Listing and the state changes read the same facade as the
`task` tool, so both surfaces return identical rows.
"""

import re
import shlex

from ..i18n import t
from ..jobs.schedule import ManifestError
from ..registry import Handler
from ..stores.jobs import JobStore, render
from . import CommandContext

VALUE_FLAGS = frozenset({"at", "every", "days", "budget", "quiet", "tz", "on", "check"})
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CLOCK = re.compile(r"^\d{2}:\d{2}$")
POLICY_FLAGS = frozenset({"skip", "defer"})
CHANGES = frozenset({"pause", "resume", "stop", "cancel"})


def parse_spec(rest: str) -> dict:
    """The `new` body: every word before the first flag is the task name."""
    try:
        words = shlex.split(rest)
    except ValueError as exc:
        raise ManifestError(f"the task could not be parsed: {exc}") from exc
    name: list[str] = []
    spec: dict = {}
    index = 0
    while index < len(words):
        word = words[index].lower()
        if word in POLICY_FLAGS:
            spec["quiet"] = word
            index += 1
        elif word in VALUE_FLAGS:
            value, used = _value(words, index, word)
            _apply(spec, word, value)
            index += used
        else:
            name.append(words[index])
            index += 1
    spec["name"] = " ".join(name).strip()
    if not spec["name"]:
        raise ManifestError("a new task needs a name")
    return spec


def _value(words: list[str], index: int, flag: str) -> tuple[str, int]:
    """`on 2026-12-01 09:00` is one value; every other flag takes one word."""
    if index + 1 >= len(words):
        raise ManifestError(f"{flag} needs a value")
    value = words[index + 1]
    following = words[index + 2] if index + 2 < len(words) else ""
    if flag == "on" and DATE.fullmatch(value) and CLOCK.fullmatch(following):
        return f"{value} {following}", 3
    return value, 2


def _apply(spec: dict, flag: str, value: str) -> None:
    if flag == "check":
        spec["condition"] = {"kind": "nl", "text": value}
        spec["schedule_kind"] = "goal"
    elif flag == "on":
        spec["at"] = value
        spec["schedule_kind"] = "at"
    elif flag == "at":
        spec["at"] = value
    elif flag == "every":
        spec["every"] = value
    elif flag == "days":
        spec["days"] = value
    elif flag == "budget":
        try:
            spec["budget_per_day"] = int(value)
        except ValueError as exc:
            raise ManifestError("budget needs a whole number") from exc
    elif flag == "quiet":
        spec["quiet_hours"] = value
    elif flag == "tz":
        spec["timezone"] = value


def _task_id(rest: str) -> int:
    text = rest.strip()
    if not text.isdigit():
        raise ManifestError("a task id is required, e.g. /task pause 3")
    return int(text)


def _change(store: JobStore, verb: str, rest: str, ctx: CommandContext, lang: str) -> str:
    task_id = _task_id(rest)
    if verb == "pause":
        row = store.pause(task_id, actor=ctx.sender)
        return t("command.task.paused", lang, id=row["id"])
    if verb == "resume":
        row = store.resume(task_id, actor=ctx.sender)
        return t("command.task.resumed", lang, id=row["id"])
    row = store.cancel(task_id, actor=ctx.sender)
    return t("command.task.cancelled", lang, id=row["id"])


async def _dispatch(
    ctx: CommandContext, store: JobStore, verb: str, rest: str, lang: str
) -> str:
    if verb in ("list", "ls"):
        return render(store.rows(ctx.sender), lang)
    if verb == "new":
        spec = parse_spec(rest)
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
    if verb in CHANGES:
        return _change(store, verb, rest, ctx, lang)
    return t("command.task.usage", lang)


async def task(ctx: CommandContext) -> str:
    lang = ctx.lang
    store = JobStore(ctx.agent.db, ctx.agent.config.automations)
    verb, _, rest = ctx.args.strip().partition(" ")
    try:
        return await _dispatch(ctx, store, (verb or "list").lower(), rest.strip(), lang)
    except (ManifestError, ValueError, TypeError, OSError) as exc:
        return f"error: {exc}"


COMMAND = Handler(
    name="task",
    description="Schedule, list, pause, resume, or stop tasks.",
    fn=task,
    aliases=("tasks",),
    usage=(
        "[list | new <name> [at HH:MM[,HH:MM] | every 30m | on <YYYY-MM-DD HH:MM> | "
        "check <text>] [days mon,wed] [budget N] [quiet HH:MM-HH:MM] [skip|defer] [tz ZONE] "
        "| pause <id> | resume <id> | stop <id>]"
    ),
)
