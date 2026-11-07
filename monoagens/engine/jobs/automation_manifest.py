"""Automation manifest: the closed vocabulary a person may author (ADR-0001).

A manifest is `event` -> `condition` -> `steps`, with an owner and a policy (quiet hours,
daily budget). Validation refuses unknown fields and step kinds while authoring; the
authority helpers decide what the owner is entitled to run, checked at creation and again
when the run is due.
"""

import time
from datetime import datetime, timedelta

from ..config import AutomationConfig, parse_quiet_hours
from ..schema import AUTOMATION_STEP_KINDS
from .schedule import (
    DAY,
    ManifestError,
    _at_list,
    _date,
    _days,
    in_quiet,
    zone,
)


class StepError(Exception):
    """One step failed; `transient` decides retry versus a permanent failure."""

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = bool(transient)


class StepSkipped(Exception):
    """The step asked for consent or the condition was not met: a `skipped` run."""


# P3.4 §3: `/task` and the `task` tool speak these three words; the automation event carries
# the marker so a stored row and a listing agree on what the schedule means.
TASK_SCHEDULE_KINDS = ("cron", "at", "goal")
TASK_STEPS: tuple[dict, ...] = ({"kind": "compose"}, {"kind": "send", "to": "owner_dm"})
INTERVAL_UNITS = {"s": 1, "m": 60, "h": 3600, "d": DAY}


def _mapping(value: object) -> dict:
    """A stored JSON field read as a mapping; any other value reads as an empty one."""
    return value if isinstance(value, dict) else {}


def _schedule_event(raw: dict, event: dict, config: AutomationConfig) -> None:
    at = raw.get("at")
    every = raw.get("every")
    if at in (None, "") and every in (None, "", 0):
        raise ManifestError("a schedule needs 'at' or 'every'")
    if at not in (None, "") and every not in (None, "", 0):
        raise ManifestError("a schedule takes 'at' or 'every', not both")
    if at not in (None, ""):
        minutes = _at_list(at)
        event["at"] = [f"{minute // 60:02d}:{minute % 60:02d}" for minute in minutes]
        _days(raw.get("days"))
        event["days"] = (
            "daily" if raw.get("days") in (None, "", "daily") else raw.get("days")
        )
        if len(minutes) > int(config.max_occurrences_per_day):
            raise ManifestError(
                f"at most {config.max_occurrences_per_day} occurrences per day"
            )
        return
    if not isinstance(every, (int, float, str)):
        raise ManifestError("schedule 'every' must be a number")
    interval = int(float(every))
    if interval < int(config.min_interval_seconds):
        raise ManifestError(
            f"the minimum interval is {config.min_interval_seconds} seconds"
        )
    if DAY / interval > int(config.max_occurrences_per_day):
        raise ManifestError(
            f"at most {config.max_occurrences_per_day} occurrences per day"
        )
    event["every"] = interval


def _message_event(raw: dict) -> dict:
    """`event.thread`/`event.sender`: empty means any; a list means those rooms only."""
    thread = raw.get("thread")
    if isinstance(thread, (list, tuple)):
        rooms = [str(item).strip() for item in thread if str(item or "").strip()]
        if not rooms:
            raise ManifestError("event.thread must name at least one chat, or be omitted")
    elif thread in (None, ""):
        rooms = []
    else:
        rooms = [str(thread).strip()]
    sender = raw.get("sender")
    if sender is not None and not isinstance(sender, str):
        raise ManifestError("event.sender must be a person id or omitted")
    return {"thread": rooms, "sender": str(sender or "").strip()}


def _validate_event(raw: object, config: AutomationConfig) -> dict:
    if not isinstance(raw, dict):
        raise ManifestError("event is required")
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in {"schedule", "message"}:
        raise ManifestError("event.kind must be 'schedule' or 'message'")
    event: dict = {"kind": kind, "tz": str(raw.get("tz") or "").strip()}
    zone(event["tz"])
    if kind == "message":
        # ADR-0001 §4.3: the sink queues a run per matching message; there is no clock.
        event.update(_message_event(raw))
        return event
    marker = str(raw.get("schedule_kind") or "").strip().lower()
    if marker:
        if marker not in TASK_SCHEDULE_KINDS:
            raise ManifestError("event.schedule_kind must be 'cron', 'at', or 'goal'")
        event["schedule_kind"] = marker
    _schedule_event(raw, event, config)
    for field in ("start", "until"):
        found = _date(raw.get(field), field)
        if found is not None:
            event[field] = found.isoformat()
    raw_count = raw.get("count")
    if raw_count is not None:
        try:
            count = int(raw_count)
        except (TypeError, ValueError) as exc:
            raise ManifestError("event.count must be a number") from exc
        if count < 1:
            raise ManifestError("event.count must be >= 1")
        event["count"] = count
    return event


FILTER_KINDS = frozenset({"keyword", "sender", "count", "time_between", "never_notified"})


def _validate_condition(raw: object, config: AutomationConfig | None = None) -> dict:
    if raw in (None, {}):
        return {"all": []}
    if not isinstance(raw, dict):
        raise ManifestError("condition must be a mapping")
    if any(key in raw for key in ("all", "any")):
        key = "all" if "all" in raw else "any"
        items = raw.get(key)
        if not isinstance(items, list):
            raise ManifestError(f"condition.{key} must be a list")
        return {key: [_validate_condition(item, config) for item in items]}
    if "not" in raw:
        return {"not": _validate_condition(raw.get("not"), config)}
    kind = str(raw.get("kind") or "").strip().lower()
    if kind == "nl":
        return _nl_condition_spec(raw, config)
    if kind not in FILTER_KINDS:
        raise ManifestError(f"condition kind '{kind}' does not exist")
    if kind == "count":
        return _count_condition(raw)
    if kind == "time_between":
        window = str(raw.get("window") or "")
        parse_quiet_hours(window)
        return {"kind": kind, "window": window}
    return {"kind": kind, "value": str(raw.get("value") or "").strip()}


def _nl_condition_spec(raw: dict, config: AutomationConfig | None) -> dict:
    if config is not None and not config.condition.nl_criteria:
        raise ManifestError("natural-language criteria are disabled in this config")
    text = str(raw.get("text") or "").strip()
    if not text:
        raise ManifestError("an nl condition needs text")
    return {"kind": "nl", "text": text}


def _count_condition(raw: dict) -> dict:
    op = str(raw.get("op") or ">=").strip()
    if op not in {">=", ">", "<=", "<", "=="}:
        raise ManifestError("condition.count op is not supported")
    return {"kind": "count", "op": op, "value": int(raw.get("value") or 0)}


def _validate_steps(raw: object, config: AutomationConfig) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise ManifestError("steps must be a non-empty list")
    if len(raw) > int(config.max_steps):
        raise ManifestError(f"at most {config.max_steps} steps")
    steps: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ManifestError("each step must be a mapping")
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in AUTOMATION_STEP_KINDS:
            raise ManifestError(f"step kind '{kind}' does not exist")
        steps.append(_validate_step(kind, item, config))
    return steps


SIMPLE_STEPS = frozenset(
    {"web_search", "compose", "send", "write", "queue_reflection"}
)


def _validate_step(kind: str, item: dict, config: AutomationConfig) -> dict:
    if kind == "collect":
        return _collect_step(item)
    if kind == "sandbox":
        return _sandbox_step(item, config)
    if kind == "remember":
        return _remember_step(item)
    if kind == "recipe":
        return _recipe_step(item)
    if kind == "relay":
        return _relay_step(item)
    if kind in SIMPLE_STEPS:
        return _simple_step(kind, item)
    raise ManifestError(f"step kind '{kind}' is not implemented")


def _collect_step(item: dict) -> dict:
    return {
        "kind": "collect",
        "chat": str(item.get("chat") or "") or "origin",
        "window": _window(item.get("window")),
        "limit": max(1, min(1000, int(item.get("limit") or 200))),
    }


def _sandbox_step(item: dict, config: AutomationConfig) -> dict:
    program = str(item.get("program") or "").strip()
    if not program:
        raise ManifestError("a sandbox step needs a named program")
    args = item.get("args") or {}
    if not isinstance(args, dict):
        raise ManifestError("sandbox args must be a mapping")
    return {
        "kind": "sandbox",
        "program": _program(program, config),
        "args": {str(key): str(value) for key, value in args.items()},
    }


def _simple_step(kind: str, item: dict) -> dict:
    step: dict = {"kind": kind}
    for key in ("query", "text", "prompt", "path", "scope", "to"):
        if item.get(key) not in (None, ""):
            step[key] = str(item.get(key)).strip()
    return step


def _remember_step(item: dict) -> dict:
    step = _simple_step("remember", item)
    memory_kind = str(item.get("memory_kind") or "note").strip().lower()
    if memory_kind == "rule":
        raise ManifestError("an automation may not write kind=rule")
    if memory_kind not in {"fact", "preference", "event", "note", "commitment"}:
        raise ManifestError("memory_kind is not a memory kind")
    step["memory_kind"] = memory_kind
    if step.get("scope") not in (None, "person", "thread", "global"):
        raise ManifestError("remember scope must be person, thread, or global")
    return step


def _recipe_step(item: dict) -> dict:
    name = str(item.get("name") or "").strip().lower()
    if not name:
        raise ManifestError("a recipe step needs a name")
    return {"kind": "recipe", "name": name, "args": str(item.get("args") or "")}


def _relay_step(item: dict) -> dict:
    step = {
        "kind": "relay",
        "task_id": str(item.get("task_id") or "").strip(),
        "to_person": str(item.get("to_person") or item.get("to") or "").strip(),
        "scope_ref": str(item.get("scope_ref") or "").strip(),
    }
    if item.get("text") not in (None, ""):
        step["text"] = str(item.get("text")).strip()
    if not step["task_id"] or not step["to_person"]:
        raise ManifestError("a relay step needs task_id and to_person")
    return step


def _window(value: object) -> int:
    text = str(value or "last_24h").strip().lower()
    units = {"last_1h": 3600, "last_24h": 86400, "last_7d": 7 * 86400}
    if text in units:
        return units[text]
    try:
        return max(60, int(text))
    except ValueError as exc:
        raise ManifestError(
            "collect window must be last_1h, last_24h, last_7d, or seconds"
        ) from exc


def _program(program: str, config: AutomationConfig) -> str:
    """Only a relative program below `programs_root`; no absolute path, no escape."""
    cleaned = program.strip().replace("\\", "/")
    if cleaned.startswith("/") or cleaned.startswith("~"):
        raise ManifestError("a sandbox program must be relative to the workspace")
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        raise ManifestError("a sandbox program must stay inside the workspace")
    return "/".join(parts)


def _validate_policy(raw: object, event: dict, config: AutomationConfig) -> dict:
    body = raw if isinstance(raw, dict) else {}
    budget = int(body.get("budget_per_day") or config.default_budget_per_day)
    if budget < 1 or budget > int(config.max_occurrences_per_day):
        raise ManifestError(
            f"budget_per_day must be between 1 and {config.max_occurrences_per_day}"
        )
    per_day = _occurrences_per_day(event)
    if per_day > budget:
        raise ManifestError(
            f"{per_day} occurrences per day exceed budget_per_day={budget}; "
            "rejected while authoring, not truncated"
        )
    quiet = str(body.get("quiet_hours") or config.default_quiet_hours).strip()
    parse_quiet_hours(quiet)
    policy = str(body.get("quiet") or config.default_quiet_policy).strip().lower()
    if policy not in {"skip", "defer"}:
        raise ManifestError("policy.quiet must be 'skip' or 'defer'")
    on_error = str(body.get("on_error") or "notify_owner").strip().lower()
    if on_error not in {"notify_owner", "silent"}:
        raise ManifestError("policy.on_error must be 'notify_owner' or 'silent'")
    return {
        "budget_per_day": budget,
        "quiet_hours": quiet,
        "quiet": policy,
        "on_error": on_error,
    }


def _occurrences_per_day(event: dict) -> int:
    if event.get("every"):
        return max(1, int(DAY / int(event["every"])))
    return max(1, len(event.get("at") or []))


def validate_manifest(data: object, config: AutomationConfig) -> dict:
    """The closed manifest: unknown fields and unknown step kinds are refused."""
    if not isinstance(data, dict):
        raise ManifestError("the automation must be a mapping")
    owner = str(data.get("owner") or "").strip()
    if not owner:
        raise ManifestError("owner is required; a promise without an owner has no authority")
    try:
        event = _validate_event(data.get("event"), config)
        steps = _validate_steps(data.get("steps"), config)
        condition = _validated_condition(data.get("condition"), config)
        policy = _validate_policy(data.get("policy"), event, config)
    except ManifestError:
        raise
    except (TypeError, ValueError) as exc:
        raise ManifestError(f"invalid manifest: {exc}") from exc
    if event["kind"] == "schedule":
        for step in steps:
            if step["kind"] == "collect" and step.get("chat") == "origin":
                raise ManifestError(
                    "a schedule has no source room; name the room in collect.chat"
                )
    return {
        "owner": owner,
        "name": str(data.get("name") or "").strip()[:120],
        "event": event,
        "condition": condition,
        "steps": steps,
        "policy": policy,
    }


def _validated_condition(raw: object, config: AutomationConfig) -> dict:
    condition = _validate_condition(raw, config)
    if _has_nl(condition) and condition.get("kind") != "nl":
        raise ManifestError("an nl condition must stand alone, not inside all/any/not")
    return condition


def _has_nl(condition: dict) -> bool:
    if not isinstance(condition, dict) or not condition:
        return False
    if condition.get("kind") == "nl":
        return True
    for key in ("all", "any"):
        if any(_has_nl(item) for item in condition.get(key) or []):
            return True
    nested = condition.get("not")
    return _has_nl(nested) if isinstance(nested, dict) else False


def describe(manifest: dict) -> str:
    """Human data for the approval card: event, steps, target, budget, quiet hours."""
    event = manifest["event"]
    if event["kind"] == "message":
        when = f"message in {event.get('thread') or '-'}"
    else:
        when = (
            f"every {int(event['every'])}s"
            if event.get("every")
            else f"at {', '.join(event.get('at') or [])} ({event.get('days') or 'daily'})"
        )
    steps = " -> ".join(step["kind"] for step in manifest["steps"])
    policy = manifest["policy"]
    return (
        f"{when}; {steps}; budget {policy['budget_per_day']}/day; "
        f"quiet {policy['quiet_hours']} ({policy['quiet']}); owner {manifest['owner']}"
    )


# P3.4 §3: one `/task` request becomes one ordinary automation manifest. The facade adds no
# scheduler, no run table, and no delivery: quiet hours, budgets, and the step log stay in the
# automation engine, and this translation is the only thing a task adds.


def task_manifest(
    spec: object,
    config: AutomationConfig,
    *,
    now: float | None = None,
    timezone: str = "",
) -> dict:
    """Translate one task request into the validated manifest the automation store stores."""
    if not isinstance(spec, dict):
        raise ManifestError("the task must be a mapping")
    kind = str(spec.get("schedule_kind") or "cron").strip().lower()
    if kind not in TASK_SCHEDULE_KINDS:
        raise ManifestError("schedule_kind must be 'cron', 'at', or 'goal'")
    moment = time.time() if now is None else float(now)
    event = _task_event(kind, spec, config, moment, timezone)
    return validate_manifest(
        {
            "owner": str(spec.get("owner") or "").strip(),
            "name": str(spec.get("name") or "").strip(),
            "event": event,
            "condition": _task_condition(kind, spec.get("condition")),
            "steps": spec.get("steps") or list(TASK_STEPS),
            "policy": _task_policy(spec, event, config),
        },
        config,
    )


def _task_event(
    kind: str, spec: dict, config: AutomationConfig, now: float, timezone: str
) -> dict:
    event: dict[str, object] = {
        "kind": "schedule",
        "tz": str(spec.get("timezone") or timezone or "").strip(),
        "schedule_kind": kind,
    }
    if kind == "at":
        return _task_once(spec, event, now)
    if spec.get("at") and spec.get("every"):
        raise ManifestError("a task takes 'at' times or an 'every' interval, not both")
    if spec.get("every"):
        event["every"] = _task_interval(spec.get("every"))
        _task_days(spec, event)
        return event
    if spec.get("at"):
        return _task_clock(spec, event)
    if kind == "goal":
        event["every"] = _default_interval(config)
        return event
    raise ManifestError("a cron task needs 'at' times or an 'every' interval")


def _task_once(spec: dict, event: dict, now: float) -> dict:
    """One absolute moment: `start`/`until` pin the day, `count: 1` stops after it."""
    moment = _task_moment(spec.get("at") or spec.get("on"), event["tz"], now)
    if moment.timestamp() <= float(now):
        raise ManifestError("an 'at' task must name a moment in the future")
    day = moment.date().isoformat()
    event["start"] = day
    event["until"] = day
    event["at"] = [moment.strftime("%H:%M")]
    event["count"] = 1
    return event


def _task_clock(spec: dict, event: dict) -> dict:
    times = _task_times(spec.get("at"))
    if not times:
        raise ManifestError("a cron task needs at least one HH:MM time")
    event["at"] = times
    _task_days(spec, event)
    return event


def _task_days(spec: dict, event: dict) -> None:
    if spec.get("days") not in (None, ""):
        event["days"] = spec["days"]


def _task_moment(value: object, tz: str, now: float):
    if isinstance(value, (list, tuple)):
        if len(value) != 1:
            raise ManifestError("an 'at' task names exactly one moment")
        value = value[0]
    text = str(value or "").strip()
    if not text:
        raise ManifestError("an 'at' task needs a date and time")
    zoneinfo = zone(tz)
    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=zoneinfo)
        return parsed.astimezone(zoneinfo)
    for pattern in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=zoneinfo)
        except ValueError:
            continue
    if len(text) == 5 and text[2] == ":":
        try:
            hour, minute = int(text[:2]), int(text[3:])
        except ValueError as exc:
            raise ManifestError(f"invalid time: {text}") from exc
        today = datetime.fromtimestamp(float(now), zoneinfo)
        moment = today.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if moment <= today:
            moment += timedelta(days=1)
        return moment
    raise ManifestError("the 'at' moment must be YYYY-MM-DD HH:MM")


def _task_times(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value]
    else:
        items = [part.strip() for part in str(value or "").replace(" ", "").split(",")]
    return [item for item in items if item]


def _task_interval(value: object) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    text = str(value or "").strip().lower()
    if not text:
        raise ManifestError("'every' needs a duration such as 30m, 2h, or 1d")
    try:
        return int(float(text))
    except ValueError:
        pass
    unit = INTERVAL_UNITS.get(text[-1])
    if unit is None:
        raise ManifestError("'every' needs seconds or a duration such as 30m, 2h, or 1d")
    try:
        return int(float(text[:-1]) * unit)
    except ValueError as exc:
        raise ManifestError(
            "'every' needs seconds or a duration such as 30m, 2h, or 1d"
        ) from exc


def _default_interval(config: AutomationConfig) -> float:
    """A goal checks often enough to be useful, never more often than the config allows."""
    per_day = max(1, int(config.max_occurrences_per_day))
    return max(3600, DAY // per_day)


def _task_condition(kind: str, raw: object):
    """A string is a natural-language check; the closed filter mapping passes through."""
    if raw in (None, ""):
        if kind == "goal":
            raise ManifestError(
                "a goal task needs a condition; use schedule_kind 'cron' otherwise"
            )
        return None
    condition = {"kind": "nl", "text": str(raw).strip()} if isinstance(raw, str) else raw
    if kind == "goal" and not _condition_present(condition):
        raise ManifestError("a goal task needs a condition that can be checked")
    return condition


def _condition_present(condition: object) -> bool:
    if not isinstance(condition, dict) or not condition:
        return False
    if set(condition) == {"all"}:
        return bool(condition.get("all"))
    if set(condition) == {"any"}:
        return bool(condition.get("any"))
    return True


def _task_policy(spec: dict, event: dict, config: AutomationConfig) -> dict:
    policy = dict(spec["policy"]) if isinstance(spec.get("policy"), dict) else {}
    for key in ("budget_per_day", "quiet_hours", "quiet", "on_error"):
        if spec.get(key) not in (None, ""):
            policy[key] = spec[key]
    if "budget_per_day" not in policy and event.get("every"):
        per_day = max(1, int(DAY / int(event["every"])))
        policy["budget_per_day"] = min(
            int(config.max_occurrences_per_day),
            max(int(config.default_budget_per_day), per_day),
        )
    return policy


def evaluate_condition(condition: dict, facts: dict) -> bool:
    """The closed evaluator: all/any/not over typed filters, no code."""
    if not condition:
        return True
    if "all" in condition:
        return all(evaluate_condition(item, facts) for item in condition["all"])
    if "any" in condition:
        return any(evaluate_condition(item, facts) for item in condition["any"])
    if "not" in condition:
        return not evaluate_condition(condition["not"], facts)
    kind = condition.get("kind")
    if kind == "keyword":
        return condition["value"].lower() in str(facts.get("text") or "").lower()
    if kind == "sender":
        return str(facts.get("sender") or "") == condition["value"]
    if kind == "count":
        found = int(facts.get("count") or 0)
        wanted = int(condition.get("value") or 0)
        return {
            ">=": found >= wanted,
            ">": found > wanted,
            "<=": found <= wanted,
            "<": found < wanted,
            "==": found == wanted,
        }[condition.get("op") or ">="]
    if kind == "time_between":
        window = parse_quiet_hours(condition["window"])
        return in_quiet(float(facts.get("now") or time.time()), facts.get("tz"), window)
    if kind == "never_notified":
        return int(facts.get("notified") or 0) == 0
    return False


# --------------------------------------------------------------------- authority


STEP_TOOLS = {
    "sandbox": "sandbox",
    "web_search": "web_search",
    "write": "write",
    "send": "send",
    "remember": "memory",
    "recipe": "recipe",
}


def _step_authority(
    step: dict,
    manifest: dict,
    *,
    roles: tuple,
    rooms: set[str],
    recipes,
    workspace,
    memory_admin: bool,
) -> list[str]:
    kind = step["kind"]
    errors: list[str] = []
    need = STEP_TOOLS.get(kind)
    if need and not _allowed(roles, need):
        errors.append(f"step {kind} needs the '{need}' tool")
    if kind in {"collect", "send"}:
        target = _target_thread(step, manifest)
        if target and not _owns(rooms, target):
            errors.append(f"{kind} cannot reach {target}: owner is not a member")
    if kind == "remember" and step.get("scope") == "global" and not memory_admin:
        errors.append("a global memory write needs memory_admin")
    if kind == "recipe":
        errors.extend(_recipe_authority(step, roles, recipes))
    if kind == "sandbox":
        errors.extend(_sandbox_authority(step, workspace))
    return errors


def _recipe_authority(step: dict, roles: tuple, recipes) -> list[str]:
    if recipes is None or recipes.get(step["name"]) is None:
        return [f"recipe '{step['name']}' does not exist"]
    recipe = recipes.get(step["name"])
    names = {role.name for role in roles}
    if not (names & set(recipe.roles)):
        return [f"recipe '{step['name']}' is not allowed for the owner"]
    return []


def _sandbox_authority(step: dict, workspace) -> list[str]:
    path = workspace / step["program"]
    try:
        path.resolve().relative_to(workspace.resolve())
    except ValueError:
        return ["sandbox program escapes the workspace"]
    return []


def authority_errors(
    manifest: dict,
    *,
    roles: tuple,
    rooms: set[str],
    recipes=None,
    config: AutomationConfig,
    workspace,
    memory_admin: bool = False,
) -> list[str]:
    """Steps the owner is not entitled to run, checked at creation and again when due."""
    errors: list[str] = []
    for step in manifest["steps"]:
        errors.extend(
            _step_authority(
                step,
                manifest,
                roles=roles,
                rooms=rooms,
                recipes=recipes,
                workspace=workspace,
                memory_admin=memory_admin,
            )
        )
    return errors


def _allowed(roles: tuple, tool: str) -> bool:
    import fnmatch

    return any(
        fnmatch.fnmatchcase(tool, pattern) for role in roles for pattern in role.tools
    )


def _owns(rooms: set[str], target: str) -> bool:
    return str(target) in {str(item) for item in rooms or ()}


def _target_thread(step: dict, manifest: dict) -> str:
    target = str(step.get("to") or "").strip()
    if target in {"", "owner_dm"}:
        return ""
    if target == "origin":
        return str(manifest["event"].get("thread") or "")
    if step["kind"] == "collect" and target == "origin":
        return str(manifest["event"].get("thread") or "")
    return target


def _rooms_of(agent, owner: str, manifest: dict) -> set[str]:
    try:
        rooms = set(agent.history.rooms_of(owner))
    except Exception:
        rooms = set()
    origin = manifest["event"].get("thread")
    if origin:
        rooms.add(str(origin))
    return rooms
