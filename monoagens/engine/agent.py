"""Agent: the handle, the door, and the turn phases wired behind one facade.

ADR-0037 §4.3: the identity, the body/address lookup, the tool and command contexts, the
lifecycle, and the public `handle`/`dispatch` signatures stay here; the turn phases live in
`turn/loop.py`, `turn/intake.py`, `turn/delivery.py`, `turn/commands.py`, and `turn/steps.py`.
"""


import asyncio
import contextlib
import logging
import time
from collections import OrderedDict, deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path

from . import io, plugins
from .access.approvals import ApprovalStore
from .access.identity import IdentityStore
from .access.permissions import tool_patterns, tools_for
from .access.privacy import PrivacyService
from .access.roles import Role
from .commands import REGISTRY as CORE_COMMANDS
from .commands import CommandContext
from .config import Config
from .guard import SafetyPolicy, SandboxGuard, build_safety
from .guard.policy import ActionRequest, approval_summary, scope_proof
from .i18n import reload_catalogs
from .inbound import (
    AUTHORIZATION,
    CONSENT,
    Action,
    Address,
    GatewayCore,
    GatewayError,
    Percept,
    Receipt,
    StandbyError,
    action_json,
    body_private_thread,
    refusal,
)
from .jobs import supervisor
from .llm import LLM
from .memory import MemoryBackend, memory_status
from .memory import evidence as privacy_evidence
from .memory.ledger import OutboundLedger
from .registry import Handler, Registry
from .runtime import ACTIVE_GENERATION, RuntimeFacts
from .stores.turns import TurnStore
from .tools import REGISTRY as CORE_TOOLS
from .tools import ToolContext, ToolIndex
from .turn import observability
from .turn.commands import TurnCommands
from .turn.context import Message, WorkingMemory
from .turn.delivery import Delivery
from .turn.intake import TurnIntake
from .turn.loop import TurnRunner
from .turn.steps import TurnSteps

log = logging.getLogger(__name__)


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
        self.turn_runner = TurnRunner(self)
        self.intake = TurnIntake(self)
        self.steps = TurnSteps(self)
        self.command_dispatch = TurnCommands(self)
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
        self.plugin_parts = self.plugins.prompts
        self.tools = Registry([*CORE_TOOLS.by_name.values(), *self.plugins.tools], "tool")
        self.body_tools = {str(item.name): list(item.tools()) for item in self.gateways}
        self.tool_index = ToolIndex(self.tools, self.body_tools)
        handlers = [*CORE_COMMANDS.by_name.values(), *gateway_commands, *self.plugins.commands]
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
        if config.memory.privacy.enabled:
            privacy_evidence.verify_entities()
        self.privacy = PrivacyService(self)

    # ------------------------------------------------------------- util

    def _sync_soul_rules(self) -> str:
        """ADR-0008 §4.3: mirror the soul's rules and promote the ones that earned it.

        Best effort: a store without the port (a fake, a read-only backend) or a DB failure
        logs one line and never blocks the boot.
        """
        rules = tuple(getattr(self.soul, "rules", ()) or ())
        sync = getattr(self.memory, "sync_soul_rules", None)
        promote: Callable[..., int] | None = getattr(self.memory, "promote_soul_rules", None)
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
        turn_id = getattr(task, "_monoagens_turn_id", "")
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

    def private_thread(self, person_id: str) -> str:
        """Ask the bodies for the one private thread a person holds (ADR-0033 §4.5)."""
        return body_private_thread(self.gateways, person_id)

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
        return await self.delivery.drain(seconds)

    async def shutdown_notices(self, pending: dict[str, str], seconds: float) -> int:
        """One `notice:shutdown` per chat with an unfinished turn, inside its own budget."""
        return await self.delivery.shutdown_notices(pending, seconds)

    def body(self, name: str) -> GatewayCore | None:
        """The body instance behind a name, or None (a name the config no longer enables)."""
        wanted = str(name or "")
        return next((item for item in self.gateways if item.name == wanted), None)

    def body_of(self, thread_id: str) -> GatewayCore | None:
        """The body that owns a thread, or None when no route and no turn map claims it."""
        try:
            return self.body(self.address_of(thread_id).gateway)
        except GatewayError:
            return None

    def address_of(self, thread_id: str) -> Address:
        """The body that owns a thread: the turn map, then the durable route (ADR-0032 §4.1).

        Membership is proven separately at the door (`refusal`); this only resolves the owner.
        A thread nobody owns is a refusal, never a fallback to the first registered body.
        """
        found = self._turn_gateways.get(thread_id)
        name = str(getattr(found, "name", "") or "")
        if not name:
            lookup = getattr(self.history, "gateway_of", None)
            if callable(lookup):
                try:
                    name = str(lookup(str(thread_id)) or "")
                except Exception:
                    log.debug("route lookup failed thread=%s", thread_id, exc_info=True)
                    name = ""
        if not name:
            raise GatewayError(f"no body owns thread '{thread_id}'")
        return Address(name, str(thread_id))

    def _send_gate_or_receipt(self, action: Action) -> Receipt | None:
        """The primary gate, as a refusal instead of an exception (`_send_gate` counts it)."""
        try:
            self._send_gate()
        except StandbyError:
            return Receipt(action.id, "refused", reason="delivery refused")
        return None
    async def dispatch(
        self,
        subject: str,
        address: Address,
        action: Action,
        *,
        origin: Address,
        reason: str = "",
    ) -> Receipt:
        """The one door to a body: policy, then ledger, then `dispatch` (ADR-0032 §4.4)."""
        body = self.body(address.gateway)
        if body is None:
            return Receipt(
                action.id, "refused", reason=f"unknown body '{address.gateway}'"
            )
        same = (
            address.gateway == origin.gateway and str(address.thread) == str(origin.thread)
        )
        approval_class = CONSENT if same else AUTHORIZATION
        action = replace(action, approval_class=approval_class)
        if not same:
            requester = str(subject or "").strip()
            if not requester:
                return Receipt(
                    action.id, "refused", reason="no person id; a cross-body act is refused"
                )
            denial = refusal(
                body,
                address.thread,
                requester,
                recorded=self.history.rooms_of(requester),
            )
            if denial:
                return Receipt(action.id, "refused", reason=denial)
            if not str(reason or "").strip():
                return Receipt(
                    action.id, "refused", reason="cross_send needs a reason from the requester"
                )
        request = ActionRequest(
            tool="send",
            args={
                "chat": str(address.thread),
                "capability": action.capability,
                "action_id": action.id,
            },
            thread_id=str(address.thread),
            sender=str(subject or ""),
            gateway=str(address.gateway),
            origin_gateway=str(origin.gateway),
            roles=tuple(role.name for role in self.config.roles.for_person(subject)),
            workspace=str(self.config.workspace),
            roots=(),
        )
        decision = self.safety.check(request)
        if decision.verdict == "pending":
            row = self.approvals.find_action(action.id)
            if row is not None:
                if row.status == "approved":
                    pass
                elif row.status == "pending":
                    return Receipt(action.id, "pending", reason=f"approval #{row.id}")
                else:
                    return Receipt(
                        action.id, "refused", reason=f"approval #{row.id} {row.status}"
                    )
            else:
                proof = scope_proof(request, extra=self.config.guard.credential_patterns)
                row = await self.approvals.create(
                    kind="send",
                    thread_id=str(address.thread),
                    sender=str(subject or ""),
                    gateway=str(address.gateway),
                    tool="",
                    args={
                        "action": action_json(action),
                        "origin": asdict(origin),
                        "reason": str(reason or ""),
                    },
                    payload={"action_id": action.id},
                    summary=approval_summary("send", request, proof),
                    scope_proof=proof,
                    approval_class=approval_class,
                )
                return Receipt(action.id, "pending", reason=f"approval #{row.id}")
        elif decision.verdict == "deny":
            return Receipt(action.id, "refused", reason=str(decision.reason or "denied"))

        receipt = self._send_gate_or_receipt(action)
        if receipt is not None:
            return receipt
        scope_key = action.id
        try:
            state = self.outbox.begin(
                scope_key,
                0,
                gateway=str(address.gateway),
                thread_id=str(address.thread),
                generation=ACTIVE_GENERATION.get(),
            )
        except Exception:
            log.exception("failed to open the outbound ledger action=%s", action.id)
            state = "new"
        if state == "sent":
            return Receipt(action.id, "sent", self.outbox.reference(scope_key, 0))
        if state == "unknown":
            return Receipt(action.id, "unknown")
        try:
            reference = str(await body.dispatch(action) or "")
        except GatewayError as exc:
            self.outbox.mark_unknown(scope_key, 0)
            return Receipt(action.id, "unknown", reason=str(exc))
        except StandbyError:
            self.outbox.forget(scope_key, 0)
            return Receipt(action.id, "refused", reason="delivery refused")
        self.outbox.mark_sent(scope_key, 0, reference)
        return Receipt(action.id, "sent", reference)

    async def recover(self) -> int:
        """Recovery pass on the primary (ADR-0011 §4.5, ADR-0013 §8)."""
        return await supervisor.recover(self)

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
        """ADR-0036 §4.4: identity selects roles; a body may never name one."""
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
        app: str = "",
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
            app=str(app or "").strip().lower(),
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
        return self.command_dispatch.context(thread_id, sender, roles, gateway, args, inbound)

    def _specs(self, roles: tuple[Role, ...], gateway: GatewayCore | None = None) -> list[Handler]:
        registry = self.tool_index.registry(gateway.name if gateway is not None else "")
        specs = list(tools_for(registry, roles))
        if self.hub is not None:
            specs += self.hub.specs(roles)
        return specs

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

    async def send_text(
        self,
        thread_id: str,
        text: str,
        *,
        gateway: GatewayCore | None,
        subject: str = "",
    ) -> str:
        return await self.delivery.send_text(
            thread_id, text, gateway=gateway, subject=subject
        )

    async def send_file(
        self,
        thread_id: str,
        path: Path,
        caption: str = "",
        *,
        gateway: GatewayCore | None,
        subject: str = "",
    ) -> str:
        return await self.delivery.send_file(
            thread_id, path, caption, gateway=gateway, subject=subject
        )

    # ------------------------------------------------------------- turn

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
            return await self.intake.receive(inbound, gateway, claimed=claimed)

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
        return await self.turn_runner.run(
            inbound,
            gateway,
            turn_id,
            deadline=deadline,
            is_command=is_command,
            body=body,
            claimed=claimed,
        )

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
        return await self.command_dispatch.run(
            name, args, thread_id, sender, roles, gateway, inbound
        )

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
            "voice.enabled": config.soul.voice.enabled,
            "voice.affect.enabled": config.soul.voice.affect.enabled,
            "voice.style.enabled": config.soul.voice.style.enabled,
            "voice.sampling": config.soul.voice.sampling,
            "voice.safety": config.soul.voice.safety,
            "ambient.enabled": config.ambient.enabled,
        }

    async def reload(self) -> str:
        """Reload config/soul/role; reject changes that need a restart."""
        from .config import ConfigError, load_config

        reload_catalogs()
        loaded = load_config(self.config.paths.config, documents=self.documents, require_key=False)
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
            self.style.config = loaded.soul.voice.style
        if self.affect is not None:
            self.affect.config = loaded.soul.voice.affect
        if self.vibe is not None:
            self.vibe.config = loaded.soul.voice.affect.vibe
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
