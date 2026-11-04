"""Intake: identity, the forget intent, and the watchdog around one turn task.

ADR-0013 §7, ADR-0017 §4.3, ADR-0037 §4.3: the body hands in a percept, the core assigns the
person id, and the turn runs under a deadline. The service never imports `agent.py`; the host is
the `Agent` it is bound to.
"""


import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import replace

from .. import io
from ..inbound import Action, GatewayCore, Percept
from ..llm import DeadlineExceeded
from ..memory import intent
from ..stores.history import RouteConflict
from .commands import command_body

log = logging.getLogger(__name__)

TERMINAL_TURN = frozenset({"delivered", "interrupted", "expired"})


class TurnIntake:
    """The one door's intake: identity, the forget intent, the turn task, and its watchdog."""

    def __init__(self, agent) -> None:
        self.agent = agent

    def __getattr__(self, name: str):
        return getattr(self.agent, name)

    def identify(self, inbound: Percept, gateway: GatewayCore) -> Percept:
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

    async def forget_intent(self, inbound: Percept, message) -> None:
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
        inbound = self.identify(inbound, gateway)
        turn_id = str(message.id or "")
        if not turn_id:
            log.warning("message without an id cannot become a turn chat=%s", thread_id)
            return []
        try:
            self.history.intake(
                thread_id,
                message,
                inbound.actor,
                language=str(self.soul.language),
                audience="private" if bool(inbound.hints.get("dm")) else "group",
                # ADR-0013 §11: the body marks a single-action turn; the backlog never folds it.
                category="action" if inbound.hints.get("action") is True else "chat",
                gateway=getattr(gateway, "name", "") or "",
                app=str(inbound.hints.get("app") or ""),
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
        self.windows.append(thread_id, message, persist=False)
        if self.ambient is not None:
            self.ambient.note(thread_id, message)
        await self.forget_intent(inbound, message)
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
        setattr(task, "_eidesoul_turn_id", turn_id)  # noqa: B010 — a dynamic task attribute
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

