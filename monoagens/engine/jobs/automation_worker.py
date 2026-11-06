"""Automation worker: the one scheduler for promises (ADR-0014).

The loop catches up, claims due occurrences, runs the closed step pipeline, records the
outcome, and apologizes for what the catch-up window lost. Only `collect` reads the archive,
`send` goes through the outbound ledger (`scope_key = automation:<run_id>`) so a retry cannot
double-send, `relay` asks through `RelayStore.ask`, and a `sandbox` step runs one named
program inside the workspace, never a command created when due.
"""

import asyncio
import contextlib
import logging
import os
import shlex
import time

from sqlalchemy import update

from .. import clock, io
from ..access.relay import RelayStore
from ..config import AutomationConfig, parse_quiet_hours
from ..i18n import t
from ..inbound import Action, Address, GatewayError
from ..schema import automation_runs
from ..tools import execute
from ..turn import observability
from .automation_manifest import (
    StepError,
    StepSkipped,
    _mapping,
    _owns,
    _rooms_of,
    _target_thread,
    authority_errors,
    evaluate_condition,
)
from .automation_store import AutomationStore
from .lease import Superseded, bind_stores
from .schedule import in_quiet, quiet_end

log = logging.getLogger(__name__)

TOOL_STEPS = {"web_search": "web_search", "write": "write", "remember": "memory"}
NL_JUDGEMENT_TOKENS = 100  # the daily NL budget is charged per judgement at its ceiling
COLLECT_CHARS = 12000
COMPOSE_CHARS = 4000


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
        # ADR-0012 §4.3: the supervisor builds this worker after attaching the lease, so this
        # is the one place every store learns the generation it must write under.
        bind = getattr(self.store, "bind_lease", None)
        if callable(bind):
            bind(getattr(agent, "lease", None))
        bind_stores(agent)
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
        """Only this instance's in-flight runs: a takeover never closes another's work."""
        try:
            return self.store.interrupt(
                worker=self.worker_id, generation=self._generation()
            )
        except Superseded:
            # A standby (generation 0) holds no runs of its own; shutdown must still proceed.
            return 0

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
            session="monoagens-automation-condition",
            purpose="label",
            run_id=f"automation:{run['id']}",
        )
        return str(raw or "").strip().lower().startswith("yes")

    def _nl_allowed(self) -> bool:
        day = clock.today(self.agent.soul.timezone).isoformat()
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
        from ..turn.prompts import PromptContext, system_prompt

        owner = str(automation["owner"])
        roles = self.agent.config.roles.for_person(owner)
        source = facts.get("text") or step.get("text") or ""
        instruction = str(step.get("prompt") or "").strip() or (
            "Write one short chat message for the owner using only the material below. "
            "Do not invent facts."
        )
        context = PromptContext(
            config=self.agent.config,
            roles=roles,
            runtime=getattr(self.agent, "runtime", None),
            mode="professional",
        )
        system = system_prompt(self.agent.config, roles, context=context)
        body = await self.agent.llm.complete(
            system,
            f"{instruction}\n\nMATERIAL:\n{source}".strip(),
            max_tokens=500,
            temperature=0.4,
            session="monoagens-automation",
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
            dm = str(self.agent.private_thread(str(target)) or "")
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
        if origin is None:
            raise StepError("explicit origin address is required", transient=False)
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
                owner, address, action, origin=origin, reason=reason
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
                origin=Address(self._route_name(owner), owner),
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
        args = str(step.get("args") or "")
        try:
            if recipe.kind == "prompt":
                runner = getattr(self.agent, "llm", None)
                if runner is None:
                    raise ValueError("prompt recipes need an LLM runner")
                result = await runner.run("", recipe.rendered(args), [], ctx)
            else:
                result = await run_recipe(recipe, args, ctx)
        except ValueError as exc:
            raise StepError(str(exc), transient=False) from exc
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
                await self._deliver(
                    owner,
                    text,
                    grouped[0]["_automation"],
                    fake_run,
                    {},
                    origin=Address(self._route_name(owner), owner),
                )
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
                address.thread,
                text,
                gateway=gateway,
                subject=str(automation["owner"]),
                origin=address,
            )
        except Exception as exc:  # a notice never fails the run
            log.warning("automation notice failed: %s", type(exc).__name__)

    def _language(self) -> str:
        return str(self.agent.soul.language)


def automation_counters(agent, now: float | None = None) -> dict[str, int]:
    """`/status` reads the real rows; a broken table reports zeros, never invented values."""
    try:
        store = AutomationStore(agent.db, agent.config.automations)
        return store.stats(now)
    except Exception:
        log.debug("automation counters unreadable", exc_info=True)
        return {}
