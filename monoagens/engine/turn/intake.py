"""Intake: identity, the forget intent, and the watchdog around one turn task.

ADR-0003 §7, ADR-0003 §4.3, ADR-0007 §4.3: the body hands in a percept, the core assigns the
person id, and the turn runs under a deadline. The service never imports `agent.py`; it is
injected with the `TurnRuntime`, the `TurnCoordinator`, the runner, and the delivery path.
"""


import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import replace

from .. import io
from ..inbound import Action, GatewayCore, Percept, awaiting_address
from ..llm import DeadlineExceeded
from ..memory import intent
from ..stores import rooms
from ..stores.history import RouteConflict
from ..stores.turns import turn_identity
from .commands import command_body
from .state import sender_of

log = logging.getLogger(__name__)

TERMINAL_TURN = frozenset({"delivered", "interrupted", "expired"})


class TurnIntake:
    """The one door's intake: identity, the forget intent, the turn task, and its watchdog."""

    def __init__(self, runtime, coordinator, runner, delivery) -> None:
        self.runtime = runtime
        self.coordinator = coordinator
        self.runner = runner
        self.delivery = delivery

    def identify(self, inbound: Percept, gateway: GatewayCore) -> Percept:
        """Core-owned identity: the body hands in an address, the core assigns the id."""
        raw = (
            inbound.actor
            or inbound.payload.sender_id
            or inbound.payload.sender_address
            or sender_of(inbound)
        )
        source = inbound.source or gateway.name
        try:
            # ADR-0001 §4.1: a resume carries an existing `per_…` id; keep it as-is.
            if str(raw).startswith("per_") and self.runtime.identity.person(str(raw)) is not None:
                return replace(inbound, actor=str(raw))
            return replace(inbound, actor=self.runtime.identity.resolve(source, raw))
        except Exception:
            log.exception("identity resolve failed source=%s", source)
            return replace(inbound, actor=inbound.actor or raw)

    def observe_group(self, inbound: Percept, gateway: GatewayCore, reason: str) -> bool:
        """Archive a shared-room message the policy gate rejected; never reply to it.

        Only a mention/reply gate failure is kept: it is the room's own conversation and a
        later mention needs it. A room the role may not enter at all is not stored, and an
        observed message creates no turn and no memory candidate.
        """
        if not awaiting_address(reason):
            return False
        message = inbound.payload
        thread_id = inbound.thread
        try:
            rooms.ensure_thread(
                self.runtime.history.db,
                thread_id,
                gateway=str(getattr(gateway, "name", "") or ""),
                kind="group",
            )
            self.runtime.history.add(thread_id, message)
        except Exception:
            log.warning("group observation failed chat=%s", thread_id, exc_info=True)
            return False
        self.runtime.windows.append(thread_id, message, persist=False)
        if self.runtime.ambient is not None:
            self.runtime.ambient.note(thread_id, message)
        return True

    async def forget_intent(self, inbound: Percept, message) -> None:
        """ADR-0005 §4.6: a "forget that …" turn removes the one entry it can mean.

        The deterministic veto+forget path runs before the turn, so the ordinary reply can
        confirm it. Ambiguity deletes nothing and the turn continues unchanged.
        """
        settings = getattr(self.runtime.config.memory, "intent", None)
        kind = await io.run(
            intent.classify,
            str(message.text or ""),
            self.runtime.soul.language,
            settings,
        )
        if kind != "forget":
            return
        forget = getattr(self.runtime.memory, "forget_by_intent", None)
        if not isinstance(forget, Callable):
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

    async def receive(
        self,
        inbound: Percept,
        gateway: GatewayCore,
        *,
        claimed: dict | None = None,
    ) -> list[Action]:
        """Intake + watchdog: the one door every body calls (ADR-0003 §7, ADR-0003 §4.3)."""
        message = inbound.payload
        thread_id = inbound.thread
        if message.outgoing or bool(inbound.hints.get("self")):
            self.runtime.windows.append(thread_id, message)
            if self.runtime.ambient is not None:
                self.runtime.ambient.note(thread_id, message)
            return []
        if not self.runtime.accepting():
            # A standby instance refuses intake and sends nothing (ADR-0003 §4.4).
            self.runtime.facts.refuse_standby()
            log.info(
                "intake refused chat=%s mode=%s draining=%s",
                thread_id,
                self.runtime.mode(),
                self.runtime.draining(),
            )
            return []
        inbound = self.identify(inbound, gateway)
        message_id = str(message.id or "")
        if not message_id:
            log.warning("message without an id cannot become a turn chat=%s", thread_id)
            return []
        # ADR-0001 §4.3: the turn identity is namespaced, so two bodies never share a turn key.
        turn_id = turn_identity(gateway.name, thread_id, message_id)
        try:
            self.runtime.history.intake(
                thread_id,
                message,
                inbound.actor,
                language=str(self.runtime.soul.language),
                audience="private" if bool(inbound.hints.get("dm")) else "group",
                # ADR-0003 §11: the body marks a single-action turn; the backlog never folds it.
                category="action" if inbound.hints.get("action") is True else "chat",
                gateway=getattr(gateway, "name", "") or "",
                app=str(inbound.hints.get("app") or ""),
                hints=dict(inbound.hints),
            )
        except RouteConflict as conflict:
            log.warning(
                "route conflict thread=%s owner=%s claimant=%s",
                conflict.thread,
                conflict.owner,
                conflict.claimant,
            )
            return []
        except Exception:
            log.exception(
                "intake failed chat=%s id=%s: the turn is not processed", thread_id, turn_id
            )
            raise
        self.runtime.windows.append(thread_id, message, persist=False)
        if self.runtime.ambient is not None:
            self.runtime.ambient.note(thread_id, message)
        await self.forget_intent(inbound, message)
        existing = self.runtime.turns.get(turn_id)
        if existing is not None and str(existing["status"]) in TERMINAL_TURN:
            log.info("turn %s already %s; no repeat", turn_id, existing["status"])
            return []
        age = time.time() - message.when.timestamp()
        backlog = float(getattr(gateway, "backlog_seconds", 0.0) or 0.0)
        if claimed is None and backlog > 0 and age > backlog:
            self.runtime.backlog[thread_id] = self.runtime.backlog.get(thread_id, 0) + 1
            try:
                self.runtime.turns.expire(turn_id, "backlog")
            except Exception:
                log.exception("failed to expire the backlog turn %s", turn_id)
            log.info("old message chat=%s age=%.0fs: archive only", thread_id, age)
            return []

        text = message.text
        body = command_body(text, self.runtime.soul.name, gateway.self_handles())
        is_command = body.startswith("/")
        timeout = float(self.runtime.config.turns.timeout_seconds)
        deadline = time.monotonic() + timeout
        task = asyncio.create_task(
            self.runner.run(
                inbound,
                gateway,
                turn_id,
                deadline=deadline,
                is_command=is_command,
                body=body,
                claimed=claimed,
            )
        )
        setattr(task, "_monoagens_turn_id", turn_id)  # noqa: B010 — a dynamic task attribute
        if not is_command:
            self.coordinator.register(thread_id, task, gateway, active=False)
        try:
            return await asyncio.wait_for(task, timeout=timeout)
        except TimeoutError:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
            await self.coordinator.interrupt(turn_id, "timeout")
            if not is_command:
                await self.delivery.notice_timeout(thread_id, turn_id, gateway)
            return []
        except DeadlineExceeded:
            await self.coordinator.interrupt(turn_id, "timeout")
            if not is_command:
                await self.delivery.notice_timeout(thread_id, turn_id, gateway)
            return []
        except asyncio.CancelledError:
            current = asyncio.current_task()
            external = current is not None and current.cancelling()
            if task.cancelled() and not external:
                reason = self.coordinator.reason(turn_id)
                await self.coordinator.interrupt(turn_id, reason)
                return []
            self.coordinator.note(turn_id, "cancelled")
            task.cancel()
            raise
        finally:
            if not is_command:
                self.coordinator.release(thread_id, task, active=False)
                self.coordinator.forget(turn_id)
