"""The one outbound path: text, file, sticker, typing, notices, and the shutdown drain.

ADR-0017 §4.4, ADR-0032 §4.4, ADR-0037 §4.3: every send goes through the door
(`Agent.dispatch`), is ledgered before it leaves, and never retries on its own. The service never
imports `agent.py`; the host is the `Agent` it is bound to.
"""


import asyncio
import contextlib
import logging
import random
import time
from datetime import datetime
from pathlib import Path

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
)
from ..runtime import ACTIVE_GENERATION
from .context import AUTHOR_BOT, TEXT_EXT, Message, clean
from .reply import split_text

log = logging.getLogger(__name__)

MAX_TEXT = 10_000
FILE_TEXT_SIZE = 512 * 1024
FILE_TEXT_CHARS = 4000


def typing_delay(text: str, config, rng=random) -> float:
    """Plausible typing delay (seconds) from the bubble length; 0 when the delay is disabled."""
    if float(config.min_delay) == 0 and float(config.max_delay) == 0:
        return 0.0
    seconds = len(str(text or "")) / max(1.0, float(config.cpms))
    bounded = max(float(config.min_delay), min(float(config.max_delay), seconds))
    return bounded + rng.uniform(0, 0.4)


def file_fallback_text(path: Path, caption: str, language: str) -> str:
    """What a body without `Rich` delivers instead of the file (ADR-0018 §4.2)."""
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

    def __init__(self, agent) -> None:
        self.agent = agent

    def __getattr__(self, name: str):
        """The host is the `Agent`: turn state and policy come from it, never the reverse."""
        return getattr(self.agent, name)

    def remember(self, thread_id: str, text: str, *, gateway, message_id: str) -> None:
        """The bot's own message joins the window: the next turn reads what it just said."""
        self.agent.windows.append(
            thread_id,
            Message(
                id=message_id,
                sender_name=self.agent.soul.name,
                sender_id=self._self_sender_id(gateway),
                when=datetime.now(),
                text=text,
                outgoing=True,
                author=AUTHOR_BOT,
            ),
        )

    def _self_sender_id(self, gateway) -> str:
        return next(iter(sorted(gateway.self_handles())), "")

    async def _door(
        self, action: Action, *, subject: str, origin: Address | None
    ) -> Receipt:
        """One call to the door; the caller maps the receipt, never retries on its own."""
        return await self.agent.dispatch(
            str(subject or ""),
            action.address,
            action,
            origin=origin or action.address,
        )

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
    ) -> str:
        address = Address(str(gateway.name), str(thread_id))
        action = Action(
            address=address, capability="send_text", payload=str(text or "")
        )
        receipt = await self._door(action, subject=subject, origin=origin)
        message_id = self._reference(receipt)
        self.remember(thread_id, text, gateway=gateway, message_id=message_id)
        if kind:
            with contextlib.suppress(Exception):
                self.agent.history.add_notice(thread_id, text, kind=kind, message_id=message_id)
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
    ) -> str:
        path = Path(path)
        if not isinstance(gateway, Rich):
            self.agent.runtime.miss_rich()
            return await self.text(
                thread_id,
                file_fallback_text(path, caption, self.agent.soul.language),
                gateway=gateway,
                reply=reply,
            )
        address = Address(str(gateway.name), str(thread_id))
        action = Action(
            address=address,
            capability="send_file",
            payload={"path": str(path), "caption": str(caption or "")},
        )
        receipt = await self._door(action, subject=subject, origin=origin)
        message_id = self._reference(receipt)
        note = f"(file terkirim: {path.name}{f' — {caption}' if caption else ''})"
        self.agent.windows.append(
            thread_id,
            Message(
                id=message_id,
                sender_name=self.agent.soul.name,
                sender_id=self._self_sender_id(gateway),
                when=datetime.now(),
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
            self.agent.runtime.miss_rich()
            return None
        address = Address(str(gateway.name), str(thread_id))
        action = Action(
            address=address,
            capability="send_sticker",
            payload=str(message_id),
            **({"id": action_id} if action_id else {}),
        )
        label = self._reference(await self._door(action, subject=subject, origin=origin))
        if label:
            self.agent.windows.append(
                thread_id,
                Message(
                    id=f"sticker-{time.time_ns()}",
                    sender_name=self.agent.soul.name,
                    sender_id=self._self_sender_id(gateway),
                    when=datetime.now(),
                    text=label,
                    outgoing=True,
                    author=AUTHOR_BOT,
                ),
            )
        return label

    async def typing(self, gateway, thread_id: str, composing: bool) -> None:
        """A body without `Rich` has no presence indicator; skip, never fail."""
        if not isinstance(gateway, Rich):
            self.agent.runtime.miss_rich()
            return
        await gateway.typing(thread_id, composing)

    async def prepare_media(self, gateway, thread_id: str, entry: Message) -> None:
        if not isinstance(gateway, Rich):
            self.agent.runtime.miss_rich()
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
                    thread_id, chunk, gateway=gateway, subject=subject, origin=origin
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
    ) -> str:
        if gateway is None:
            return "error: gateway is not available"
        try:
            await self.file(
                thread_id, Path(path), caption, gateway=gateway, subject=subject, origin=origin
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
        self.agent.turns.plan_sending(turn_id, len(chunks))
        sent_ids: list[str] = []
        actions: list[Action] = []
        for index, chunk in enumerate(chunks):
            action = Action(
                address=Address(str(gateway.name), str(thread_id)),
                capability="send_text",
                payload=chunk,
                id=f"turn:{turn_id}:{index}",
            )
            state = self.agent.outbox.status(action.id, 0) or ""
            if state == "sent":
                continue
            if state == "unknown" and partial == "skip":
                log.info("chunk %d of turn %s is unknown: skipped", index, turn_id)
                continue
            if state == "unknown":
                # A policy-allowed resend: the unknown row is cleared before the second attempt.
                self.agent.outbox.retry(action.id, 0)
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
            self.agent.turns.chunk_sent(turn_id)
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
        agent = self.agent
        text = t(key, agent.soul.language, **(params or {}))
        retry = agent.config.turns.shutdown.notice_retry
        try:
            status = agent.outbox.begin(
                scope_key,
                0,
                gateway=gateway.name,
                thread_id=thread_id,
                generation=ACTIVE_GENERATION.get(),
            )
        except Exception:
            log.exception("failed to open the notice ledger chat=%s kind=%s", thread_id, kind)
            agent.runtime.fail_notice("notice ledger unavailable")
            return False
        if status == "sent":
            return False
        if status == "unknown":
            # It may already be visible; a notice must never duplicate (ADR-0013 §10).
            return False
        attempts = max(1, int(retry.attempts))
        for attempt in range(attempts):
            try:
                message_id = await self.text(thread_id, text, gateway=gateway, kind=kind)
            except StandbyError:
                agent.outbox.forget(scope_key, 0)
                return False
            except GatewayError as exc:
                agent.runtime.fail_notice(f"{type(exc).__name__}: {exc}")
                log.warning("notice failed chat=%s kind=%s: %s", thread_id, kind, exc)
                if attempt < attempts - 1:
                    await asyncio.sleep(
                        min(
                            max(0.0, float(retry.backoff_seconds)),
                            max(0.0, float(agent.config.llm.retry.retry_after_max_seconds)),
                        )
                    )
                    continue
                agent.outbox.mark_unknown(scope_key, 0)
                return False
            except Exception as exc:
                log.exception("notice send failed chat=%s kind=%s", thread_id, kind)
                agent.runtime.fail_notice(f"{type(exc).__name__}: {exc}")
                with contextlib.suppress(Exception):
                    agent.outbox.mark_unknown(scope_key, 0)
                return False
            agent.outbox.mark_sent(scope_key, 0, message_id)
            return True
        return False

    async def notice_timeout(self, thread_id: str, turn_id: str, gateway) -> None:
        self.agent.runtime.timeout_turn()
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
            gateway = self._shutdown_gateways.get(thread) or self.body_of(thread)
            if gateway is None:
                log.warning("shutdown notice skipped: no body owns chat=%s", thread)
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
