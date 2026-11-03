"""Agent: orchestration of one conversation turn without any transport."""


import asyncio
import contextlib
import logging
import random
import time
from collections import OrderedDict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path

from . import guard, io, plugins
from .access.approvals import ApprovalStore
from .access.identity import IdentityStore, display_target
from .access.permissions import command_allowed, commands_for, tool_patterns, tools_for
from .access.privacy import PrivacyService
from .access.roles import Role
from .character.character import effective_limits, polish, regulate
from .character.knowledge import select_facts
from .character.patterns import load_lexicon, room_register, select_patterns
from .commands import REGISTRY as CORE_COMMANDS
from .commands import CommandContext
from .config import Config
from .guard import SafetyPolicy, SandboxGuard, build_safety
from .i18n import reload_catalogs, t
from .inbound import (
    CONSENT,
    MESSAGE,
    Action,
    GatewayCore,
    Percept,
    StandbyError,
)
from .llm import LLM, DeadlineExceeded
from .memory import MemoryBackend, MemoryContext, intent, memory_status
from .memory.ledger import OutboundLedger
from .registry import Handler, Registry
from .runtime import ACTIVE_GENERATION as runtime_generation
from .runtime import RuntimeFacts
from .stores.turns import SIDE_EFFECTING, TurnStore, args_hash
from .tools import REGISTRY as CORE_TOOLS
from .tools import ToolContext
from .turn import assembly, observability
from .turn.context import (
    Message,
    WorkingMemory,
    build_user_content,
    fallback_content,
    keyword_set,
    prompt_entry,
)
from .turn.decision import (
    Appraisal,
    Decision,
    RelationshipDelta,
    decide_turn,
    decision_from_row,
)
from .turn.prompts import CRISIS_SAFETY, away_block, character_block, research_block, system_prompt
from .turn.reply import Delivery, plan_chunks

log = logging.getLogger(__name__)

TYPING_REFRESH = 8
RESTART_FIELDS = ("workspace", "gateways", "sandbox", "web_search", "mcp", "database", "paths")
DECISION_HISTORY = 30
DECISION_FALLBACK_HISTORY = 20
DECISION_LINE_CHARS = 500
# ADR-0013 §11: the resume budget is a rolling minute, not a fixed per-pass cap.
RESUME_WINDOW = 60.0
MAX_TEXT = 10_000
MENTION_HEAD_RE = r"^@([^\s@]+)\s*"


class SideEffectAlreadyRan(RuntimeError):
    """A side-effecting tool already executed; the turn is interrupted, never repeated."""


ACTIVE_GENERATION = runtime_generation
TERMINAL_TURN = frozenset({"delivered", "interrupted", "expired"})


def command_body(text: str, bot_name: str, handles: set[str]) -> str:
    """Strip a leading bot mention: '@Eidesoul /mood' -> '/mood'."""
    import re

    body = str(text or "").lstrip()
    pattern = re.compile(MENTION_HEAD_RE)
    match = pattern.match(body)
    while match is not None:
        handle = match.group(1).lstrip("+")
        digits = "".join(ch for ch in handle if ch.isdigit()) or handle.lower()
        if handle.casefold() != str(bot_name or "").casefold() and digits not in handles:
            break
        body = body[match.end() :]
        match = pattern.match(body)
    return body


class Agent:
    """One process, one heart: every gateway calls the same `handle`."""

    def __init__(
        self,
        config: Config,
        db,
        *,
        llm: LLM | None,
        hub=None,
        windows: WorkingMemory,
        history,
        memory: MemoryBackend,
        ledger,
        skills=None,
        recipes=None,
        affect=None,
        style=None,
        ambient=None,
        currents=None,
        vibe=None,
        gateways: tuple[GatewayCore, ...] = (),
        documents=None,
        identity=None,
        approvals: ApprovalStore | None = None,
        safety: SafetyPolicy | None = None,
        runtime: RuntimeFacts | None = None,
    ) -> None:
        self.config = config
        self.soul = config.soul
        self.db = db
        self.llm = llm
        self.hub = hub
        self.windows = windows
        self.history = history
        self.memory = memory
        self.ledger = ledger
        self.skills = skills
        self.recipes = recipes
        self.affect = affect
        self.style = style
        self.ambient = ambient
        self.currents = currents
        self.vibe = vibe
        self.gateways = tuple(gateways)
        self.documents = documents
        self.identity = identity or IdentityStore(db)
        self.approvals = approvals or ApprovalStore(db, config.approval)
        self.safety = safety or build_safety(config.approval)
        self.sandbox_guard = SandboxGuard(
            config.guard, config.approval.sensitive_commands, self.llm
        )
        self.mode = "standby"
        self.lease = None
        self.tasks: set[asyncio.Task] = set()
        self.turns = TurnStore(db)
        self.outbox = OutboundLedger(db)
        self.delivery = Delivery(self)
        self.backlog: dict[str, int] = {}
        self._thread_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._lock_waiters: dict[str, int] = {}
        self._turn_tasks: dict[str, asyncio.Task] = {}
        self._pending_tasks: dict[str, asyncio.Task] = {}
        self._turn_gateways: dict[str, GatewayCore] = {}
        self._resume_times: deque[float] = deque()
        self._shutdown_gateways: dict[str, GatewayCore] = {}
        self._cancel_reasons: dict[str, str] = {}
        self.draining = False
        self.boot_epoch = int(time.time() * 1000)
        self.shutdown_epoch = 0
        self._reaper_task: asyncio.Task | None = None
        gateway_tools = [handler for gateway in self.gateways for handler in gateway.tools()]
        gateway_commands = [
            handler for gateway in self.gateways for handler in gateway.commands()
        ]
        self.plugins = plugins.load(
            plugins.PluginLoader.from_config(config),
            tools=[
                *CORE_TOOLS.reserved(),
                *(value for h in gateway_tools for value in (h.name, *h.aliases)),
            ],
            commands=[
                *CORE_COMMANDS.reserved(),
                *(value for h in gateway_commands for value in (h.name, *h.aliases)),
            ],
        )
        handlers = [*CORE_TOOLS.by_name.values(), *gateway_tools, *self.plugins.tools]
        self.tools = Registry(handlers, "tool")
        handlers = [
            *CORE_COMMANDS.by_name.values(),
            *gateway_commands,
            *self.plugins.commands,
        ]
        self.commands = Registry(handlers, "command")
        self._sync_soul_rules()
        self.runtime = runtime or RuntimeFacts(
            db_label=getattr(db, "label", ""), paths=config.paths
        )
        self.runtime.paths = config.paths
        self.runtime.db_label = getattr(db, "label", "")
        self.runtime.gateways = tuple(gateway.name for gateway in self.gateways)
        self.runtime.memory_status = memory_status(memory)
        io.configure(config.io.workers)
        self.privacy = PrivacyService(self)

    # ------------------------------------------------------------- util

    def _sync_soul_rules(self) -> str:
        """ADR-0008 §4.3: mirror the soul's rules and promote the ones that earned it.

        Best effort: a store without the port (a fake, a read-only backend) or a DB failure
        logs one line and never blocks the boot.
        """
        rules = tuple(getattr(self.soul, "rules", ()) or ())
        sync = getattr(self.memory, "sync_soul_rules", None)
        promote = getattr(self.memory, "promote_soul_rules", None)
        try:
            if callable(sync) and rules:
                sync(rules)
            if callable(promote):
                promoted = int(promote() or 0)
                if promoted:
                    log.info("soul rules promoted: %d", promoted)
        except Exception:
            log.warning("soul rule sync failed; continuing", exc_info=True)
            return "failed"
        return "ok"

    def spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # ------------------------------------------------------------- gate

    def _send_gate(self) -> int:
        """Primary gate before every transport call (ADR-0017 §4.4, ADR-0012 §4.3)."""
        expected = int(ACTIVE_GENERATION.get() or 0)
        if self.mode != "primary":
            self.runtime.refuse_standby()
            raise StandbyError("this instance is standby")
        lease = self.lease
        if lease is not None:
            live = lease.current_generation()
            if live is None or live != int(lease.generation) or (
                expected and int(expected) != int(live)
            ):
                self.runtime.refuse_standby()
                log.warning(
                    "send refused: generation moved turn=%s live=%s owned=%s",
                    expected,
                    live,
                    getattr(lease, "generation", None),
                )
                raise StandbyError("the lease generation moved on")
        return expected or (int(lease.generation) if lease is not None else 0)

    def _accepting(self) -> bool:
        return self.mode == "primary" and not self.draining

    @asynccontextmanager
    async def _thread_lock(self, thread_id: str) -> AsyncIterator[None]:
        """One conversation is one sequence; different threads run in parallel (ADR-0017 §4.2)."""
        lock = self._thread_locks.get(thread_id)
        if lock is None:
            lock = asyncio.Lock()
            self._thread_locks[thread_id] = lock
        self._thread_locks.move_to_end(thread_id)
        self._lock_waiters[thread_id] = self._lock_waiters.get(thread_id, 0) + 1
        self._evict_locks()
        try:
            async with lock:
                yield
        finally:
            waiting = self._lock_waiters.get(thread_id, 1) - 1
            if waiting > 0:
                self._lock_waiters[thread_id] = waiting
            else:
                self._lock_waiters.pop(thread_id, None)
            self._evict_locks()

    def _evict_locks(self) -> None:
        limit = max(1, int(self.config.concurrency.max_chat_locks))
        while len(self._thread_locks) > limit:
            for key, lock in self._thread_locks.items():
                if not lock.locked() and not self._lock_waiters.get(key):
                    del self._thread_locks[key]
                    break
            else:
                return

    # ----------------------------------------------------------- turns

    async def cancel_turn(self, thread_id: str, *, reason: str = "user_stop") -> bool:
        """Cancel the task running in this thread only (ADR-0013 §7.1)."""
        task = self._turn_tasks.get(thread_id) or self._pending_tasks.get(thread_id)
        if task is None or task.done():
            return False
        turn_id = getattr(task, "_eidesoul_turn_id", "")
        if turn_id:
            self._cancel_reasons[turn_id] = reason
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        if turn_id:
            self.turns.interrupt(turn_id, reason, force=True)
        return True

    def _interrupt(self, turn_id: str, reason: str, *, force: bool = False) -> None:
        try:
            self.turns.interrupt(turn_id, reason, force=force)
        except Exception:
            log.exception("failed to mark turn %s interrupted (%s)", turn_id, reason)

    def _finish(self, turn_id: str, *, planned: int = 0) -> None:
        try:
            self.turns.finish(turn_id, planned=planned)
        except Exception:
            log.exception("failed to mark turn %s delivered", turn_id)

    def _step_recorder(self, turn_id: str, step: str):
        def record(tool, args, result, started, finished, status: str = "executed"):
            try:
                self.turns.record_step(
                    turn_id,
                    step=step,
                    tool=str(tool),
                    args=args,
                    result=result,
                    status=str(status or "executed"),
                    started_at=started,
                    finished_at=finished,
                )
            except Exception:
                log.exception("failed to record a tool step turn=%s tool=%s", turn_id, tool)

        return record

    def _step_lookup(self, turn_id: str):
        def lookup(tool, args):
            found = self.turns.find_step(turn_id, str(tool), args_hash(tool, args))
            if found is None:
                return None
            if found["status"] != "executed":
                return None
            if str(tool) in SIDE_EFFECTING:
                raise SideEffectAlreadyRan(
                    f"{tool} already executed in this turn; refusing to repeat it"
                )
            return str(found["result"] or "")

        return lookup

    async def _notice(
        self,
        thread_id: str,
        gateway: GatewayCore,
        key: str,
        *,
        scope_key: str,
        kind: str,
        params: dict | None = None,
    ) -> bool:
        return await self.delivery.notice(
            thread_id,
            gateway,
            key,
            scope_key=scope_key,
            kind=kind,
            params=params,
        )

    async def _notice_timeout(self, thread_id: str, turn_id: str, gateway) -> None:
        await self.delivery.notice_timeout(thread_id, turn_id, gateway)

    async def _notice_interrupted(self, turn_id: str, thread_id: str, gateway) -> None:
        await self.delivery.notice_interrupted(turn_id, thread_id, gateway)

    # --------------------------------------------------------- shutdown

    def begin_drain(self) -> int:
        """Enter `draining`; intake stops before the drain clock starts (ADR-0011 §4.4)."""
        self.draining = True
        self.shutdown_epoch = int(time.time() * 1000)
        self.runtime.phase = "draining"
        return self.shutdown_epoch

    async def drain(self, seconds: float) -> dict[str, str]:
        """Wait for in-flight turns; return the ones still running after the budget."""
        pending: dict[str, str] = {}
        tasks: dict[str, asyncio.Task] = dict(self._pending_tasks)
        tasks.update(self._turn_tasks)
        items = [(thread, task) for thread, task in tasks.items() if not task.done()]
        self._shutdown_gateways = dict(self._turn_gateways)
        if not items:
            return pending
        _done, waiting = await asyncio.wait(
            [task for _, task in items], timeout=max(0.0, float(seconds))
        )
        by_task = {task: thread for thread, task in items}
        for task in waiting:
            thread = by_task[task]
            turn_id = getattr(task, "_eidesoul_turn_id", "")
            if turn_id:
                self._cancel_reasons[turn_id] = "shutdown"
            pending[thread] = turn_id
            task.cancel()
        if waiting:
            await asyncio.gather(*waiting, return_exceptions=True)
        return pending

    async def shutdown_notices(self, pending: dict[str, str], seconds: float) -> int:
        """One `notice:shutdown` per chat with an unfinished turn, inside its own budget."""
        self.runtime.phase = "noticed"
        if not pending:
            return 0
        sent = 0
        deadline = time.monotonic() + max(0.0, float(seconds))
        for thread, turn_id in pending.items():
            if time.monotonic() >= deadline:
                break
            if sent and not bool(self.config.turns.shutdown.notice_per_chat):
                break
            gateway = self._shutdown_gateways.get(thread) or (
                self.gateways[0] if self.gateways else None
            )
            if gateway is None:
                continue
            self._interrupt(turn_id, "shutdown", force=True)
            if await self._notice(
                thread,
                gateway,
                "turn.notice.shutdown",
                scope_key=f"notice:{thread}:shutdown:{self.shutdown_epoch}",
                kind="notice:shutdown",
            ):
                sent += 1
            try:
                self.turns.stamp_shutdown(turn_id, self.shutdown_epoch)
            except Exception:
                log.debug("failed to stamp the shutdown epoch turn=%s", turn_id, exc_info=True)
        self.runtime.phase = "stopped"
        return sent

    # --------------------------------------------------------- recovery

    def _gateway_for(self, thread_id: str) -> GatewayCore | None:
        """The body that serves this thread: turn map, then the durable route, then the first."""
        found = self._turn_gateways.get(thread_id)
        if found is not None:
            return found
        lookup = getattr(self.history, "gateway_of", None)
        if callable(lookup):
            try:
                name = str(lookup(str(thread_id)) or "")
            except Exception:
                log.debug("route lookup failed thread=%s", thread_id, exc_info=True)
                name = ""
            if name:
                match = next((item for item in self.gateways if item.name == name), None)
                if match is not None:
                    return match
        return self.gateways[0] if self.gateways else None

    @staticmethod
    def _is_action(row: dict) -> bool:
        """`turns.category` marks a single-action turn: never consolidated away (ADR-0013 §11)."""
        return str(row.get("category") or "chat") == "action"

    def _expire_consolidated(self, thread: str, turn_ids: list[str]) -> int:
        """Expire the consolidated tail of one chat and let the away block explain it."""
        expired = 0
        for turn_id in turn_ids:
            try:
                self.turns.expire(turn_id, "consolidate")
            except Exception:
                log.exception("failed to expire consolidated turn %s", turn_id)
                continue
            expired += 1
        if expired:
            self.backlog[thread] = self.backlog.get(thread, 0) + expired
            log.info("recovery: %d turn(s) consolidated chat=%s", expired, thread)
        return expired

    def _select_resume(self, rows: list[dict], allowed: int) -> list[dict]:
        """Pace, fair-share, and consolidate one backlog (ADR-0013 §11).

        Selection is round-robin across chats so one busy room cannot starve the others; the
        global minute budget bounds the whole pass and the per-chat quota bounds one room. Under
        `per_message_then_consolidate` the rows past that quota are expired with
        `reason=consolidate`, which the away block turns into one consolidated answer; an
        `action` turn is always selected on its own and never expired.
        """
        cfg = self.config.turns
        policy = str(cfg.backlog_policy)
        budget = max(0, int(allowed))
        if not budget:
            return []
        groups = self._backlog_groups(rows)
        if policy == "last_only":
            return [group[-1] for group in groups.values()][:budget]
        if policy == "consolidate":
            return self._consolidate(groups)[:budget]
        quota = max(1, int(cfg.max_resume_per_chat))
        actions, chats = self._split_actions(groups)
        taken = dict.fromkeys(groups, 0)
        selected = self._round_robin(groups, actions, chats, taken, quota, budget)
        if policy == "per_message_then_consolidate":
            for thread in sorted(groups):
                if taken[thread] < quota:
                    continue
                self._expire_consolidated(thread, [str(row["id"]) for row in chats[thread]])
        return selected

    @staticmethod
    def _backlog_groups(rows: list[dict]) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = {}
        for row in sorted(
            rows, key=lambda item: (str(item["thread_id"]), float(item["created_at"]))
        ):
            groups.setdefault(str(row["thread_id"]), []).append(row)
        return groups

    @staticmethod
    def _split_actions(
        groups: dict[str, list[dict]],
    ) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
        actions = {
            thread: [row for row in group if Agent._is_action(row)]
            for thread, group in groups.items()
        }
        chats = {
            thread: [row for row in group if not Agent._is_action(row)]
            for thread, group in groups.items()
        }
        return actions, chats

    def _consolidate(self, groups: dict[str, list[dict]]) -> list[dict]:
        """`consolidate`: the last chat row per room plus every action row, oldest first."""
        chosen: list[dict] = []
        for thread in sorted(groups):
            group = groups[thread]
            chats = [row for row in group if not self._is_action(row)]
            chosen.extend(row for row in group if self._is_action(row))
            if chats:
                chosen.append(chats[-1])
            self._expire_consolidated(thread, [str(row["id"]) for row in chats[:-1]])
        chosen.sort(key=lambda row: float(row["created_at"]))
        return chosen

    def _round_robin(
        self,
        groups: dict[str, list[dict]],
        actions: dict[str, list[dict]],
        chats: dict[str, list[dict]],
        taken: dict[str, int],
        quota: int,
        budget: int,
    ) -> list[dict]:
        """One row per room per lap: an action row first, then a chat row under the quota."""
        selected: list[dict] = []
        while len(selected) < budget:
            progressed = False
            for thread in sorted(groups):
                if len(selected) >= budget:
                    break
                if actions[thread]:
                    selected.append(actions[thread].pop(0))
                    progressed = True
                    continue
                if taken[thread] >= quota or not chats[thread]:
                    continue
                selected.append(chats[thread].pop(0))
                taken[thread] += 1
                progressed = True
            if not progressed:
                break
        return selected

    async def recover(self) -> int:
        """Recovery pass on the primary (ADR-0011 §4.5, ADR-0013 §8)."""
        cfg = self.config.turns
        if not cfg.resume or not self._accepting():
            return 0
        generation = int(getattr(self.lease, "generation", 0) or 0)
        rows, dead = await io.run(
            self.turns.claim_expired,
            owner=getattr(self.lease, "instance_id", "") or "local",
            generation=generation,
            lease_seconds=max(60.0, float(cfg.timeout_seconds) + 30.0),
            limit=max(1, int(self.config.worker.reaper.batch)),
            max_attempts=int(cfg.max_attempts),
            dead_letter_after=min(
                int(self.config.worker.retry.dead_letter_after),
                int(self.config.worker.retry.attempts),
                int(self.config.llm.retry.worker_attempts),
            ),
            boot_epoch=self.boot_epoch,
        )
        if dead:
            log.warning("recovery: %d turn(s) abandoned after attempts were exhausted", dead)
        moment = time.time()
        self._resume_times = deque(
            stamp for stamp in self._resume_times if moment - stamp < RESUME_WINDOW
        )
        allowed = max(0, int(cfg.max_resume_per_minute) - len(self._resume_times))
        selected = self._select_resume(rows, allowed)
        picked = {str(row["id"]) for row in selected}
        unselected = [str(row["id"]) for row in rows if str(row["id"]) not in picked]
        if unselected:
            released = await io.run(self.turns.release, unselected)
            if released:
                log.info("recovery: %d turn(s) released for a later pass", released)
        resumed = 0
        noticed: set[str] = set()
        for row in selected:
            thread = str(row["thread_id"])
            turn_id = str(row["id"])
            message = await io.run(self.history.find, thread, turn_id)
            if message is None:
                self.turns.expire(turn_id, "resume: message missing")
                continue
            age = time.time() - message.when.timestamp()
            if age > float(cfg.stale_after_seconds):
                self.turns.expire(turn_id, "resume: stale")
                continue
            if age > float(cfg.max_age_seconds) and str(cfg.backlog_policy) not in {
                "consolidate",
                "per_message_then_consolidate",
            }:
                self.turns.expire(turn_id, "resume: too old")
                continue
            gateway = self._gateway_for(thread)
            if gateway is None:
                log.warning("recovery: no gateway for chat=%s turn=%s", thread, turn_id)
                continue
            downtime = time.time() - float(
                row.get("previous_updated_at") or row.get("updated_at") or 0.0
            )
            if (
                thread not in noticed
                and downtime > float(self.config.turns.shutdown.resume_notice.after_seconds)
            ):
                noticed.add(thread)
                with contextlib.suppress(Exception):
                    await self._notice(
                        thread,
                        gateway,
                        "turn.notice.resumed",
                        scope_key=f"notice:{thread}:resumed:{self.boot_epoch}",
                        kind="notice:resumed",
                    )
            inbound = Percept(
                source=gateway.name,
                thread=thread,
                actor=str(row.get("person_id") or ""),
                kind=MESSAGE,
                payload=message,
                hints={},
            )
            self._resume_times.append(time.time())
            self.spawn(self.handle(inbound, gateway, claimed=row))
            resumed += 1
        if resumed:
            log.info("recovery resumed %d turn(s)", resumed)
        return resumed

    def start_reaper(self) -> None:
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(self._reaper_loop())

    async def stop_reaper(self) -> None:
        task = self._reaper_task
        self._reaper_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

    async def _reaper_loop(self) -> None:
        every = max(1.0, float(self.config.worker.reaper.every_seconds))
        while not self.draining:
            await asyncio.sleep(every)
            if self.draining:
                return
            try:
                await self.recover()
            except Exception:
                log.exception("reaper pass failed")

    def _role_set(self, inbound: Percept) -> tuple[Role, ...]:
        declared = inbound.hints.get("roles") or ()
        names = (declared,) if isinstance(declared, str) else tuple(str(item) for item in declared)
        if names:
            return self.config.roles.override(names)
        return self.config.roles.for_person(inbound.actor)

    @staticmethod
    def _sender(inbound: Percept) -> str:
        payload = inbound.payload
        return payload.sender_id or payload.sender_address or inbound.thread

    def tool_context(
        self,
        thread_id: str,
        sender: str,
        roles: tuple[Role, ...],
        gateway: GatewayCore | None,
        *,
        approved: bool = False,
        approval_id: str = "",
        request: str = "",
        person_id: str = "",
        direct: bool = False,
    ) -> ToolContext:
        return ToolContext(
            self.config,
            roles,
            tool_patterns(roles),
            thread_id=thread_id,
            sender=sender,
            request=request,
            history=self.history,
            memory=self.memory,
            ledger=self.ledger,
            skills=self.skills,
            recipes=self.recipes,
            affect=self.affect,
            agent=self,
            gateway=gateway,
            approved=approved,
            approval_id=approval_id,
            person_id=person_id,
            direct=direct,
        )

    def command_context(
        self,
        thread_id: str,
        sender: str,
        roles: tuple[Role, ...],
        gateway: GatewayCore | None,
        args: str = "",
        inbound: Percept | None = None,
    ) -> CommandContext:
        specs = tools_for(self.tools, roles) + (self.hub.specs(roles) if self.hub else [])
        return CommandContext(
            agent=self,
            gateway=gateway,
            roles=roles,
            thread_id=thread_id,
            sender=sender,
            direct=bool(inbound.hints.get("dm", False)) if inbound is not None else True,
            args=args,
            commands=tuple(commands_for(self.commands, roles)),
            tools=tuple(specs),
            inbound=inbound,
        )

    def _specs(self, roles: tuple[Role, ...]) -> list[Handler]:
        specs = list(tools_for(self.tools, roles))
        if self.hub is not None:
            specs += self.hub.specs(roles)
        return specs

    def _decision_transcript(self, thread_id: str) -> str:
        entries: list[Message] = []
        try:
            entries = list(self.history.recent(thread_id, DECISION_HISTORY))
        except Exception:  # a broken archive must not fail the decision
            log.debug(
                "failed to read the archive for the decision chat=%s", thread_id, exc_info=True
            )
        if not entries:
            entries = self.windows.get(thread_id)[-DECISION_FALLBACK_HISTORY:]
        lines = [
            prompt_entry(entry, self.soul.name, DECISION_LINE_CHARS) for entry in entries
        ]
        return "\n".join(line for line in lines if line)

    # ------------------------------------------------------------ outbound

    async def deliver_text(
        self,
        thread_id: str,
        text: str,
        *,
        gateway: GatewayCore,
        reply: Percept | None = None,
        kind: str = "",
    ) -> str:
        return await self.delivery.text(
            thread_id, text, gateway=gateway, reply=reply, kind=kind
        )

    async def deliver_file(
        self,
        thread_id: str,
        path: Path,
        caption: str = "",
        *,
        gateway: GatewayCore,
        reply: Percept | None = None,
    ) -> str:
        return await self.delivery.file(
            thread_id, path, caption, gateway=gateway, reply=reply
        )

    async def deliver_sticker(
        self, thread_id: str, message_id: str, *, gateway: GatewayCore
    ) -> str | None:
        return await self.delivery.sticker(thread_id, message_id, gateway=gateway)

    async def _typing(self, gateway: GatewayCore, thread_id: str, composing: bool) -> None:
        await self.delivery.typing(gateway, thread_id, composing)

    async def _prepare_media(
        self, gateway: GatewayCore, thread_id: str, entry: Message
    ) -> None:
        await self.delivery.prepare_media(gateway, thread_id, entry)

    async def send_text(self, thread_id: str, text: str, *, gateway: GatewayCore | None) -> str:
        return await self.delivery.send_text(thread_id, text, gateway=gateway)

    async def send_file(
        self, thread_id: str, path: Path, caption: str = "", *, gateway: GatewayCore | None
    ) -> str:
        return await self.delivery.send_file(thread_id, path, caption, gateway=gateway)

    # ------------------------------------------------------------- turn

    async def _forget_intent(self, inbound: Percept, message) -> None:
        """ADR-0005 §4.6: a "forget that …" turn removes the one entry it can mean.

        The deterministic veto+forget path runs before the turn, so the ordinary reply can
        confirm it. Ambiguity deletes nothing and the turn continues unchanged.
        """
        settings = getattr(self.config.memory, "intent", None)
        kind = await io.run(
            intent.classify, str(message.text or ""), self.soul.language, settings
        )
        if kind != "forget":
            return
        forget = getattr(self.memory, "forget_by_intent", None)
        if not callable(forget):
            return
        try:
            removed = await forget(
                str(message.text or ""),
                person_id=str(inbound.actor or ""),
                thread_id=str(inbound.thread or ""),
            )
        except Exception:
            log.warning("forget intent failed; the turn continues", exc_info=True)
            return
        if removed:
            log.info(
                "forget intent removed %d entr%s chat=%s",
                len(removed),
                "y" if len(removed) == 1 else "ies",
                inbound.thread,
            )

    def _identify(self, inbound: Percept, gateway: GatewayCore) -> Percept:
        """Core-owned identity: the body hands in an address, the core assigns the id."""
        raw = (
            inbound.actor
            or inbound.payload.sender_id
            or inbound.payload.sender_address
            or self._sender(inbound)
        )
        source = inbound.source or gateway.name
        try:
            return replace(inbound, actor=self.identity.resolve(source, raw))
        except Exception:
            log.exception("identity resolve failed source=%s", source)
            return replace(inbound, actor=inbound.actor or raw)

    async def handle(
        self,
        inbound: Percept,
        gateway: GatewayCore,
        *,
        claimed: dict | None = None,
    ) -> list[Action]:
        """Intake + watchdog with the turn's correlation ids bound (ADR-0019 §4.2)."""
        token = str(getattr(inbound.payload, "id", "") or "")
        with observability.bind(
            turn_id=token,
            thread_id=inbound.thread,
            gateway=getattr(gateway, "name", "") or "",
        ):
            return await self._handle_message(inbound, gateway, claimed=claimed)

    async def _handle_message(
        self,
        inbound: Percept,
        gateway: GatewayCore,
        *,
        claimed: dict | None = None,
    ) -> list[Action]:
        """Intake + watchdog: the one door every body calls (ADR-0013 §7, ADR-0017 §4.3)."""
        message = inbound.payload
        thread_id = inbound.thread
        if message.outgoing or bool(inbound.hints.get("self")):
            self.windows.append(thread_id, message)
            if self.ambient is not None:
                self.ambient.note(thread_id, message)
            return []
        if not self._accepting():
            # A standby instance refuses intake and sends nothing (ADR-0017 §4.4).
            self.runtime.refuse_standby()
            log.info(
                "intake refused chat=%s mode=%s draining=%s",
                thread_id,
                self.mode,
                self.draining,
            )
            return []
        inbound = self._identify(inbound, gateway)
        turn_id = str(message.id or "")
        if not turn_id:
            log.warning("message without an id cannot become a turn chat=%s", thread_id)
            return []
        try:
            self.history.intake(
                thread_id,
                message,
                inbound.actor,
                language=str(self.soul.language or "id"),
                audience="private" if bool(inbound.hints.get("dm")) else "group",
                # ADR-0013 §11: the body marks a single-action turn; the backlog never folds it.
                category="action" if inbound.hints.get("action") is True else "chat",
                gateway=getattr(gateway, "name", "") or "",
            )
        except Exception:
            log.exception(
                "intake failed chat=%s id=%s: the turn is not processed", thread_id, turn_id
            )
            raise
        self.windows.append(thread_id, message, persist=False)
        if self.ambient is not None:
            self.ambient.note(thread_id, message)
        await self._forget_intent(inbound, message)
        existing = self.turns.get(turn_id)
        if existing is not None and str(existing["status"]) in TERMINAL_TURN:
            log.info("turn %s already %s; no repeat", turn_id, existing["status"])
            return []
        age = time.time() - message.when.timestamp()
        backlog = float(getattr(gateway, "backlog_seconds", 0.0) or 0.0)
        if claimed is None and backlog > 0 and age > backlog:
            self.backlog[thread_id] = self.backlog.get(thread_id, 0) + 1
            try:
                self.turns.expire(turn_id, "backlog")
            except Exception:
                log.exception("failed to expire the backlog turn %s", turn_id)
            log.info("old message chat=%s age=%.0fs: archive only", thread_id, age)
            return []

        text = message.text
        body = command_body(text, self.soul.name, gateway.self_handles())
        is_command = body.startswith("/")
        timeout = float(self.config.turns.timeout_seconds)
        deadline = time.monotonic() + timeout
        task = asyncio.create_task(
            self._run_turn(
                inbound,
                gateway,
                turn_id,
                deadline=deadline,
                is_command=is_command,
                body=body,
                claimed=claimed,
            )
        )
        task._eidesoul_turn_id = turn_id
        if not is_command:
            self._pending_tasks[thread_id] = task
        try:
            return await asyncio.wait_for(task, timeout=timeout)
        except TimeoutError:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
            self._interrupt(turn_id, "timeout", force=True)
            if not is_command:
                await self._notice_timeout(thread_id, turn_id, gateway)
            return []
        except DeadlineExceeded:
            self._interrupt(turn_id, "timeout", force=True)
            if not is_command:
                await self._notice_timeout(thread_id, turn_id, gateway)
            return []
        except asyncio.CancelledError:
            current = asyncio.current_task()
            external = current is not None and current.cancelling()
            if task.cancelled() and not external:
                reason = self._cancel_reasons.get(turn_id, "cancelled")
                self._interrupt(turn_id, reason, force=True)
                return []
            self._cancel_reasons[turn_id] = "cancelled"
            task.cancel()
            raise
        finally:
            if not is_command:
                if self._pending_tasks.get(thread_id) is task:
                    self._pending_tasks.pop(thread_id, None)
                self._cancel_reasons.pop(turn_id, None)

    async def _run_turn(
        self,
        inbound: Percept,
        gateway: GatewayCore,
        turn_id: str,
        *,
        deadline: float,
        is_command: bool,
        body: str,
        claimed: dict | None = None,
    ) -> list[Action]:
        """One turn, checkpointed; a resumed turn starts from `turns.step`."""
        actions: list[Action] = []
        message = inbound.payload
        thread_id = inbound.thread
        min_call = float(self.config.turns.min_call_seconds)
        generation = int(getattr(self.lease, "generation", 0) or 0)
        lease_seconds = max(60.0, float(self.config.turns.timeout_seconds) + 30.0)
        queue = contextlib.nullcontext() if is_command else self._thread_lock(thread_id)
        async with queue:
            if not is_command:
                self._turn_tasks[thread_id] = asyncio.current_task()
                self._turn_gateways[thread_id] = gateway
            claimed = claimed or self.turns.claim(
                turn_id,
                owner=getattr(self.lease, "instance_id", "") or "local",
                generation=generation,
                lease_seconds=lease_seconds,
                boot_epoch=self.boot_epoch,
            )
            if claimed is None:
                log.info("turn %s is terminal or owned elsewhere", turn_id)
                if not is_command and self._turn_tasks.get(thread_id) is asyncio.current_task():
                    self._turn_tasks.pop(thread_id, None)
                    self._turn_gateways.pop(thread_id, None)
                return actions
            if self.draining:
                self._interrupt(turn_id, "shutdown", force=True)
                if not is_command and self._turn_tasks.get(thread_id) is asyncio.current_task():
                    self._turn_tasks.pop(thread_id, None)
                    self._turn_gateways.pop(thread_id, None)
                return actions
            token = ACTIVE_GENERATION.set(generation)
            try:
                step = str(claimed["step"] or "received")
                attempt = int(claimed["attempt"] or 0)
                if attempt > int(self.config.turns.max_attempts):
                    self._interrupt(turn_id, "resume abandoned: attempts exhausted", force=True)
                    await self._notice_interrupted(turn_id, thread_id, gateway)
                    return actions
                if step in {"received", "decided"} and self.turns.side_effect_done(turn_id):
                    self._interrupt(
                        turn_id, "non-idempotent action already executed", force=True
                    )
                    await self._notice_interrupted(turn_id, thread_id, gateway)
                    return actions
                skip_decision = step in {"decided", "drafted", "reviewed", "sending"}
                skip_draft = step in {"drafted", "reviewed", "sending"}
                resuming = step != "received"
                resume_reply = str(claimed.get("reviewed_text") or claimed.get("draft_text") or "")

                text = message.text
                crisis = guard.is_crisis(text)
                roles = self._role_set(inbound)
                if not is_command and await self.privacy.handle_pending(
                    inbound, gateway, self._sender(inbound)
                ):
                    self._finish(turn_id)
                    return actions
                if not is_command and not crisis and self.llm is None:
                    self._finish(turn_id)
                    return actions
                head_word, _, rest = body[1:].partition(" ") if is_command else ("", "", "")
                name = head_word.lower()
                direct = (
                    bool(inbound.hints.get("dm"))
                    or bool(inbound.hints.get("mention"))
                    or bool(inbound.hints.get("reply"))
                )

                missed = self.backlog.pop(thread_id, 0) if not is_command and not resuming else 0
                away = away_block(missed)
                vibe = ""
                if (
                    self.vibe is not None
                    and not is_command
                    and not crisis
                    and not resuming
                    and text.strip()
                ):
                    try:
                        vibe = await self.vibe.observe(thread_id, text)
                    except Exception:
                        log.exception("failed to update vibe chat=%s", thread_id)
                emotion_block = ""
                emotion = None
                if not is_command and self.affect is not None and not resuming:
                    try:
                        emotion = self.affect.turn_start(thread_id, message.id)
                    except Exception:
                        log.exception("failed to open the emotion turn chat=%s", thread_id)
                    try:
                        emotion_block = self.affect.prompt(thread_id, inbound.actor)
                    except Exception:
                        log.exception("failed to read the emotion state chat=%s", thread_id)

                called = bool(inbound.hints.get("mention")) or bool(inbound.hints.get("reply"))
                unprompted = (
                    not bool(inbound.hints.get("dm"))
                    and not is_command
                    and not crisis
                    and not called
                )
                decision: Decision | None
                safety = ""
                if skip_decision:
                    decision = decision_from_row(claimed)
                    if decision is None:
                        decision = Decision(
                            addressed="me" if direct else "unknown",
                            act="reply",
                            mode="daily",
                            tone="neutral",
                            bubbles=1,
                            appraisal=Appraisal("neutral", 0.0, 0.0, 0.0),
                            relationship=RelationshipDelta(),
                            reason="resume",
                        )
                    elif skip_draft and decision.act != "reply":
                        # A stored reply exists: resuming must finish it, not re-decide silence.
                        decision = replace(decision, act="reply")
                    safety = CRISIS_SAFETY if crisis else ""
                elif crisis:
                    decision = Decision(
                        addressed="me",
                        act="reply",
                        mode="daily",
                        tone="neutral",
                        bubbles=1,
                        appraisal=Appraisal("neutral", 0.0, 0.0, 0.0),
                        relationship=RelationshipDelta(),
                        reason="crisis",
                    )
                    safety = CRISIS_SAFETY
                elif is_command:
                    decision = None
                elif unprompted:
                    decision = Decision(
                        addressed="group",
                        act="ignore",
                        mode="daily",
                        tone="neutral",
                        bubbles=1,
                        appraisal=Appraisal("neutral", 0.0, 0.0, 0.0),
                        relationship=RelationshipDelta(),
                        reason="group without a call",
                    )
                else:
                    annoyance = 0.0
                    if emotion is not None and self.affect is not None:
                        try:
                            annoyance = next(
                                (
                                    float(row["annoyance"])
                                    for row in self.affect.known_people(thread_id, 20)
                                    if row["person_id"] == inbound.actor
                                ),
                                0.0,
                            )
                        except Exception:
                            log.debug(
                                "failed to read annoyance chat=%s", thread_id, exc_info=True
                            )
                    decision = await decide_turn(
                        self.llm,
                        thread_id=thread_id,
                        chat_name=gateway.chat_name(thread_id),
                        current=prompt_entry(message, self.soul.name, DECISION_LINE_CHARS),
                        transcript=self._decision_transcript(thread_id),
                        emotion=emotion,
                        relationship=emotion_block,
                        direct=bool(inbound.hints.get("dm")),
                        mentioned=bool(inbound.hints.get("mention")),
                        replied=bool(inbound.hints.get("reply")),
                        away=away,
                        vibe=vibe,
                        soul=self.soul,
                        deadline=deadline,
                        min_call_seconds=min_call,
                        annoyance=annoyance,
                    )

                if not is_command and self.affect is not None and not resuming:
                    try:
                        self.affect.turn_end(
                            thread_id,
                            inbound.actor,
                            message.sender_name,
                            message.text,
                            roles[0].name,
                            message.sender_address,
                            None if crisis else decision,
                            message.id,
                        )
                    except Exception:
                        log.exception("failed to close the emotion turn chat=%s", thread_id)

                if not is_command:
                    if decision is not None and decision.act == "ignore":
                        log.info(
                            "diam chat=%s addressed=%s reason=%s",
                            thread_id,
                            decision.addressed,
                            decision.reason,
                        )
                        self._finish(turn_id)
                        return actions
                    if decision is not None and decision.act == "react":
                        label = await self.deliver_sticker(
                            thread_id, message.id, gateway=gateway
                        )
                        if label:
                            actions.append(
                                Action(
                                    capability="send_sticker",
                                    target=thread_id,
                                    payload=label,
                                    approval_class=CONSENT,
                                )
                            )
                        self._finish(turn_id)
                        return actions

                if not is_command and not skip_decision:
                    self.turns.checkpoint(
                        turn_id,
                        "decided",
                        decision_json=asdict(decision) if decision is not None else None,
                    )
                await self._typing(gateway, thread_id, True)
                typing = asyncio.create_task(self._keep_typing(thread_id, gateway))
                try:
                    log.info(
                        "message chat=%s sender=%s roles=%s act=%s mode=%s tone=%s addressed=%s",
                        thread_id,
                        message.sender_id,
                        ",".join(role.name for role in roles),
                        getattr(decision, "act", "command"),
                        getattr(decision, "mode", "-"),
                        getattr(decision, "tone", "-"),
                        getattr(decision, "addressed", "-"),
                    )
                    reply: str | None = None
                    memory_ctx = MemoryContext()
                    if skip_draft:
                        reply = resume_reply
                    elif is_command:
                        reply = await self._command(
                            name,
                            rest.strip(),
                            thread_id,
                            self._sender(inbound),
                            roles,
                            gateway,
                            inbound,
                        )
                    else:
                        specs = self._specs(roles)
                        cross_send = any(spec.name == "send_to" for spec in specs)
                        mood = None
                        if emotion is not None:
                            mood = (
                                (float(emotion["pleasure"]) + 1.0) / 2.0,
                                float(emotion["arousal"]),
                            )
                        memory_block = ""
                        style_block = topic_block = person_style = ""
                        try:
                            memory_ctx = await self.memory.context(
                                person_id=inbound.actor,
                                thread_id=thread_id,
                                query=message.text,
                                direct=bool(inbound.hints.get("dm")),
                                mood=mood,
                                max_items=self.config.memory.max_items,
                                max_chars=self.config.memory.max_chars,
                            )
                            memory_block = memory_ctx.text
                        except Exception:
                            log.exception("failed to read memory chat=%s", thread_id)
                        actions_block = ""
                        if self.ledger is not None:
                            try:
                                actions_block = self.ledger.actions_context()
                            except Exception:
                                log.exception(
                                    "failed to read the action ledger chat=%s", thread_id
                                )
                        skill_block = ""
                        if self.skills is not None:
                            try:
                                skill_block = self.skills.retrieve(
                                    message.text,
                                    thread_id=thread_id,
                                    person_id=inbound.actor,
                                    held_tools=tuple(spec.name for spec in specs),
                                    catalog_tools=tuple(self.tools.by_name),
                                    max_chars=int(self.config.skills.max_chars),
                                )
                            except Exception:
                                log.exception("failed to retrieve procedures")
                        if self.affect is not None:
                            try:
                                topic_block = self.affect.topic_block(thread_id)
                            except Exception:
                                log.exception(
                                    "failed to read the chat topic chat=%s", thread_id
                                )
                        if self.style is not None:
                            try:
                                style_block = self.style.profile(thread_id)
                                person_style = self.style.person_profile(inbound.actor)
                            except Exception:
                                log.debug(
                                    "style: failed to read the chat profile chat=%s",
                                    thread_id,
                                    exc_info=True,
                                )
                        messages = assembly.thread(
                            self.history,
                            thread_id,
                            message,
                            max_chars=self.config.prompt.thread_chars,
                            window=self.windows.get(thread_id),
                        )
                        for entry in messages:
                            with contextlib.suppress(Exception):
                                await self._prepare_media(gateway, thread_id, entry)
                        target, confidence, _source = await display_target(
                            self, inbound.actor, gateway=gateway, messages=messages, message=message
                        )
                        limits = effective_limits(self.config.character, target, confidence)
                        style = self.soul.style
                        lexicon = load_lexicon(style.patterns)
                        register = room_register(
                            [entry.text for entry in messages[-6:] if entry.text.strip()],
                            lexicon,
                        )
                        patterns = select_patterns(
                            style.max_patterns,
                            register=register,
                            target=target,
                            limits=limits,
                            messages=[entry.text for entry in messages[-8:]],
                            packs=style.patterns,
                            registers=style.registers,
                            lexicon=lexicon,
                        )
                        facts = select_facts(
                            style.max_facts,
                            messages=[entry.text for entry in messages[-8:]],
                            packs=style.facts,
                        )
                        character = character_block(
                            limits,
                            target,
                            confidence,
                            gate=self.config.character.gender_gate,
                            jabs=self.config.character.roast_jabs,
                            patterns=patterns,
                            facts=facts,
                            language=self.soul.language,
                        )
                        currents_block = ""
                        if self.currents is not None:
                            try:
                                currents_block = self.currents.block(time.time(), 6)
                            except Exception:
                                log.debug(
                                    "currents: failed to read the context", exc_info=True
                                )
                        try:
                            content = await build_user_content(
                                self.config,
                                thread_id,
                                messages,
                                message,
                                chat_name=gateway.chat_name(thread_id),
                            )
                        except Exception:
                            log.exception(
                                "failed to assemble the payload chat=%s", thread_id
                            )
                            content = fallback_content(self.config, message)
                        tool_ctx = self.tool_context(
                            thread_id,
                            self._sender(inbound),
                            roles,
                            gateway,
                            request=message.text,
                            person_id=inbound.actor,
                            direct=bool(inbound.hints.get("dm")),
                        )
                        bonuses: dict[str, float] = {}
                        if self.affect is not None:
                            try:
                                person_row = next(
                                    (
                                        row
                                        for row in self.affect.known_people(thread_id, 20)
                                        if row["person_id"] == inbound.actor
                                    ),
                                    None,
                                )
                                bonuses = {
                                    "topic": 0.3 if self.affect.pending(thread_id) > 0 else 0.0,
                                    "person_style": (
                                        0.25
                                        if person_row
                                        and int(person_row["interactions"]) >= 15
                                        else 0.0
                                    ),
                                    "style": (
                                        0.15
                                        if person_row
                                        and float(person_row["familiarity"]) >= 0.3
                                        else 0.0
                                    ),
                                    "skills": 0.2 if decision.mode == "professional" else 0.0,
                                }
                            except Exception:
                                log.debug(
                                    "failed to compute the salience bonus chat=%s",
                                    thread_id,
                                    exc_info=True,
                                )
                                bonuses = {}
                        bonuses["memory"] = (
                            0.35
                            if memory_block
                            and keyword_set(message.text, self.soul.language)
                            & keyword_set(memory_block, self.soul.language)
                            else 0.0
                        )
                        answer = await self.llm.run(
                            system_prompt(
                                self.config,
                                roles,
                                runtime=self.runtime,
                                gateway_hints=gateway.prompt_hints(),
                                memory=memory_block,
                                actions=actions_block,
                                skills=skill_block,
                                cross_send=cross_send,
                                topic=topic_block,
                                away=away,
                                vibe=vibe,
                                emotion=emotion_block,
                                style=style_block,
                                person_style=person_style,
                                safety=safety,
                                character=character,
                                research=research_block(self.config.soul.locale),
                                currents=currents_block,
                                sender=message.sender_name,
                                direct=bool(inbound.hints.get("dm")),
                                mode=decision.mode,
                                tone=decision.tone,
                                decision_reason=decision.reason,
                                query=message.text,
                                bonuses=bonuses,
                            ),
                            content,
                            specs,
                            tool_ctx,
                            session=f"eidesoul-{thread_id}",
                            turn_id=turn_id,
                            deadline=deadline,
                            min_call_seconds=min_call,
                            on_step=self._step_recorder(turn_id, "drafted"),
                            lookup_step=self._step_lookup(turn_id),
                        )
                        reply = (
                            answer
                            if answer.strip() or not tool_ctx.sent
                            else t("agent.sent_ack", self.soul.language)
                        )
                        self.turns.checkpoint(turn_id, "drafted", draft_text=reply or "")
                        daily = decision is None or decision.mode != "professional"
                        if (
                            daily
                            and not crisis
                            and reply
                            and not tool_ctx.sent
                        ):
                            recent = "\n".join(
                                f"{entry.sender_name}: {entry.text}"
                                for entry in messages[-6:]
                                if entry.text.strip()
                            )
                            before = reply
                            reply = await polish(
                                self.llm,
                                self.config,
                                reply,
                                latest=message.text,
                                recent=recent,
                                direct=bool(inbound.hints.get("dm")),
                                tone=decision.tone if decision is not None else "neutral",
                                session=f"eidesoul-character-{thread_id}",
                                deadline=deadline,
                                min_call_seconds=min_call,
                            )
                            log.info(
                                "character chat=%s status=%s",
                                thread_id,
                                "rewrite" if reply != before else "pass",
                            )
                            if not reply.strip():
                                reply = before
                        if reply and not crisis:
                            before = reply
                            reply = await regulate(
                                self.llm,
                                self.config,
                                reply,
                                target=target,
                                confidence=confidence,
                                latest=message.text,
                                session=f"eidesoul-character-{thread_id}",
                                is_professional=bool(
                                    decision is not None and decision.mode == "professional"
                                ),
                                deadline=deadline,
                                min_call_seconds=min_call,
                            )
                            log.info(
                                "level chat=%s target=%s/%s status=%s",
                                thread_id,
                                target,
                                f"{confidence:.2f}",
                                "regulated" if reply != before else "pass",
                            )
                        review = await self.privacy.review(
                            inbound,
                            reply,
                            [*memory_ctx.hits, *tool_ctx.memory_hits],
                            gateway,
                        )
                        if review.handled:
                            self._finish(turn_id)
                            return actions
                        reply = review.reply
                        if reply and tool_ctx.cross_reads and guard.cross_chat_leak(
                            reply, tool_ctx.cross_reads
                        ):
                            log.warning("cross-chat verbatim reply blocked chat=%s", thread_id)
                            reply = t("agent.cross_chat_blocked", self.soul.language)
                        self.turns.checkpoint(turn_id, "reviewed", reviewed_text=reply or "")

                    professional = bool(
                        decision is not None and decision.mode == "professional"
                    )
                    humanize = self.config.humanize
                    chunks, pace, quote_all = plan_chunks(
                        reply,
                        gateway=gateway,
                        language=self.soul.language,
                        is_command=is_command,
                        professional=professional,
                        humanize=humanize,
                    )
                    planned_chunks = int(claimed["planned_chunks"] or 0)
                    if resume_reply and planned_chunks and len(chunks) != planned_chunks:
                        # The stored chunk plan no longer matches: sending would duplicate.
                        self._interrupt(
                            turn_id, "resume mismatch: chunk plan changed", force=True
                        )
                        await self._notice_interrupted(turn_id, thread_id, gateway)
                        return actions
                    _sent_ids, sent_actions, stop = await self.delivery.send_reply(
                        turn_id=turn_id,
                        thread_id=thread_id,
                        gateway=gateway,
                        inbound=inbound,
                        chunks=chunks,
                        pace=pace,
                        quote_all=quote_all,
                        generation=generation,
                        partial=str(self.config.turns.partial_delivery or "skip"),
                        humanize=humanize,
                    )
                    actions.extend(sent_actions)
                    if stop == "superseded":
                        self._interrupt(turn_id, "superseded", force=True)
                        return actions
                    if stop == "failed":
                        self.turns.defer(
                            turn_id,
                            "send failed",
                            delay_seconds=min(
                                max(0.1, float(self.config.llm.retry.cap_seconds)),
                                max(
                                    float(self.config.worker.retry.backoff_seconds),
                                    float(self.config.llm.retry.base_seconds),
                                )
                                * (2 ** max(0, attempt - 1))
                                * (
                                    1.0
                                    + random.uniform(0.0, float(self.config.llm.retry.jitter))
                                ),
                            ),
                        )
                    else:
                        touch = getattr(self.memory, "record_uses", None)
                        if touch is not None and memory_ctx.hits:
                            try:
                                await io.run(
                                    touch, turn_id, [hit.id for hit in memory_ctx.hits]
                                )
                            except Exception:
                                log.exception("failed to record memory use chat=%s", thread_id)
                        self._finish(turn_id, planned=len(chunks))
                    self.runtime.pending_approvals = self.approvals.count_pending()
                    self.approvals.expire()
                finally:
                    typing.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await typing
                    await self._typing(gateway, thread_id, False)
            except asyncio.CancelledError:
                reason = self._cancel_reasons.get(turn_id, "cancelled")
                self._interrupt(turn_id, reason, force=True)
                raise
            except SideEffectAlreadyRan as exc:
                log.warning("turn %s interrupted: %s", turn_id, exc)
                self._interrupt(turn_id, str(exc), force=True)
                await self._notice_interrupted(turn_id, thread_id, gateway)
                return actions
            finally:
                ACTIVE_GENERATION.reset(token)
                if not is_command and self._turn_tasks.get(thread_id) is asyncio.current_task():
                    self._turn_tasks.pop(thread_id, None)
                    self._turn_gateways.pop(thread_id, None)
        return actions

    async def _keep_typing(self, thread_id: str, gateway: GatewayCore) -> None:
        while True:
            await asyncio.sleep(TYPING_REFRESH)
            await self._typing(gateway, thread_id, True)

    async def _command(
        self,
        name: str,
        args: str,
        thread_id: str,
        sender: str,
        roles: tuple[Role, ...],
        gateway: GatewayCore,
        inbound: Percept,
    ) -> str:
        if not name:
            return t("agent.help_hint", self.soul.language)
        handler = self.commands.get(name)
        recipe = None
        if handler is None and self.recipes is not None:
            with contextlib.suppress(Exception):
                recipe = self.recipes.get(name)
        if handler is None and recipe is None:
            return t("agent.unknown_command", self.soul.language, name=name)
        names = {role.name for role in roles}
        denied = t(
            "agent.command_denied",
            self.soul.language,
            name=name,
            roles=", ".join(sorted(names)),
        )
        if recipe is not None and not (names & set(recipe.roles)):
            return denied
        if handler is not None and not command_allowed(roles, handler.name):
            return denied
        if recipe is not None:
            from .stores.recipes import run_recipe

            ctx = self.tool_context(thread_id, sender, roles, gateway, request=args)
            if recipe.kind == "recipe":
                return await run_recipe(recipe, args, ctx)
            if self.llm is None:
                return t("agent.no_llm", self.soul.language)
            specs = self._specs(roles)
            return await self.llm.run(
                system_prompt(
                    self.config,
                    roles,
                    runtime=self.runtime,
                    gateway_hints=gateway.prompt_hints(),
                    mode="professional",
                ),
                f"{recipe.prompt}\n\nArguments: {args}".strip(),
                specs,
                ctx,
                session=f"eidesoul-recipe-{thread_id}-{name}",
            )
        ctx = self.command_context(thread_id, sender, roles, gateway, args, inbound)
        return await handler.fn(ctx)

    # ------------------------------------------------------------ hidup

    def request_freeze(self) -> None:
        if self.lease is not None:
            self.lease.request_freeze(self.lease.instance_id)

    def _restart_tokens(self, config: Config) -> dict[str, object]:
        memory = config.memory
        return {
            "workspace": config.workspace,
            "gateways": config.gateways,
            "sandbox": config.sandbox,
            "web_search": config.web_search,
            "mcp": config.mcp,
            "database": config.database,
            "paths": config.paths,
            "llm.api_key_env": config.llm.api_key_env,
            "memory.collection": memory.collection,
            "memory.extraction_model": memory.extraction_model,
            "affect.enabled": config.affect.enabled,
            "affect.vibe.enabled": config.affect.vibe.enabled,
            "affect.vibe.attack": config.affect.vibe.attack,
            "ambient.enabled": config.ambient.enabled,
            "style.enabled": config.style.enabled,
        }

    async def reload(self) -> str:
        """Reload config/soul/role; reject changes that need a restart."""
        from .config import ConfigError, load_config

        reload_catalogs()
        loaded = load_config(
            self.config.paths.config, documents=self.documents, require_key=False
        )
        before = self._restart_tokens(self.config)
        after = self._restart_tokens(loaded)
        changed = [key for key in before if before[key] != after[key]]
        if changed:
            raise ConfigError("restart required: " + ", ".join(changed))
        old_llm = self.config.llm
        self.config = loaded
        self.soul = loaded.soul
        if self.llm is not None and loaded.llm != old_llm:
            self.llm = LLM(loaded, db=self.db)
        if self.skills is not None:
            self.skills.config = loaded.skills
        if self.style is not None:
            self.style.config = loaded.style
        if self.affect is not None:
            self.affect.config = loaded.affect
        if self.vibe is not None:
            self.vibe.config = loaded.affect.vibe
        configure = getattr(self.memory, "configure_limits", None)
        if callable(configure):
            configure(loaded.memory)
        self.approvals.configure(loaded.approval)
        self.safety = build_safety(loaded.approval)
        self.sandbox_guard = SandboxGuard(
            loaded.guard, loaded.approval.sensitive_commands, self.llm
        )
        self.privacy.configure(loaded.memory.privacy)
        if self.ambient is not None:
            self.ambient.config = loaded
            self.ambient.settings = loaded.ambient
            self.ambient.llm = self.llm
        self.runtime.paths = loaded.paths
        self.runtime.memory_status = memory_status(self.memory)
        self._sync_soul_rules()
        return "config/soul/roles reloaded"

    async def apply_settings_payload(self, payload: dict) -> str:
        from .tools.settings import apply_payload

        return await apply_payload(self, payload)

    async def close(self) -> None:
        await self.stop_reaper()
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        for gateway in self.gateways:
            with contextlib.suppress(Exception):
                await gateway.stop()
        if self.ambient is not None:
            with contextlib.suppress(Exception):
                await self.ambient.stop()
        if self.vibe is not None:
            with contextlib.suppress(Exception):
                await self.vibe.close()
        with contextlib.suppress(Exception):
            await self.memory.close()
        if self.hub is not None:
            with contextlib.suppress(Exception):
                await self.hub.stop()
        with contextlib.suppress(Exception):
            self.db.dispose()
