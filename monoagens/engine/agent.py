"""Agent: the handle, the door, and the turn phases wired behind one facade.

ADR-0007 §4.3: the identity, the body/address lookup, the tool and command contexts, the
lifecycle, and the public `handle`/`dispatch` signatures stay here; the turn phases live in
`turn/loop.py`, `turn/intake.py`, `turn/delivery.py`, `turn/commands.py`, and `turn/steps.py`.
"""

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace
from functools import partial

from . import io, plugins
from .access.approvals import ApprovalStore
from .access.identity import IdentityStore
from .access.permissions import command_allowed, tool_patterns, tools_for
from .access.privacy import PrivacyService
from .access.roles import Role
from .agent_delivery import DeliveryFacade
from .agent_guard import CrossRoomGuard
from .commands import REGISTRY as CORE_COMMANDS
from .commands import CommandContext
from .commands.approve import run_approval
from .config import Config
from .guard import SafetyDecision, SafetyPolicy, SandboxGuard, build_safety
from .guard.policy import (
    ActionRequest,
    approval_summary,
    scope_granted,
    scope_proof,
)
from .i18n import t
from .inbound import (
    AUTHORIZATION,
    CONSENT,
    Action,
    Address,
    Directory,
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
from .observability import AlertManager, notify_admin
from .observability import install as install_alerts
from .registry import Handler, Registry
from .runtime import ACTIVE_GENERATION, RuntimeFacts
from .stores.turns import TurnStore, turn_identity
from .tools import REGISTRY as CORE_TOOLS
from .tools import ToolContext, ToolIndex
from .turn import observability
from .turn.commands import TurnCommands, command_body
from .turn.context import WorkingMemory
from .turn.coordinator import TurnCoordinator
from .turn.delivery import Delivery
from .turn.intake import TurnIntake
from .turn.loop import TurnRunner
from .turn.runtime import TurnRuntime
from .turn.steps import TurnSteps

log = logging.getLogger(__name__)


class Agent(DeliveryFacade, CrossRoomGuard):
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
        self.alerts = AlertManager(config, notify=partial(notify_admin, self))
        install_alerts(self.alerts)
        self.windows = windows
        self.history = history
        self.memory = memory
        # `note.md` §10 §6: the store asks this model before it stores a claim about access.
        bind_judge = getattr(memory, "bind_judge", None)
        if callable(bind_judge):
            bind_judge(llm)
        self.ledger = ledger
        self.skills = skills
        self.recipes = recipes
        self.affect = affect
        self.style = style
        self.ambient = ambient
        self.vibe = vibe
        self.gateways = tuple(gateways)
        self.documents = documents
        self.identity = identity or IdentityStore(db, roles=self.config.roles)
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
        self.backlog: dict[str, int] = {}
        self._resume_times: deque[float] = deque()
        self.draining = False
        self.reload_generation = 0
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
        self.body_tools = self._body_tools()
        self.tool_index = ToolIndex(self.tools, self.body_tools)
        handlers = [*CORE_COMMANDS.by_name.values(), *gateway_commands, *self.plugins.commands]
        self.commands = Registry(handlers, "command")
        # ADR-0008 §4.3: the soul rules are synced by the primary start path only, so building
        # a standby Agent writes no rows (Supervisor.activate calls `_sync_soul_rules`).
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
        # ADR-0007 §4.3: the turn pipeline reads one typed runtime, and the coordinator owns
        # the per-thread locks and the in-flight task registry. The Agent stays the facade.
        self.turn_runtime = TurnRuntime(
            _live=self,
            history=self.history,
            turns=self.turns,
            memory=self.memory,
            skills=self.skills,
            recipes=self.recipes,
            affect=self.affect,
            style=self.style,
            approvals=self.approvals,
            outbox=self.outbox,
            windows=self.windows,
            ledger=self.ledger,
            identity=self.identity,
            privacy=self.privacy,
            tool_index=self.tool_index,
            commands=self.commands,
            vibe=self.vibe,
            ambient=self.ambient,
            plugin_parts=tuple(self.plugin_parts),
            facts=self.runtime,
            backlog=self.backlog,
            boot_epoch=self.boot_epoch,
            dispatch=self.dispatch,
            body_of=self.body_of,
            lease=lambda: self.lease,
            clock=time.time,
            mode=lambda: self.mode,
            draining=lambda: self.draining,
            accepting=self._accepting,
            shutdown_epoch=lambda: self.shutdown_epoch,
            role_policy=self._role_set,
            tool_context=self.tool_context,
            specs=self._specs,
            command=self._command,
            approve=partial(run_approval, self),
        )
        self.coordinator = TurnCoordinator(self.turn_runtime)
        self.delivery = Delivery(self.turn_runtime, self.coordinator)
        self.steps = TurnSteps(self.turns, self.tools)
        self.turn_runner = TurnRunner(
            self.turn_runtime, self.coordinator, self.delivery, self.steps
        )
        self.intake = TurnIntake(
            self.turn_runtime, self.coordinator, self.turn_runner, self.delivery
        )
        self.command_dispatch = TurnCommands(self)

    # ------------------------------------------------------------- util

    def _body_tools(self) -> dict[str, list[Handler]]:
        """Each body's own tools plus the transport aliases it still answers to.

        An alias is a copy of the canonical core handler under the body's old name, so the
        engine never carries transport vocabulary while an older prompt keeps resolving.
        """
        mapping = {str(item.name): list(item.tools()) for item in self.gateways}
        for gateway in self.gateways:
            aliases = getattr(gateway, "tool_aliases", None)
            if not callable(aliases):
                continue
            table = aliases()
            if not isinstance(table, Mapping):
                continue
            for alias, canonical in table.items():
                spec = self.tools.by_name.get(str(canonical))
                if spec is None or alias in self.tools.by_name:
                    continue
                mapping.setdefault(str(gateway.name), []).append(
                    replace(spec, name=str(alias), aliases=())
                )
        return mapping

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
                promoted = int(promote(tz=self.soul.timezone) or 0)
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
        """Primary gate before every transport call (ADR-0003 §4.4, ADR-0002 §4.3)."""
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

    # ----------------------------------------------------------- turns

    async def cancel_turn(self, thread_id: str, *, reason: str = "user_stop") -> bool:
        """Cancel the task running in this thread only (ADR-0003 §7.1)."""
        return await self.coordinator.interrupt_thread(thread_id, reason=reason)

    @property
    def _turn_gateways(self) -> dict[str, GatewayCore]:
        """The live body map of in-flight turns; the coordinator owns the writes."""
        return self.coordinator.gateway_map

    @property
    def _shutdown_gateways(self) -> dict[str, GatewayCore]:
        """The bodies a drain captured for the shutdown notices of the same epoch."""
        return self.delivery.shutdown_gateways

    def private_thread(self, person_id: str) -> str:
        """Ask the bodies for the one private thread a person holds (ADR-0001 §4.5)."""
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

    # --------------------------------------------------------- shutdown

    def begin_drain(self) -> int:
        """Enter `draining`; intake stops before the drain clock starts (ADR-0001 §4.4)."""
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
        """The body that owns a thread: the turn map, then the durable route (ADR-0004 §4.1).

        Membership is proven separately at the door (`refusal`); this only resolves the owner.
        A thread nobody owns is a refusal, never a fallback to the first registered body.
        """
        found = self.coordinator.gateway_of(thread_id)
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
        """The one door to a body: policy, then ledger, then `dispatch` (ADR-0004 §4.4)."""
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
            blocked = await self._cross_room_leak(action, origin)
            if blocked:
                return Receipt(action.id, "refused", reason=blocked)
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
            declared_class=action.approval_class,
            door=True,
        )
        decision = self.safety.check(request)
        if decision.verdict == "pending" and scope_granted(request):
            # The person just confirmed this exact target; the door does not ask again.
            decision = SafetyDecision("allow", "the approved scope covers this action")
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
                    action_id=action.id,
                    origin_gateway=str(origin.gateway),
                    origin_thread=str(origin.thread),
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
        """Recovery pass on the primary (ADR-0001 §4.5, ADR-0003 §8)."""
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
        """ADR-0006 §4.4: identity selects roles; a body may never name one."""
        return self.config.roles.for_person(
            inbound.actor,
            room=f"{inbound.source or ''}:{inbound.thread}",
            participants=self._room_participants,
        )

    def _room_participants(self, gateway: str, thread: str) -> set[str] | None:
        """The body's own proof of who is in a room; `None` when it cannot answer."""
        body = self.body(gateway)
        if not isinstance(body, Directory):
            return None
        return body.participants(thread)

    @staticmethod
    def _sender(inbound: Percept) -> str:
        payload = inbound.payload
        return payload.sender_id or payload.sender_address or inbound.thread

    @staticmethod
    def _actor(inbound: Percept) -> str:
        """The canonical person id; approvals and memory key on it, never a transport id."""
        return str(inbound.actor or Agent._sender(inbound))

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
        turn_id: str = "",
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
            turn_id=turn_id,
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

    # ------------------------------------------------------------- turn

    async def handle(
        self,
        inbound: Percept,
        gateway: GatewayCore,
        *,
        claimed: dict | None = None,
    ) -> list[Action]:
        """Intake + watchdog with the turn's correlation ids bound (ADR-0003 §4.2)."""
        message_id = str(getattr(inbound.payload, "id", "") or "")
        token = (
            turn_identity(
                getattr(gateway, "name", "") or "", inbound.thread, message_id
            )
            if message_id
            else ""
        )
        with observability.bind(
            turn_id=token,
            thread_id=inbound.thread,
            gateway=getattr(gateway, "name", "") or "",
        ):
            if await self._access_refused(inbound, gateway, claimed=claimed):
                return []
            return await self.intake.receive(inbound, gateway, claimed=claimed)

    def _command_permitted(self, inbound: Percept, gateway: GatewayCore) -> bool:
        """A command the sender's role may run skips the gateway's conversation policy."""
        body = command_body(
            str(getattr(inbound.payload, "text", "") or ""),
            self.soul.name,
            gateway.self_handles(),
        )
        if not body.startswith("/"):
            return False
        name = body[1:].partition(" ")[0].strip().lower()
        if not name:
            return False
        try:
            person = self.identity.resolve(
                inbound.source or gateway.name,
                inbound.actor or self._sender(inbound),
            )
        except Exception:
            log.exception(
                "identity resolve failed source=%s", inbound.source or gateway.name
            )
            return False
        roles = self.config.roles.for_person(
            person,
            room=f"{inbound.source or gateway.name}:{inbound.thread}",
            participants=self._room_participants,
        )
        handler = self.commands.get(name)
        if handler is not None and command_allowed(roles, handler.name):
            return True
        recipe = self.recipes.get(name) if self.recipes is not None else None
        return bool(recipe is not None and {role.name for role in roles} & set(recipe.roles))

    async def _access_refused(
        self,
        inbound: Percept,
        gateway: GatewayCore,
        *,
        claimed: dict | None,
    ) -> bool:
        """One refusal for every body: the gateway decides, the core sends the same reply."""
        check = getattr(gateway, "access_refusal", None)
        if claimed is not None or not callable(check):
            return False
        message = inbound.payload
        if getattr(message, "outgoing", False) or bool(inbound.hints.get("self")):
            return False
        if self._command_permitted(inbound, gateway):
            return False
        try:
            reason = str(check(inbound) or "")
        except Exception:
            log.exception(
                "access check failed gateway=%s chat=%s; refusing",
                getattr(gateway, "name", ""),
                inbound.thread,
            )
            reason = "check_failed"
        if not reason:
            return False
        if not bool(inbound.hints.get("dm")):
            # A shared room never hears a refusal; the gate keeps its conversation silent.
            self.intake.observe_group(inbound, gateway, reason)
            return True
        try:
            await self.delivery.text(
                inbound.thread,
                t("agent.access_refused", self.soul.language),
                gateway=gateway,
                origin=Address(str(getattr(gateway, "name", "") or ""), str(inbound.thread)),
            )
        except Exception:
            log.warning(
                "access refusal notice failed gateway=%s chat=%s",
                getattr(gateway, "name", ""),
                inbound.thread,
                exc_info=True,
            )
        return True

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

    async def reload(self) -> str:
        """ADR-0002 §4.9: validate the three files, swap the hot runtime, report the rest."""
        from .turn.reload import reload_runtime

        return await reload_runtime(self)

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
