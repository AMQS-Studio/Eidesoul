"""Review pipeline and payload helpers for one turn (ADR-0003 §4.2).

`polish -> privacy.review -> cross_chat_leak` runs on a fresh draft and again on a draft
restored from the `"drafted"` checkpoint, so the `"reviewed"` checkpoint never certifies a
reply nobody inspected. The service never imports `agent.py`; its dependencies are the injected
`TurnRuntime`, `TurnCoordinator`, and `Delivery`.
"""


import asyncio
import logging
from collections.abc import Sequence

from .. import guard
from ..character.character import polish
from ..i18n import t
from ..inbound import GatewayCore, Percept
from ..memory import MemoryHit
from . import assembly
from .context import Message, prompt_entry
from .decision import Decision
from .research import trigger_reason

log = logging.getLogger(__name__)

TYPING_REFRESH = 8
DECISION_HISTORY = 30
DECISION_FALLBACK_HISTORY = 20
DECISION_LINE_CHARS = 500


class TurnReview:
    """Review chain plus the transcript helpers the runner calls on every turn."""

    def __init__(self, runtime, coordinator, delivery) -> None:
        self.runtime = runtime
        self.coordinator = coordinator
        self.delivery = delivery

    def _thread_view(self, thread_id: str, message: Message) -> list[Message]:
        """The payload rows for this turn; also the recent lines a resumed review reads."""
        return assembly.thread(
            self.runtime.history,
            thread_id,
            message,
            max_chars=self.runtime.config.prompt.thread_chars,
            config=self.runtime.config,
            window=self.runtime.windows.get(thread_id),
        )

    async def _review(
        self,
        inbound: Percept,
        reply: str,
        gateway: GatewayCore,
        *,
        hits: Sequence[MemoryHit],
        cross_reads: Sequence[str],
        decision: Decision | None,
        crisis: bool,
        sent: bool,
        messages: list[Message],
        deadline: float,
        min_call: float,
    ) -> str | None:
        """`polish -> privacy.review -> cross_chat_leak` on one draft (ADR-0003 §4.2).

        Returns the text to persist as `reviewed_text`, or `None` when the privacy review handled
        the turn (an approval question was sent). A turn resumed from `"drafted"` runs this on the
        stored draft again, so the `"reviewed"` checkpoint never certifies an unseen reply.
        """
        recent = "\n".join(
            f"{entry.sender_name}: {entry.text}"
            for entry in messages[-6:]
            if entry.text.strip()
        )
        daily = decision is None or decision.mode != "professional"
        if daily and not crisis and not sent and reply and self.runtime.llm is not None:
            before = reply
            reply = await polish(
                self.runtime.llm,
                self.runtime.config,
                reply,
                latest=inbound.payload.text,
                recent=recent,
                direct=bool(inbound.hints.get("dm")),
                tone=decision.tone if decision is not None else "neutral",
                session=f"monoagens-character-{inbound.thread}",
                deadline=deadline,
                min_call_seconds=min_call,
            )
            log.info(
                "character chat=%s status=%s",
                inbound.thread,
                "rewrite" if reply != before else "pass",
            )
            if not reply.strip():
                reply = before
        review = await self.runtime.privacy.review(inbound, reply, list(hits), gateway)
        if review.handled:
            return None
        reply = review.reply
        if reply and guard.cross_chat_leak(reply, list(cross_reads)):
            log.warning("cross-chat verbatim reply blocked chat=%s", inbound.thread)
            reply = t("agent.cross_chat_blocked", self.runtime.soul.language)
        return reply

    def _challenged(self, message, thread_id: str) -> bool:
        """A quote is a challenge only when it answers a bot claim that needed evidence.

        A quoted notice (timeout, interruption, shutdown) is not an answer, and an answer
        without a current claim has nothing to verify. The cue itself is checked in
        `trigger_reason`, so a bare quote never forces a search.
        """
        quote = message.quote
        if quote is None or quote.sender_name != self.runtime.soul.name:
            return False
        quoted = self.runtime.history.find(thread_id, quote.id)
        if quoted is not None and quoted.notice:
            return False
        return bool(
            trigger_reason(
                quoted.text if quoted is not None else quote.text,
                language=self.runtime.soul.language,
                mode=self.runtime.config.research.mode,
            )
        )

    async def keep_typing(self, thread_id: str, gateway: GatewayCore) -> None:
        while True:
            await asyncio.sleep(TYPING_REFRESH)
            await self.delivery.typing(gateway, thread_id, True)

    def decision_transcript(self, thread_id: str) -> str:
        entries: list[Message] = []
        try:
            entries = list(self.runtime.history.recent(thread_id, DECISION_HISTORY))
        except Exception:  # a broken archive must not fail the decision
            log.debug(
                "failed to read the archive for the decision chat=%s", thread_id, exc_info=True
            )
        if not entries:
            entries = self.runtime.windows.get(thread_id)[-DECISION_FALLBACK_HISTORY:]
        lines = [
            prompt_entry(entry, self.runtime.soul.name, DECISION_LINE_CHARS) for entry in entries
        ]
        return "\n".join(line for line in lines if line)
