"""Automation: a declarative promise owned by a person (ADR-0014).

A row is `event` → `condition` → `steps`, with a closed step vocabulary, an owner, a policy
(quiet hours, daily budget), and a state. The worker claims due occurrences with
`SKIP LOCKED`, fences them with the lease `generation`, and records every run. The occurrence
key (`hash(automation_id, scheduled_at_utc)`) is unique, so a duplicate tick, restart, or
takeover cannot double a run.

The steps are deliberately narrow. Only `collect` reads the archive, and it checks the owner's
presence in the source room first; `send` goes through the outbound ledger
(`scope_key = automation:<run_id>`) so a retry cannot double-send; `relay` asks through
`RelayStore.ask` with the run deadline as `expires_at` and refuses while there is no grant; a
`sandbox` step runs one named program inside the workspace, never a command created when due.

No step is free code. Unexpressible wishes are refused at authoring, not mapped onto weak
keywords.
"""


import asyncio
import contextlib
import logging
import os
import random
import re
import shlex
import time
from datetime import date, datetime, timedelta
from datetime import time as clock_time

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .. import io
from ..access.relay import RelayStore
from ..config import AutomationConfig, parse_quiet_hours
from ..db import Database
from ..i18n import t
from ..inbound import Action, Address, GatewayError
from ..schema import (
    AUTOMATION_STATES,
    AUTOMATION_STEP_KINDS,
    AUTOMATION_TERMINAL,
    automation_runs,
    automation_state_log,
    automations,
)
from ..tools import execute
from ..turn import observability
from .schedule import (
    DAY,
    DEFAULT_TZ,
    ManifestError,
    _at_list,
    _date,
    _days,
    _first_occurrence,
    in_quiet,
    next_after,
    occurrence_key,
    quiet_end,
    zone,
)

log = logging.getLogger(__name__)

TOOL_STEPS = {"web_search": "web_search", "write": "write", "remember": "memory"}
DETAIL_CHARS = 2000
NL_JUDGEMENT_TOKENS = 100  # the daily NL budget is charged per judgement at its ceiling
_RETRY_AFTER = re.compile(r"retry[- ]after[: ]+(\d+)", re.IGNORECASE)
COLLECT_CHARS = 12000
COMPOSE_CHARS = 4000
MATERIALIZE_CAP = 100


class StepError(Exception):
    """One step failed; `transient` decides retry versus a permanent failure."""

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = bool(transient)


class StepSkipped(Exception):
    """The step asked for consent or the condition was not met: a `skipped` run."""


# --------------------------------------------------------------------- clock helpers


# --------------------------------------------------------------------- manifest


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
    event: dict = {"kind": kind, "tz": str(raw.get("tz") or DEFAULT_TZ).strip()}
    zone(event["tz"])
    if kind == "message":
        # ADR-0014 §4.3: the sink queues a run per matching message; there is no clock.
        event.update(_message_event(raw))
        return event
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


# --------------------------------------------------------------------- store


class AutomationStore:
    """The row, its runs, and the transition log; no delivery lives here."""

    def __init__(self, db: Database, config: AutomationConfig) -> None:
        self.db = db
        self.config = config

    # ---------------------------------------------------------------- read

    def get(self, automation_id: int | str) -> dict | None:
        return self.db.fetch_one(
            select(automations).where(automations.c.id == int(automation_id))
        )

    def runs(self, automation_id: int | str, limit: int = 50) -> list[dict]:
        return self.db.fetch_all(
            select(automation_runs)
            .where(automation_runs.c.automation_id == int(automation_id))
            .order_by(automation_runs.c.id.desc())
            .limit(max(1, int(limit)))
        )

    def transitions(self, automation_id: int | str) -> list[dict]:
        return self.db.fetch_all(
            select(automation_state_log)
            .where(automation_state_log.c.automation_id == int(automation_id))
            .order_by(automation_state_log.c.id)
        )

    # --------------------------------------------------------------- write

    async def propose(self, agent, manifest: dict, *, thread_id: str, gateway=None):
        """Create the approval that must exist before a row is stored (ADR-0014 §4.8)."""
        body = validate_manifest(manifest, self.config)
        summary = describe(body)
        approval = await agent.approvals.create(
            kind="automation",
            thread_id=str(thread_id),
            sender=str(body["owner"]),
            gateway=getattr(gateway, "name", "") or "",
            tool="",
            args={"manifest": body},
            payload={"manifest": body, "summary": summary},
            summary=summary,
            roles=tuple(role.name for role in agent.config.roles.for_person(body["owner"])),
        )
        return approval, summary

    def create(
        self,
        manifest: dict,
        *,
        approval,
        agent,
        now: float | None = None,
        approved_now: bool = False,
    ) -> int:
        """Store the row only after an approval; authority is checked here too.

        `approved_now` is set by `/approve` itself: the command is the approval act, and the
        row is not stored until that command runs. Every other caller must present an
        approval whose stored status is already `approved`.
        """
        body = validate_manifest(manifest, self.config)
        status = getattr(approval, "status", "")
        if approval is None or (status != "approved" and not approved_now):
            raise ManifestError("an automation needs an approved approval before it is stored")
        if str(getattr(approval, "kind", "")) != "automation":
            raise ManifestError("the approval is not an automation approval")
        if str(getattr(approval, "sender", "")) != body["owner"]:
            raise ManifestError("the approval belongs to another person")
        roles = agent.config.roles.for_person(body["owner"])
        rooms = _rooms_of(agent, body["owner"], body)
        errors = authority_errors(
            body,
            roles=roles,
            rooms=rooms,
            recipes=getattr(agent, "recipes", None),
            config=self.config,
            workspace=agent.config.workspace,
            memory_admin=any(role.memory_admin for role in roles),
        )
        if errors:
            raise ManifestError("; ".join(errors))
        active = self.db.fetch_all(
            select(automations.c.id).where(
                automations.c.owner == body["owner"], automations.c.state == "active"
            )
        )
        if len(active) >= int(self.config.max_active_per_person):
            raise ManifestError(
                f"at most {self.config.max_active_per_person} active automations per person"
            )
        moment = time.time() if now is None else float(now)
        event = body["event"]
        created = moment
        first = _first_occurrence(event, moment, created)
        ends = _until_epoch(event)
        row = {
            "owner": body["owner"],
            "name": body["name"],
            "event": event,
            "condition": body["condition"],
            "steps": body["steps"],
            "policy": body["policy"],
            "state": "active",
            "reason": "",
            "owner_roles_at_creation": [role.name for role in roles],
            "approval_id": int(getattr(approval, "id", 0) or 0),
            "next_occurrence": float(first if first is not None else 0.0),
            "fire_count": 0,
            "ends_at": ends,
            "created_at": created,
            "updated_at": moment,
        }
        with self.db.connect() as conn:
            found = conn.execute(automations.insert().values(row).returning(automations.c.id))
            new_id = found.scalar()
            if new_id is None:  # pragma: no cover - the insert always returns its own row
                raise RuntimeError("the new automation could not be read back")
            automation_id = int(new_id)
            conn.execute(
                automation_state_log.insert().values(
                    automation_id=automation_id,
                    from_state="draft",
                    to_state="active",
                    reason="approved",
                    actor=str(body["owner"]),
                    at=moment,
                )
            )
        log.info(
            "automation created id=%s owner=%s next=%s",
            automation_id,
            body["owner"],
            row["next_occurrence"],
        )
        return automation_id

    def set_state(
        self,
        automation_id: int | str,
        state: str,
        *,
        reason: str = "",
        actor: str = "",
        now: float | None = None,
    ) -> dict | None:
        """One legal transition, one log row; terminal states never reopen (ADR-0014 §4.13)."""
        target = str(state or "").strip().lower()
        if target not in AUTOMATION_STATES:
            raise ManifestError(f"automation state '{target}' does not exist")
        row = self.get(automation_id)
        if row is None:
            return None
        current = str(row["state"])
        if current == target:
            return row
        if current in AUTOMATION_TERMINAL:
            raise ManifestError(f"state '{current}' is terminal; create a new automation")
        legal = {
            "draft": {"active", "stopped"},
            "active": {"paused", "stopped", "expired"},
            "paused": {"active", "stopped", "expired"},
            "stopped": set(),
            "expired": set(),
        }[current]
        if target not in legal:
            raise ManifestError(f"illegal transition {current} -> {target}")
        moment = time.time() if now is None else float(now)
        with self.db.connect() as conn:
            conn.execute(
                update(automations)
                .where(automations.c.id == int(automation_id))
                .values(state=target, reason=str(reason or ""), updated_at=moment)
            )
            conn.execute(
                automation_state_log.insert().values(
                    automation_id=int(automation_id),
                    from_state=current,
                    to_state=target,
                    reason=str(reason or ""),
                    actor=str(actor or ""),
                    at=moment,
                )
            )
        log.info(
            "automation %s %s -> %s (%s)", automation_id, current, target, reason or "-"
        )
        return self.get(automation_id)

    # ---------------------------------------------------------- occurrences

    def due_automations(self, now: float, limit: int = 200) -> list[dict]:
        """Clock-driven rows only: a message automation is fed by the intake sink."""
        return self.db.fetch_all(
            select(automations)
            .where(
                automations.c.state == "active",
                automations.c.next_occurrence > 0,
                automations.c.next_occurrence <= float(now),
                func.coalesce(automations.c.event["kind"].astext, "") != "message",
            )
            .order_by(automations.c.next_occurrence)
            .limit(max(1, int(limit)))
        )

    def enqueue_message(
        self,
        conn,
        *,
        thread_id: str,
        gateway: str,
        message,
        person_id: str,
        now: float,
    ) -> int:
        """ADR-0014 §4.3: one queued run per active message automation that matches.

        The sink runs inside the intake transaction, so the run and the message commit together
        or not at all. The occurrence key is the message id: a redelivered message inserts
        nothing, and the payload carries what the condition and the steps read.
        """
        rows = conn.execute(
            select(automations).where(
                automations.c.state == "active",
                automations.c.event["kind"].astext == "message",
            )
        ).mappings().all()
        created = 0
        for row in rows:
            event = row["event"] if isinstance(row["event"], dict) else {}
            rooms = event.get("thread") or []
            if rooms and str(thread_id) not in {str(item) for item in rooms}:
                continue
            sender = str(event.get("sender") or "")
            if sender and str(person_id) != sender:
                continue
            found = conn.execute(
                pg_insert(automation_runs)
                .values(
                    automation_id=int(row["id"]),
                    occurrence_key=f"message:{int(row['id'])}:{message.id!s}",
                    scheduled_at=float(message.when.timestamp()) if message.when else float(now),
                    status="queued",
                    payload={
                        "message_id": str(message.id),
                        "thread": str(thread_id),
                        "gateway": str(gateway),
                        "person_id": str(person_id),
                    },
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=["occurrence_key"])
                .returning(automation_runs.c.id)
            ).scalar()
            if found is not None:
                created += 1
        return created

    def materialize(self, now: float | None = None) -> dict[str, int]:
        """Turn every due schedule into its own run row; a duplicate never doubles."""
        moment = time.time() if now is None else float(now)
        report = {"materialized": 0, "missed": 0, "expired": 0, "duplicate": 0}
        for row in self.due_automations(moment):
            self._materialize_one(row, moment, report)
        return report

    def _materialize_one(self, row: dict, now: float, report: dict[str, int]) -> None:
        event = row["event"] if isinstance(row["event"], dict) else {}
        current = float(row["next_occurrence"] or 0.0)
        fire = int(row["fire_count"] or 0)
        count = int(event.get("count") or 0)
        created = float(row["created_at"])
        stale = False
        processed = 0
        while current and current <= now and processed < MATERIALIZE_CAP:
            processed += 1
            if count and fire >= count:
                current = 0.0
                break
            spent, stale = self._enqueue_occurrence(row, current, now, report)
            fire += spent
            if stale:
                current = 0.0
                break
            found = next_after(event, current, anchor=created)
            current = float(found) if found is not None else 0.0
            if count and fire >= count:
                current = 0.0
        self._store_schedule(row["id"], current, fire, now)
        if stale:
            self.set_state(row["id"], "paused", reason="stale", actor="engine", now=now)
        elif not current:
            report["expired"] += int(self.maybe_expire(row["id"], now=now))

    def _enqueue_occurrence(
        self, row: dict, current: float, now: float, report: dict[str, int]
    ) -> tuple[int, bool]:
        """One occurrence row; returns `(spent, stale)` and never doubles a key."""
        late = now - current
        max_stale = int(self.config.resilience.max_stale_days)
        if max_stale and late > max_stale * DAY:
            inserted = self._insert_run(row, current, "missed", "past max_stale_days", now)
            report["missed" if inserted else "duplicate"] += 1
            return (1 if inserted else 0), True
        catchup = int(self.config.resilience.catchup_minutes) * 60
        status = "missed" if late > catchup else "queued"
        detail = "catch-up window missed" if status == "missed" else ""
        inserted = self._insert_run(row, current, status, detail, now)
        report["missed" if status == "missed" else "materialized"] += int(inserted)
        report["duplicate"] += int(not inserted)
        return int(inserted), False

    def _store_schedule(
        self, automation_id: int | str, current: float, fire: int, now: float
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                update(automations)
                .where(automations.c.id == int(automation_id))
                .values(next_occurrence=current, fire_count=fire, updated_at=now)
            )

    def _insert_run(
        self, automation: dict, scheduled_at: float, status: str, detail: str, now: float
    ) -> bool:
        row = {
            "automation_id": int(automation["id"]),
            "occurrence_key": occurrence_key(automation["id"], scheduled_at),
            "scheduled_at": float(scheduled_at),
            "status": status,
            "detail": detail,
            "created_at": now,
            "updated_at": now,
        }
        with self.db.connect() as conn:
            found = conn.execute(
                pg_insert(automation_runs)
                .values(row)
                .on_conflict_do_nothing(index_elements=["occurrence_key"])
                .returning(automation_runs.c.id)
            ).scalar()
        return found is not None

    # ---------------------------------------------------------------- claim

    def claim(
        self,
        now: float,
        *,
        worker: str,
        generation: int = 0,
        limit: int = 5,
    ) -> list[dict]:
        lease_until = now + float(self.config.resilience.stale_claim_seconds)
        claimed: list[dict] = []
        with self.db.connect() as conn:
            rows = conn.execute(
                select(automation_runs)
                .where(
                    automation_runs.c.status == "queued",
                    automation_runs.c.scheduled_at <= float(now),
                    automation_runs.c.next_attempt_at <= float(now),
                )
                .order_by(automation_runs.c.scheduled_at, automation_runs.c.id)
                .limit(max(1, int(limit)))
                .with_for_update(skip_locked=True)
            ).mappings().all()
            for row in rows:
                conn.execute(
                    update(automation_runs)
                    .where(
                        automation_runs.c.id == int(row["id"]),
                        automation_runs.c.status == "queued",
                    )
                    .values(
                        status="claimed",
                        claimed_by=str(worker),
                        claimed_at=now,
                        lease_until=lease_until,
                        attempts=int(row["attempts"]) + 1,
                        generation=int(generation),
                        updated_at=now,
                    )
                )
                body = dict(row)
                body.update(
                    status="claimed",
                    claimed_by=str(worker),
                    claimed_at=now,
                    lease_until=lease_until,
                    attempts=int(row["attempts"]) + 1,
                    generation=int(generation),
                )
                claimed.append(body)
        return claimed

    def reap(self, now: float | None = None) -> int:
        """A claim from a dead process goes back to the queue; attempts stay counted."""
        moment = time.time() if now is None else float(now)
        rows = self.db.fetch_all(
            select(automation_runs).where(
                automation_runs.c.status.in_(("claimed", "running")),
                automation_runs.c.lease_until < moment,
            )
        )
        for row in rows:
            if int(row["attempts"]) >= int(self.config.retry.max_attempts):
                self.finish(
                    row["id"], "failed", "stale claim: attempts exhausted", now=moment
                )
            else:
                with self.db.connect() as conn:
                    conn.execute(
                        update(automation_runs)
                        .where(automation_runs.c.id == int(row["id"]))
                        .values(status="queued", next_attempt_at=moment, updated_at=moment)
                    )
        if rows:
            log.info("automation reaper released %d stale claim(s)", len(rows))
        return len(rows)

    def interrupt(self, now: float | None = None) -> int:
        """Clean shutdown: an in-flight run is `interrupted`, not lost and not failed."""
        moment = time.time() if now is None else float(now)
        with self.db.connect() as conn:
            outcome = conn.execute(
                update(automation_runs)
                .where(automation_runs.c.status.in_(("claimed", "running")))
                .values(
                    status="interrupted",
                    detail="clean shutdown",
                    finished_at=moment,
                    updated_at=moment,
                )
            )
            return int(outcome.rowcount or 0)

    def resume_interrupted(self, now: float | None = None) -> int:
        """Next boot: interrupted runs inside the window resume; older ones are missed."""
        moment = time.time() if now is None else float(now)
        window = int(self.config.resilience.catchup_minutes) * 60
        rows = self.db.fetch_all(
            select(automation_runs).where(automation_runs.c.status == "interrupted")
        )
        resumed = 0
        for row in rows:
            if moment - float(row["scheduled_at"]) <= window:
                with self.db.connect() as conn:
                    conn.execute(
                        update(automation_runs)
                        .where(automation_runs.c.id == int(row["id"]))
                        .values(
                            status="queued",
                            next_attempt_at=0,
                            claimed_at=None,
                            claimed_by="",
                            lease_until=0,
                            updated_at=moment,
                        )
                    )
                resumed += 1
            else:
                self.finish(row["id"], "missed", "interrupted: window missed", now=moment)
        return resumed

    def finish(
        self,
        run_id: int | str,
        status: str,
        detail: str = "",
        *,
        now: float | None = None,
    ) -> None:
        moment = time.time() if now is None else float(now)
        values: dict = {
            "status": str(status),
            "detail": str(detail)[:DETAIL_CHARS],
            "finished_at": moment,
            "updated_at": moment,
            "lease_until": 0,
        }
        with self.db.connect() as conn:
            conn.execute(
                update(automation_runs)
                .where(automation_runs.c.id == int(run_id))
                .values(**values)
            )

    def retry(self, run: dict, error: str, *, now: float | None = None, step: int = -1) -> str:
        """Transient failure: back off, or `failed` once attempts are exhausted."""
        moment = time.time() if now is None else float(now)
        attempts = max(1, int(run.get("attempts") or 1))
        if attempts >= int(self.config.retry.max_attempts):
            self.finish(run["id"], "failed", f"attempts exhausted: {error}", now=moment)
            return "failed"
        delay = min(
            float(self.config.retry.max_backoff_seconds),
            float(self.config.retry.backoff_seconds) * (2 ** (attempts - 1)),
        )
        if self.config.retry.respect_retry_after:
            found = _RETRY_AFTER.search(str(error))
            if found:
                delay = max(delay, float(found.group(1)))
        if self.config.retry.jitter:
            delay *= 1.0 + random.uniform(0.0, 0.2)
        with self.db.connect() as conn:
            conn.execute(
                update(automation_runs)
                .where(automation_runs.c.id == int(run["id"]))
                .values(
                    status="queued",
                    next_attempt_at=moment + delay,
                    step_index=int(step),
                    detail=str(error)[:DETAIL_CHARS],
                    updated_at=moment,
                )
            )
        return "retry"

    def budget_used(
        self, automation: dict, scheduled_at: float, *, exclude_run: int | None = None
    ) -> int:
        """Runs already spent on the occurrence's local day; skipped/missed do not count.

        Only an executed outcome spends the budget (ADR-0014 §4.13: `budget_per_day` counts
        runs, not attempts); the currently claimed run is excluded until it finishes.
        """
        event = _mapping(automation.get("event"))
        moment = datetime.fromtimestamp(
            float(scheduled_at), zone(event.get("tz") or DEFAULT_TZ)
        )
        start = datetime.combine(
            moment.date(), clock_time(0, 0), tzinfo=moment.tzinfo
        ).timestamp()
        statement = select(func.count()).select_from(automation_runs).where(
            automation_runs.c.automation_id == int(automation["id"]),
            automation_runs.c.scheduled_at >= start,
            automation_runs.c.scheduled_at < start + DAY,
            automation_runs.c.status.in_(("succeeded", "failed", "interrupted")),
        )
        if exclude_run is not None:
            statement = statement.where(automation_runs.c.id != int(exclude_run))
        return int(self.db.scalar(statement) or 0)

    def pending_runs(self, automation_id: int | str) -> int:
        return int(
            self.db.scalar(
                select(func.count())
                .select_from(automation_runs)
                .where(
                    automation_runs.c.automation_id == int(automation_id),
                    automation_runs.c.status.in_(("queued", "claimed", "running")),
                )
            )
            or 0
        )

    def maybe_expire(self, automation_id: int | str, *, now: float | None = None) -> bool:
        """A finished schedule expires after its last run, never before it (ADR-0014 §4.13)."""
        row = self.get(automation_id)
        if row is None or row["state"] != "active":
            return False
        if int(row["next_occurrence"] or 0) or self.pending_runs(automation_id):
            return False
        self.set_state(
            automation_id, "expired", reason="schedule exhausted", actor="engine", now=now
        )
        return True

    def mark_apologized(self, run_id: int | str, *, now: float | None = None) -> None:
        """The apology is a timestamp, not a new reason: `detail` keeps why it was missed."""
        moment = time.time() if now is None else float(now)
        with self.db.connect() as conn:
            conn.execute(
                update(automation_runs)
                .where(automation_runs.c.id == int(run_id))
                .values(apology_sent_at=moment, updated_at=moment)
            )

    def missed_apologies(self, limit: int = 50) -> list[dict]:
        return self.db.fetch_all(
            select(automation_runs)
            .where(
                automation_runs.c.status == "missed",
                automation_runs.c.apology_sent_at.is_(None),
            )
            .order_by(automation_runs.c.scheduled_at)
            .limit(max(1, int(limit)))
        )

    # ---------------------------------------------------------------- stats

    def stats(self, now: float | None = None) -> dict[str, int]:
        """Counters for `/status`: real rows, zero when nothing happened."""
        moment = time.time() if now is None else float(now)
        runs = self.db.fetch_all(
            select(automation_runs.c.status, func.count().label("count")).group_by(
                automation_runs.c.status
            )
        )
        states = self.db.fetch_all(
            select(automations.c.state, func.count().label("count")).group_by(
                automations.c.state
            )
        )
        today = self.db.scalar(
            select(func.count())
            .select_from(automation_runs)
            .where(
                automation_runs.c.scheduled_at >= moment - DAY,
                automation_runs.c.status.in_(("succeeded", "failed", "running", "claimed")),
            )
        )
        found = {f"runs.{row['status']}": int(row["count"]) for row in runs}
        found.update({f"states.{row['state']}": int(row["count"]) for row in states})
        found["runs_total"] = sum(int(row["count"]) for row in runs)
        found["runs_today"] = int(today or 0)
        found["missed"] = sum(
            int(row["count"]) for row in runs if str(row["status"]) == "missed"
        )
        found["denied"] = sum(
            int(row["count"]) for row in runs if str(row["status"]) == "denied"
        )
        found["queued"] = sum(
            int(row["count"]) for row in runs if str(row["status"]) == "queued"
        )
        found["active"] = sum(
            int(row["count"]) for row in states if str(row["state"]) == "active"
        )
        return found


def _rooms_of(agent, owner: str, manifest: dict) -> set[str]:
    try:
        rooms = set(agent.history.rooms_of(owner))
    except Exception:
        rooms = set()
    origin = manifest["event"].get("thread")
    if origin:
        rooms.add(str(origin))
    return rooms


def _until_epoch(event: dict) -> float | None:
    until = _date(event.get("until"), "until")
    if until is None:
        return None
    zoneinfo = zone(event.get("tz"))
    end = datetime.combine(until + timedelta(days=1), clock_time(0, 0), tzinfo=zoneinfo)
    return end.timestamp() - 1.0


# --------------------------------------------------------------------- worker


class AutomationWorker:
    """The one scheduler for promises: catch up, claim, run, record, apologize."""

    def __init__(
        self,
        agent,
        *,
        store: AutomationStore | None = None,
        tick: float = 5.0,
        limit: int = 5,
    ) -> None:
        self.agent = agent
        self.config: AutomationConfig = agent.config.automations
        self.store = store or AutomationStore(agent.db, self.config)
        self.tick = max(1.0, float(tick))
        self.limit = max(1, int(limit))
        self.worker_id = f"automation:{os.getpid()}"
        self._task: asyncio.Task | None = None
        self._running = False
        self._nl: dict[str, int | str] = {"day": "", "tokens": 0}

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._task is None and self.config.enabled:
            self._task = asyncio.create_task(self._loop(), name="automation-worker")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        self._running = False

    def interrupt(self) -> int:
        return self.store.interrupt()

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # a broken automation must not stop the bot
                log.exception("automation tick failed")
            await asyncio.sleep(self.tick)

    # ---------------------------------------------------------------- tick

    async def run_once(self, now: float | None = None) -> dict[str, int]:
        moment = time.time() if now is None else float(now)
        report = {"reaped": 0, "materialized": 0, "missed": 0, "queued": 0, "skipped": 0}
        if not self.config.enabled or self.config.kill_switch:
            return report
        if self._running:
            return report
        self._running = True
        try:
            report["reaped"] = self.store.reap(moment)
            self.store.resume_interrupted(moment)
            materialized = self.store.materialize(moment)
            report.update(
                {
                    "materialized": materialized.get("materialized", 0),
                    "missed": materialized.get("missed", 0),
                }
            )
            await self._apologize(moment)
            runs = self.store.claim(
                moment,
                worker=self.worker_id,
                generation=self._generation(),
                limit=self.limit,
            )
            for run in runs:
                outcome = await self._run(run, moment)
                report[outcome] = report.get(outcome, 0) + 1
        finally:
            self._running = False
        return report

    def _generation(self) -> int:
        return int(getattr(getattr(self.agent, "lease", None), "generation", 0) or 0)

    # ---------------------------------------------------------------- run

    async def _run(self, run: dict, now: float) -> str:
        automation = self.store.get(run["automation_id"])
        if automation is None:
            self.store.finish(run["id"], "cancelled", "automation is gone", now=now)
            return "cancelled"
        state = str(automation["state"])
        if state in {"stopped", "expired"}:
            self.store.finish(run["id"], "cancelled", f"automation is {state}", now=now)
            return "cancelled"
        if state == "paused":
            self.store.finish(run["id"], "skipped", "automation is paused", now=now)
            return "skipped"
        if not self._quiet(automation, run, now):
            return "skipped"
        if self.store.budget_used(
            automation, float(run["scheduled_at"]), exclude_run=int(run["id"])
        ) >= int(
            automation["policy"].get("budget_per_day") or self.config.default_budget_per_day
        ):
            self.store.finish(run["id"], "skipped", "daily budget reached", now=now)
            return "skipped"
        errors = self._authority_errors(automation)
        if errors:
            self.store.finish(run["id"], "denied", "; ".join(errors), now=now)
            self.store.set_state(
                automation["id"], "paused", reason="authority", actor="engine", now=now
            )
            await self._notify(
                automation,
                "automation.denied",
                name=automation["name"] or str(automation["id"]),
                reason="; ".join(errors),
            )
            return "denied"
        return await self._execute(automation, run, now)

    def _quiet(self, automation: dict, run: dict, now: float) -> bool:
        """True when the run may proceed; records `skipped` or defers to the window end."""
        policy = _mapping(automation.get("policy"))
        event = _mapping(automation.get("event"))
        window = parse_quiet_hours(policy.get("quiet_hours") or self.config.default_quiet_hours)
        if window is None or not in_quiet(float(run["scheduled_at"]), event.get("tz"), window):
            return True
        moment = quiet_end(float(run["scheduled_at"]), event.get("tz"), window)
        if str(policy.get("quiet") or "skip") == "defer":
            with self.store.db.connect() as conn:
                conn.execute(
                    update(automation_runs)
                    .where(automation_runs.c.id == int(run["id"]))
                    .values(
                        status="queued",
                        scheduled_at=moment,
                        deferred=True,
                        next_attempt_at=0,
                        attempts=max(0, int(run.get("attempts") or 1) - 1),
                        claimed_by="",
                        claimed_at=None,
                        lease_until=0,
                        updated_at=now,
                    )
                )
            log.info("automation run %s deferred to %s (quiet hours)", run["id"], moment)
            return False
        self.store.finish(run["id"], "skipped", "quiet hours", now=now)
        return False

    def _authority_errors(self, automation: dict) -> list[str]:
        owner = str(automation["owner"])
        roles = self.agent.config.roles.for_person(owner)
        manifest = {"owner": owner, "event": automation["event"], "steps": automation["steps"]}
        rooms = _rooms_of(self.agent, owner, manifest)
        return authority_errors(
            manifest,
            roles=roles,
            rooms=rooms,
            recipes=getattr(self.agent, "recipes", None),
            config=self.config,
            workspace=self.agent.config.workspace,
            memory_admin=any(role.memory_admin for role in roles),
        )

    # ------------------------------------------------------------- pipeline

    async def _execute(self, automation: dict, run: dict, now: float) -> str:
        with observability.bind(run_id=f"automation:{run['id']}"):
            return await self._pipeline(automation, run, now)

    async def _pipeline(self, automation: dict, run: dict, now: float) -> str:
        steps = automation["steps"] if isinstance(automation["steps"], list) else []
        facts: dict = {
            "run_id": run["id"],
            "automation": automation,
            "text": "",
            "composed": "",
            "chunk": 0,
            "relay_grant": None,
            "relay_to": "",
            "notified": 0,
        }
        await io.run(self._mark_running, run["id"], now)
        if not await self._message_facts(run, facts):
            self.store.finish(run["id"], "skipped", "message gone", now=now)
            return "skipped"
        try:
            async with asyncio.timeout(int(self.config.run_timeout_seconds)):
                start = await self._readers(steps, automation, run, facts)
                if not await self._condition_met(automation, run, facts):
                    self.store.finish(run["id"], "skipped", "condition not met")
                    return "skipped"
                for index in range(start, len(steps)):
                    await self._step(steps[index], index, automation, run, facts)
                    await io.run(self._mark_step, run["id"], index)
        except StepSkipped as exc:
            self.store.finish(run["id"], "skipped", str(exc), now=now)
            return "skipped"
        except StepError as exc:
            outcome = (
                self.store.retry(run, str(exc), now=now) if exc.transient else "failed"
            )
            if not exc.transient:
                self.store.finish(run["id"], "failed", str(exc), now=now)
            if outcome == "failed":
                await self._notify(
                    automation,
                    "automation.failed",
                    name=automation["name"] or str(automation["id"]),
                    error=str(exc)[:200],
                )
            return "failed" if outcome == "failed" else "retried"
        except TimeoutError:
            self.store.retry(run, "run timeout", now=now)
            return "retried"
        self.store.finish(run["id"], "succeeded", now=now)
        self.store.maybe_expire(automation["id"], now=None)
        return "succeeded"

    async def _message_facts(self, run: dict, facts: dict) -> bool:
        """A message run carries its message: the condition and the steps read real fields."""
        payload = _mapping(run.get("payload"))
        message_id = str(payload.get("message_id") or "")
        if not message_id:
            return True
        thread = str(payload.get("thread") or "")
        message = await io.run(self.agent.history.find, thread, message_id)
        if message is None:
            return False
        facts["text"] = str(message.text or "")
        facts["sender"] = str(payload.get("person_id") or message.sender_id or "")
        facts["thread"] = thread
        return True

    async def _readers(
        self, steps: list[dict], automation: dict, run: dict, facts: dict
    ) -> int:
        """`collect` runs before the condition: it is the only reader, never an action."""
        for index, step in enumerate(steps):
            if step["kind"] != "collect":
                return index
            await self._step(step, index, automation, run, facts)
            facts.setdefault("thread", _target_thread(step, automation))
        return len(steps)

    async def _condition_met(self, automation: dict, run: dict, facts: dict) -> bool:
        condition = automation.get("condition") or {"all": []}
        if not isinstance(condition, dict):
            return False
        if condition.get("kind") == "nl":
            return await self._nl_condition(condition, facts, run)
        event = automation.get("event") or {}
        facts.setdefault("now", time.time())
        facts.setdefault("tz", event.get("tz"))
        return evaluate_condition(condition, facts)

    async def _nl_condition(self, condition: dict, facts: dict, run: dict) -> bool:
        """The opt-in ambiguity judge, bounded per day (ADR-0014 §4.3/§5)."""
        text = str(facts.get("text") or "").strip()
        if not text or self.agent.llm is None:
            return False
        if not self.config.condition.nl_criteria or not self._nl_allowed():
            return False
        self._nl_charge()
        raw = await self.agent.llm.complete(
            "Answer only yes or no.",
            f"QUESTION: {condition['text']}\nMATERIAL:\n{text[:4000]}",
            max_tokens=5,
            temperature=0.0,
            session="eidesoul-automation-condition",
            purpose="label",
            run_id=f"automation:{run['id']}",
        )
        return str(raw or "").strip().lower().startswith("yes")

    def _nl_allowed(self) -> bool:
        day = date.today().isoformat()
        if self._nl.get("day") != day:
            self._nl = {"day": day, "tokens": 0}
        budget = int(self.config.condition.nl_budget_tokens_per_day)
        return int(self._nl.get("tokens") or 0) + NL_JUDGEMENT_TOKENS <= budget

    def _nl_charge(self) -> None:
        self._nl["tokens"] = int(self._nl.get("tokens") or 0) + NL_JUDGEMENT_TOKENS

    def _mark_running(self, run_id: int | str, now: float) -> None:
        with self.store.db.connect() as conn:
            conn.execute(
                update(automation_runs)
                .where(automation_runs.c.id == int(run_id))
                .values(status="running", started_at=now, updated_at=now)
            )

    def _mark_step(self, run_id: int | str, index: int) -> None:
        with self.store.db.connect() as conn:
            conn.execute(
                update(automation_runs)
                .where(automation_runs.c.id == int(run_id))
                .values(step_index=int(index), updated_at=time.time())
            )

    async def _step(
        self, step: dict, index: int, automation: dict, run: dict, facts: dict
    ) -> None:
        kind = step["kind"]
        if kind == "collect":
            await self._collect(step, automation, facts)
        elif kind == "compose":
            await self._compose(step, automation, facts)
        elif kind == "send":
            await self._send(step, automation, run, facts)
        elif kind == "relay":
            await self._relay(step, automation, run, facts)
        elif kind == "sandbox":
            await self._sandbox(step, automation, run, facts)
        elif kind == "recipe":
            await self._recipe(step, automation, facts)
        elif kind == "queue_reflection":
            self._queue_reflection(step, automation, run)
        elif kind in TOOL_STEPS:
            await self._tool(kind, step, automation, facts)
        else:  # pragma: no cover - validation refuses an unknown kind earlier
            raise StepError(f"step kind {kind} is not implemented")

    # --------------------------------------------------------------- steps

    async def _collect(self, step: dict, automation: dict, facts: dict) -> None:
        thread = _target_thread(step, automation)
        if not thread:
            raise StepError("collect has no source room", transient=False)
        owner = str(automation["owner"])
        rooms = _rooms_of(self.agent, owner, automation)
        if not _owns(rooms, thread):
            raise StepError(
                "collect refused: the owner is not present in that room", transient=False
            )
        rows = self.agent.history.recent(thread, int(step.get("limit") or 200))
        cutoff = time.time() - int(step.get("window") or 86400)
        lines = [
            f"{row.sender_name or row.sender_id}: {row.text}"
            for row in rows
            if float(getattr(row.when, "timestamp", lambda: 0.0)()) >= cutoff
        ]
        facts["text"] = "\n".join(lines)[:COLLECT_CHARS]
        facts["thread"] = thread

    async def _compose(self, step: dict, automation: dict, facts: dict) -> None:
        if self.agent.llm is None:
            raise StepError("compose needs the LLM", transient=False)
        from ..turn.prompts import system_prompt

        owner = str(automation["owner"])
        roles = self.agent.config.roles.for_person(owner)
        source = facts.get("text") or step.get("text") or ""
        instruction = str(step.get("prompt") or "").strip() or (
            "Write one short chat message for the owner using only the material below. "
            "Do not invent facts."
        )
        system = system_prompt(
            self.agent.config,
            roles,
            runtime=getattr(self.agent, "runtime", None),
            mode="professional",
        )
        body = await self.agent.llm.complete(
            system,
            f"{instruction}\n\nMATERIAL:\n{source}".strip(),
            max_tokens=500,
            temperature=0.4,
            session="eidesoul-automation",
            purpose="reply",
            run_id=f"automation:{facts['run_id']}",
        )
        text = str(body or "").strip()[:COMPOSE_CHARS]
        if not text:
            raise StepError("compose returned nothing", transient=True)
        facts["composed"] = text

    async def _send(self, step: dict, automation: dict, run: dict, facts: dict) -> None:
        text = str(step.get("text") or facts.get("composed") or "").strip()
        if not text:
            raise StepError("send has no text to deliver", transient=False)
        target = self._send_target(step, automation)
        owner = str(automation["owner"])
        origin_thread = str(automation["event"].get("thread") or owner)
        attribution = target not in {owner, origin_thread}
        await self._deliver(
            target,
            text,
            automation,
            run,
            facts,
            attribution=attribution,
            origin=Address(self._route_name(origin_thread), origin_thread),
        )

    def _send_target(self, step: dict, automation: dict) -> str:
        owner = str(automation["owner"])
        target = str(step.get("to") or "owner_dm").strip()
        if target == "owner_dm":
            return owner
        if target == "origin":
            return str(automation["event"].get("thread") or owner)
        return target

    def _address(self, target: str) -> Address:
        """The routed thread, or the person's DM thread (ADR-0032 §4.1: a route or a refusal)."""
        try:
            return self.agent.address_of(str(target))
        except GatewayError as exc:
            dm = str(getattr(self.agent.history, "dm_thread", lambda _p: "")(str(target)) or "")
            if not dm:
                raise StepError(str(exc), transient=False) from None
            return self.agent.address_of(dm)

    def _route_name(self, thread_id: str) -> str:
        """ADR-0014 §4.6: the body that first saw this thread owns its route."""
        try:
            return str(self.agent.address_of(str(thread_id)).gateway or "")
        except GatewayError:
            raise StepError(
                f"no body owns thread '{thread_id}'; the send is refused", transient=False
            ) from None

    async def _deliver(
        self,
        target: str,
        text: str,
        automation: dict,
        run: dict,
        facts: dict,
        *,
        attribution: bool = False,
        origin: Address | None = None,
    ) -> None:
        from ..turn.reply import split_text

        address = self._address(str(target))
        owner = str(automation["owner"])
        reason = t("automation.attribution", self._language()) if attribution else ""
        body = str(text)
        if attribution:
            body = f"{reason}\n{body}"
        for chunk in split_text(body) or [body]:
            index = int(facts.get("chunk") or 0)
            action = Action(
                address=address,
                capability="send_text",
                payload=chunk,
                id=f"automation:{run['id']}:{index}",
            )
            receipt = await self.agent.dispatch(
                owner, address, action, origin=origin or address, reason=reason
            )
            if receipt.state == "sent":
                facts["chunk"] = index + 1
                continue
            if receipt.state == "unknown":
                raise StepError("delivery is unknown; not resending", transient=False)
            if receipt.state == "pending":
                raise StepError(f"needs approval ({receipt.reason})", transient=False)
            raise StepError(f"send refused: {receipt.reason}", transient=False)

    async def _relay(self, step: dict, automation: dict, run: dict, facts: dict) -> None:
        owner = str(automation["owner"])
        target = str(step["to_person"])
        task = str(step["task_id"])
        store = RelayStore(self.agent.db)
        grant, reason = store.check(task_id=task, from_person=owner, to_person=target)
        if grant is None:
            deadline = time.time() + int(self.config.run_timeout_seconds)
            await store.ask(
                self.agent,
                task_id=task,
                from_person=owner,
                to_person=target,
                scope_ref=str(step.get("scope_ref") or ""),
                thread_id=owner,
                address=self.agent.address_of(owner),
                expires_at=deadline,
                now=time.time(),
            )
            raise StepSkipped(f"relay: {reason or 'no grant'}; consent asked")
        facts["relay_grant"] = grant
        facts["relay_to"] = target
        text = str(step.get("text") or facts.get("composed") or "").strip()
        if text:
            await self._deliver(
                target,
                text,
                automation,
                run,
                facts,
                attribution=grant.mode != "anonymous",
            )

    async def _sandbox(self, step: dict, automation: dict, run: dict, facts: dict) -> None:
        program = str(step["program"])
        path = (self.agent.config.workspace / program).resolve()
        try:
            path.relative_to(self.agent.config.workspace.resolve())
        except ValueError as exc:
            raise StepError("sandbox program escapes the workspace", transient=False) from exc
        if not path.is_file():
            raise StepError(f"sandbox program not found: {program}", transient=False)
        args = step.get("args") or {}
        job = " ".join(f"--{key} {shlex.quote(str(value))}" for key, value in args.items())
        command = f"python3 {shlex.quote(program)} {job}".strip()
        timeout = min(
            int(self.agent.config.sandbox.timeout_seconds),
            int(self.agent.config.turns.shutdown.drain_seconds),
        )
        ctx = self._tool_context(automation, facts)
        result = await execute(
            {"sandbox": self.agent.tools.get("sandbox")},
            "sandbox",
            {"command": command, "timeout": max(1, timeout)},
            ctx,
        )
        if str(result).startswith("error:"):
            raise StepError(str(result)[:200], transient="timeout" in str(result).lower())
        facts["sandbox"] = str(result)[-2000:]

    async def _recipe(self, step: dict, automation: dict, facts: dict) -> None:
        recipes = getattr(self.agent, "recipes", None)
        if recipes is None:
            raise StepError("recipes are not available", transient=False)
        recipe = recipes.get(str(step["name"]))
        if recipe is None:
            raise StepError(f"recipe not found: {step['name']}", transient=False)
        roles = self.agent.config.roles.for_person(str(automation["owner"]))
        if not ({role.name for role in roles} & set(recipe.roles)):
            raise StepError("recipe is not allowed for the owner", transient=False)
        from ..stores.recipes import run_recipe

        ctx = self._tool_context(automation, facts)
        result = await run_recipe(recipe, str(step.get("args") or ""), ctx)
        if str(result).startswith("error:"):
            raise StepError(str(result)[:200], transient=False)
        facts["recipe"] = str(result)[-2000:]

    def _queue_reflection(self, step: dict, automation: dict, run: dict) -> None:
        cognition = getattr(self.agent, "cognition", None)
        if cognition is None or cognition.queue is None:
            raise StepError("queue_reflection is unavailable", transient=True)
        thread = str(step.get("text") or automation["event"].get("thread") or "")
        scope = str(step.get("scope") or (f"thread:{thread}" if thread else "global"))
        cognition.queue.enqueue_job(
            "reflect",
            scope,
            key=f"automation:{run['id']}:reflect",
        )

    async def _tool(
        self, name: str, step: dict, automation: dict, facts: dict
    ) -> None:
        tool = TOOL_STEPS[name]
        spec = self.agent.tools.get(tool)
        if spec is None:
            raise StepError(f"tool '{tool}' is not available", transient=False)
        args = self._tool_args(name, step, automation, facts)
        ctx = self._tool_context(automation, facts)
        result = await execute({tool: spec}, tool, args, ctx)
        if str(result).startswith("error:"):
            low = str(result).lower()
            raise StepError(str(result)[:200], transient="timeout" in low or "unavailable" in low)

    def _tool_args(self, name: str, step: dict, automation: dict, facts: dict) -> dict:
        if name == "web_search":
            query = str(step.get("query") or facts.get("text") or "").strip()
            if not query:
                raise StepError("web_search needs a query", transient=False)
            return {"query": query}
        if name == "write":
            path = str(step.get("path") or "").strip()
            if not path:
                raise StepError("write needs a path", transient=False)
            content = str(step.get("text") or facts.get("composed") or "")
            if not content:
                raise StepError("write has nothing to write", transient=False)
            return {"path": path, "content": content}
        text = str(step.get("text") or facts.get("composed") or "").strip()
        if not text:
            raise StepError("remember has nothing to write", transient=False)
        scope = str(step.get("scope") or "thread")
        if scope == "global" and not any(
            role.memory_admin
            for role in self.agent.config.roles.for_person(str(automation["owner"]))
        ):
            raise StepError("a global memory write needs memory_admin", transient=False)
        return {
            "action": "add",
            "text": text,
            "scope": scope,
            "kind": str(step.get("memory_kind") or "note"),
        }

    def _tool_context(self, automation: dict, facts: dict):
        owner = str(automation["owner"])
        roles = self.agent.config.roles.for_person(owner)
        thread = str(
            automation["event"].get("thread")
            or facts.get("thread")
            or owner
        )
        gateway = self.agent.body_of(thread)
        if gateway is None:
            raise StepError(f"no body owns thread '{thread}'", transient=False)
        return self.agent.tool_context(
            thread,
            owner,
            roles,
            gateway,
            request="automation",
        )

    # ------------------------------------------------------------- apology

    async def _apologize(self, now: float) -> int:
        if not self.config.resilience.missed_apology:
            return 0
        runs = self.store.missed_apologies()
        if not runs:
            return 0
        by_owner: dict[str, list[dict]] = {}
        for run in runs:
            automation = self.store.get(run["automation_id"])
            if automation is None:
                continue
            owner = str(automation["owner"])
            if self.config.resilience.apology_respect_quiet_hours:
                policy = automation.get("policy") or {}
                event = automation.get("event") or {}
                window = parse_quiet_hours(
                    policy.get("quiet_hours") or self.config.default_quiet_hours
                )
                if in_quiet(now, event.get("tz"), window):
                    continue
            by_owner.setdefault(owner, []).append({**run, "_automation": automation})
        sent = 0
        for owner, grouped in by_owner.items():
            names = sorted(
                {
                    str(item["_automation"]["name"] or item["automation_id"])
                    for item in grouped
                }
            )
            text = t(
                "automation.apology",
                self._language(),
                count=len(grouped),
                tasks=", ".join(names[:5]),
            )
            fake_run = {"id": f"apology-{grouped[0]['id']}"}
            try:
                await self._deliver(owner, text, grouped[0]["_automation"], fake_run, {})
            except StepError as exc:
                log.warning("apology failed: %s", exc)
                continue
            for item in grouped:
                self.store.mark_apologized(item["id"], now=now)
            sent += len(grouped)
        return sent

    async def _notify(self, automation: dict, key: str, **kw) -> None:
        policy = _mapping(automation.get("policy"))
        if str(policy.get("on_error") or "notify_owner") != "notify_owner":
            return
        text = t(key, self._language(), **kw)
        try:
            address = self._address(str(automation["owner"]))
            gateway = self.agent.body(address.gateway)
            if gateway is None:
                return
            await self.agent.send_text(
                address.thread, text, gateway=gateway, subject=str(automation["owner"])
            )
        except Exception as exc:  # a notice never fails the run
            log.warning("automation notice failed: %s", type(exc).__name__)

    def _language(self) -> str:
        return str(getattr(getattr(self.agent, "soul", None), "language", "id") or "id")


def automation_counters(agent, now: float | None = None) -> dict[str, int]:
    """`/status` reads the real rows; a broken table reports zeros, never invented values."""
    try:
        store = AutomationStore(agent.db, agent.config.automations)
        return store.stats(now)
    except Exception:
        log.debug("automation counters unreadable", exc_info=True)
        return {}


__all__ = [
    "AutomationStore",
    "AutomationWorker",
    "ManifestError",
    "StepError",
    "StepSkipped",
    "authority_errors",
    "automation_counters",
    "describe",
    "evaluate_condition",
    "in_quiet",
    "next_after",
    "occurrence_key",
    "quiet_end",
    "validate_manifest",
]


