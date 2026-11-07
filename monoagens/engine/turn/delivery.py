"""The one outbound path: text, file, sticker, typing, notices, and the shutdown drain.

ADR-0003 §4.4, ADR-0004 §4.4, ADR-0007 §4.3: every send goes through the door
(`Agent.dispatch`), is ledgered before it leaves, and never retries on its own. The service never
imports `agent.py`; it is injected with the `TurnRuntime` and the `TurnCoordinator`.
"""


import asyncio
import contextlib
import logging
import random
import time
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

from .. import clock
from ..i18n import t
from ..inbound import (
    Action,
    Address,
    GatewayCore,
    GatewayError,
    Percept,
    Receipt,
    Rich,
    SendRefused,
    StandbyError,
    tool_action_id,
)
from ..runtime import ACTIVE_GENERATION
from .context import AUTHOR_BOT, TEXT_EXT, Message, clean
from .reply import split_text

log = logging.getLogger(__name__)

MAX_TEXT = 10_000
FILE_TEXT_SIZE = 512 * 1024
FILE_TEXT_CHARS = 4000

# ADR-0004 §4.2: a delivery names the room that asked for it. The turn binds the room it was
# asked in once; every delivery inside it passes that address to the door explicitly.
TURN_ORIGIN: ContextVar[Address | None] = ContextVar("monoagens_turn_origin", default=None)


def bind_origin(address: Address):
    """Bind the origin room for one turn; returns the token `reset_origin` consumes."""
    return TURN_ORIGIN.set(address)


def reset_origin(token) -> None:
    TURN_ORIGIN.reset(token)


def origin_for(origin: Address | None = None) -> Address:
    """The explicit origin or the bound turn origin; the target room is never an origin.

    AGENTS §5: the door must be able to run its cross-room check, so a delivery without a
    stated origin is a programming error, not a send to the target.
    """
    found = origin or TURN_ORIGIN.get()
    if found is None:
        raise ValueError("explicit origin address is required")
    return found


def typing_delay(text: str, config, rng=random) -> float:
    """Plausible typing delay (seconds) from the bubble length; 0 when the delay is disabled."""
    if float(config.min_delay) == 0 and float(config.max_delay) == 0:
        return 0.0
    seconds = len(str(text or "")) / max(1.0, float(config.cpms))
    bounded = max(float(config.min_delay), min(float(config.max_delay), seconds))
    return bounded + rng.uniform(0, 0.4)


def file_fallback_text(path: Path, caption: str, language: str) -> str:
    """What a body without `Rich` delivers instead of the file (ADR-0004 §4.2)."""
    label = t(
        "agent.file_fallback",
        language,
        name=path.name,
        caption=f" — {caption}" if caption else "",
    )
    if path.suffix.lower() not in TEXT_EXT:
        return label
    try:
        if not path.is_file() or path.stat().st_size > FILE_TEXT_SIZE:
            return label
        body = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return label
    return f"{label}\n{body[:FILE_TEXT_CHARS]}" if body else label


class Delivery:
    """One chat path for every caller: text, file, sticker, typing, notices, and the turn."""

    def __init__(self, runtime, coordinator) -> None:
        self.runtime = runtime
        self.coordinator = coordinator
        self._shutdown_gateways: dict[str, GatewayCore] = {}

    @property
    def shutdown_gateways(self) -> dict[str, GatewayCore]:
        """The bodies a drain captured, for the shutdown notices of the same epoch."""
        return self._shutdown_gateways

    def remember(self, thread_id: str, text: str, *, gateway, message_id: str) -> None:
        """The bot's own message joins the window: the next turn reads what it just said."""
        self.runtime.windows.append(
            thread_id,
            Message(
                id=message_id,
                sender_name=self.runtime.soul.name,
                sender_id=self._self_sender_id(gateway),
                when=clock.now(self.runtime.soul.timezone),
                text=text,
                outgoing=True,
                author=AUTHOR_BOT,
            ),
        )

    def _self_sender_id(self, gateway) -> str:
        return next(iter(sorted(gateway.self_handles())), "")

    async def _door(
        self, action: Action, *, subject: str, origin: Address, reason: str = ""
    ) -> Receipt:
        """One call to the door; the origin is never inferred here (ADR-0004 §4.2)."""
        return await self.runtime.dispatch(
            str(subject or ""),
            action.address,
            action,
            origin=origin,
            reason=str(reason or ""),
        )

    @staticmethod
    def _action_id() -> dict:
        """`{"id": …}` when a tool step is bound, so the outbox key is deterministic.

        ADR-0003 §4.3: a resumed turn that reaches the same step derives the same id, and the
        ledger then answers `sent` instead of dispatching the bubble a second time.
        """
        found = tool_action_id()
        return {"id": found} if found else {}

    @staticmethod
    def _reference(receipt: Receipt) -> str:
        """The body's reference for a delivered action, or the refusal that stopped it."""
        if receipt.state == "sent":
            return receipt.reference
        if receipt.state == "unknown":
            raise GatewayError(receipt.reason or "delivery is unknown")
        if receipt.state == "pending":
            raise SendRefused(f"needs approval ({receipt.reason})")
        if "delivery refused" in receipt.reason:
            raise StandbyError(receipt.reason)
        raise SendRefused(receipt.reason or "the send was refused")

    async def text(
        self,
        thread_id: str,
        text: str,
        *,
        gateway,
        reply: Percept | None = None,
        kind: str = "",
        subject: str = "",
        origin: Address | None = None,
        reason: str = "",
        action_id: str = "",
    ) -> str:
        address = Address(str(gateway.name), str(thread_id))
        found = action_id or str(self._action_id().get("id") or "")
        action = Action(
            address=address,
            capability="send_text",
            payload=str(text or ""),
            id=found or uuid4().hex,
        )
        receipt = await self._door(
            action, subject=subject, origin=origin_for(origin), reason=reason
        )
        message_id = self._reference(receipt)
        self.remember(thread_id, text, gateway=gateway, message_id=message_id)
        if kind:
            with contextlib.suppress(Exception):
                self.runtime.history.add_notice(thread_id, text, kind=kind, message_id=message_id)
        return message_id

    async def file(
        self,
        thread_id: str,
        path: Path,
        caption: str = "",
        *,
        gateway: GatewayCore,
        reply: Percept | None = None,
        subject: str = "",
        origin: Address | None = None,
        reason: str = "",
        action_id: str = "",
    ) -> str:
        path = Path(path)
        if not isinstance(gateway, Rich):
            self.runtime.facts.miss_rich()
            return await self.text(
                thread_id,
                file_fallback_text(path, caption, self.runtime.soul.language),
                gateway=gateway,
                reply=reply,
                origin=origin,
                reason=reason,
            )
        address = Address(str(gateway.name), str(thread_id))
        found = action_id or str(self._action_id().get("id") or "")
        action = Action(
            address=address,
            capability="send_file",
            payload={"path": str(path), "caption": str(caption or "")},
            id=found or uuid4().hex,
        )
        receipt = await self._door(
            action, subject=subject, origin=origin_for(origin), reason=reason
        )
        message_id = self._reference(receipt)
        note = t(
            "agent.file_sent",
            self.runtime.soul.language,
            name=path.name,
            caption=f" — {caption}" if caption else "",
        )
        self.runtime.windows.append(
            thread_id,
            Message(
                id=message_id,
                sender_name=self.runtime.soul.name,
                sender_id=self._self_sender_id(gateway),
                when=clock.now(self.runtime.soul.timezone),
                text=note,
                outgoing=True,
                author=AUTHOR_BOT,
            ),
        )
        return message_id

    async def sticker(
        self,
        thread_id: str,
        message_id: str,
        *,
        gateway: GatewayCore,
        subject: str = "",
        origin: Address | None = None,
        action_id: str = "",
    ) -> str | None:
        if not isinstance(gateway, Rich):
            self.runtime.facts.miss_rich()
            return None
        address = Address(str(gateway.name), str(thread_id))
        action = Action(
            address=address,
            capability="send_sticker",
            payload=str(message_id),
            id=action_id or str(self._action_id().get("id") or uuid4().hex),
        )
        label = self._reference(
            await self._door(action, subject=subject, origin=origin_for(origin))
        )
        if label:
            self.runtime.windows.append(
                thread_id,
                Message(
                    id=f"sticker-{time.time_ns()}",
                    sender_name=self.runtime.soul.name,
                    sender_id=self._self_sender_id(gateway),
                    when=clock.now(self.runtime.soul.timezone),
                    text=label,
                    outgoing=True,
                    author=AUTHOR_BOT,
                ),
            )
        return label

    async def typing(self, gateway, thread_id: str, composing: bool) -> None:
        """A body without `Rich` has no presence indicator; skip, never fail."""
        if not isinstance(gateway, Rich):
            self.runtime.facts.miss_rich()
            return
        await gateway.typing(thread_id, composing)

    async def prepare_media(self, gateway, thread_id: str, entry: Message) -> None:
        if not isinstance(gateway, Rich):
            self.runtime.facts.miss_rich()
            return
        await gateway.prepare_media(thread_id, entry)

    async def send_text(
        self,
        thread_id: str,
        text: str,
        *,
        gateway,
        subject: str = "",
        origin: Address | None = None,
        reason: str = "",
    ) -> str:
        """Tool-facing: render the gateway dialect, split, send, return ok:/error:."""
        if gateway is None:
            return "error: gateway is not available"
        body = clean(str(text or "")).strip()
        if not body:
            return "error: empty text"
        chunks = split_text(gateway.render(body), limit=MAX_TEXT)
        if not chunks:
            return "error: empty text"
        try:
            for chunk in chunks:
                await self.text(
                    thread_id,
                    chunk,
                    gateway=gateway,
                    subject=subject,
                    origin=origin,
                    reason=reason,
                )
        except StandbyError:
            return "error: delivery refused"
        except SendRefused as exc:
            return f"error: {exc}"
        except GatewayError as exc:
            return f"error: failed to send text: {exc}"
        name = gateway.chat_name(thread_id)
        suffix = f" ({len(chunks)} parts)" if len(chunks) > 1 else ""
        return f"ok: text sent to {name or thread_id}{suffix}"

    async def send_file(
        self,
        thread_id: str,
        path: Path,
        caption: str = "",
        *,
        gateway,
        subject: str = "",
        origin: Address | None = None,
        reason: str = "",
        action_id: str = "",
    ) -> str:
        if gateway is None:
            return "error: gateway is not available"
        try:
            await self.file(
                thread_id,
                Path(path),
                caption,
                gateway=gateway,
                subject=subject,
                origin=origin,
                reason=reason,
                action_id=action_id,
            )
        except StandbyError:
            return "error: delivery refused"
        except SendRefused as exc:
            return f"error: {exc}"
        except GatewayError as exc:
            return f"error: failed to send file: {exc}"
        except OSError as exc:
            return f"error: {type(exc).__name__}: {exc}"
        return f"ok: file sent: {Path(path).name}"

    async def send_reply(
        self,
        *,
        turn_id: str,
        thread_id: str,
        gateway,
        inbound: Percept,
        chunks: list[str],
        pace: bool,
        quote_all: bool,
        generation: int,
        partial: str,
        humanize,
    ) -> tuple[list[str], list[Action], str]:
        """Send the planned chunks of one turn.

        `stop` is "", "failed:<reason>", or "refused:<reason>"; the caller decides what a stop
        means for the turn, never the delivery path.
        """
        self.runtime.turns.plan_sending(turn_id, len(chunks))
        sent_ids: list[str] = []
        actions: list[Action] = []
        for index, chunk in enumerate(chunks):
            action = Action(
                address=Address(str(gateway.name), str(thread_id)),
                capability="send_text",
                payload=chunk,
                id=f"turn:{turn_id}:{index}",
            )
            state = self.runtime.outbox.status(action.id, 0) or ""
            if state == "sent":
                continue
            if state == "unknown" and partial == "skip":
                log.info("chunk %d of turn %s is unknown: skipped", index, turn_id)
                continue
            if state == "unknown":
                # A policy-allowed resend: the unknown row is cleared before the second attempt.
                self.runtime.outbox.retry(action.id, 0)
            if pace:
                await self.typing(gateway, thread_id, True)
                await asyncio.sleep(typing_delay(chunk, humanize))
            try:
                receipt = await self._door(
                    action,
                    subject=str(inbound.actor or ""),
                    origin=Address(str(gateway.name), str(inbound.thread)),
                )
            except GatewayError as exc:
                log.exception("failed to send the reply to %s", thread_id)
                return sent_ids, actions, f"failed:{exc}"
            if receipt.state == "unknown":
                return sent_ids, actions, "failed:delivery is unknown"
            if receipt.state != "sent":
                return sent_ids, actions, f"refused:{receipt.reason or receipt.state}"
            self.runtime.turns.chunk_sent(turn_id)
            self.remember(thread_id, chunk, gateway=gateway, message_id=receipt.reference)
            sent_ids.append(receipt.reference)
            actions.append(action)
            if pace and index < len(chunks) - 1:
                await asyncio.sleep(
                    max(0.0, float(humanize.between_delay)) + random.uniform(0, 0.4)
                )
        return sent_ids, actions, ""

    async def notice(
        self,
        thread_id: str,
        gateway,
        key: str,
        *,
        scope_key: str,
        kind: str,
        params: dict | None = None,
    ) -> bool:
        """One catalog notice, ledgered with its `kind`; it never marks a turn delivered."""
        runtime = self.runtime
        text = t(key, runtime.soul.language, **(params or {}))
        retry = runtime.config.turns.shutdown.notice_retry
        try:
            status = runtime.outbox.begin(
                scope_key,
                0,
                gateway=gateway.name,
                thread_id=thread_id,
                generation=ACTIVE_GENERATION.get(),
            )
        except Exception:
            log.exception("failed to open the notice ledger chat=%s kind=%s", thread_id, kind)
            runtime.facts.fail_notice("notice ledger unavailable")
            return False
        if status == "sent":
            return False
        if status == "unknown":
            # It may already be visible; a notice must never duplicate (ADR-0003 §10).
            return False
        attempts = max(1, int(retry.attempts))
        origin = Address(str(gateway.name), str(thread_id))
        for attempt in range(attempts):
            try:
                message_id = await self.text(
                    thread_id, text, gateway=gateway, kind=kind, origin=origin
                )
            except StandbyError:
                runtime.outbox.forget(scope_key, 0)
                return False
            except GatewayError as exc:
                runtime.facts.fail_notice(f"{type(exc).__name__}: {exc}")
                log.warning("notice failed chat=%s kind=%s: %s", thread_id, kind, exc)
                if attempt < attempts - 1:
                    await asyncio.sleep(
                        min(
                            max(0.0, float(retry.backoff_seconds)),
                            max(0.0, float(runtime.config.llm.retry.retry_after_max_seconds)),
                        )
                    )
                    continue
                runtime.outbox.mark_unknown(scope_key, 0)
                return False
            except Exception as exc:
                log.exception("notice send failed chat=%s kind=%s", thread_id, kind)
                runtime.facts.fail_notice(f"{type(exc).__name__}: {exc}")
                with contextlib.suppress(Exception):
                    runtime.outbox.mark_unknown(scope_key, 0)
                return False
            runtime.outbox.mark_sent(scope_key, 0, message_id)
            return True
        return False

    async def notice_timeout(self, thread_id: str, turn_id: str, gateway) -> None:
        self.runtime.facts.timeout_turn()
        await self.notice(
            thread_id,
            gateway,
            "turn.notice.timeout",
            scope_key=f"notice:{turn_id}:timeout",
            kind="notice:timeout",
        )

    async def notice_interrupted(self, turn_id: str, thread_id: str, gateway) -> None:
        if gateway is None:
            return
        await self.notice(
            thread_id,
            gateway,
            "turn.notice.interrupted",
            scope_key=f"notice:{turn_id}:interrupted",
            kind="notice:interrupted",
        )

    async def drain(self, seconds: float) -> dict[str, str]:
        """Wait for in-flight turns; return the ones still running after the budget."""
        pending: dict[str, str] = {}
        items = [
            (thread, task)
            for thread, task in self.coordinator.tasks().items()
            if not task.done()
        ]
        self._shutdown_gateways = self.coordinator.gateways()
        if not items:
            return pending
        _done, waiting = await asyncio.wait(
            [task for _, task in items], timeout=max(0.0, float(seconds))
        )
        by_task = {task: thread for thread, task in items}
        for task in waiting:
            thread = by_task[task]
            turn_id = getattr(task, "_monoagens_turn_id", "")
            if turn_id:
                self.coordinator.note(turn_id, "shutdown")
            pending[thread] = turn_id
            task.cancel()
        if waiting:
            await asyncio.gather(*waiting, return_exceptions=True)
        return pending

    async def shutdown_notices(self, pending: dict[str, str], seconds: float) -> int:
        """One `notice:shutdown` per chat with an unfinished turn, inside its own budget."""
        self.runtime.facts.phase = "noticed"
        if not pending:
            return 0
        sent = 0
        deadline = time.monotonic() + max(0.0, float(seconds))
        epoch = self.runtime.shutdown_epoch()
        for thread, turn_id in pending.items():
            if time.monotonic() >= deadline:
                break
            if sent and not bool(self.runtime.config.turns.shutdown.notice_per_chat):
                break
            gateway = self._shutdown_gateways.get(thread) or self.runtime.body_of(thread)
            if gateway is None:
                log.warning("shutdown notice skipped: no body owns chat=%s", thread)
                continue
            await self.coordinator.interrupt(turn_id, "shutdown")
            if await self.notice(
                thread,
                gateway,
                "turn.notice.shutdown",
                scope_key=f"notice:{thread}:shutdown:{epoch}",
                kind="notice:shutdown",
            ):
                sent += 1
            try:
                self.runtime.turns.stamp_shutdown(turn_id, epoch)
            except Exception:
                log.debug("failed to stamp the shutdown epoch turn=%s", turn_id, exc_info=True)
        self.runtime.facts.phase = "stopped"
        return sent
