"""Research trigger, search, and citation verification for one turn (ADR-0043)."""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .. import io
from ..i18n import t
from ..registry import allows
from ..tools.web_search import collect
from .prompts import CORE_PARTS, system_prompt
from .research import build_card, retry_prompt, trigger_reason, verify
from .research import render as render_research

if TYPE_CHECKING:
    from .runtime import TurnRuntime

log = logging.getLogger(__name__)

class ResearchFlow:
    """The research card and the citation gate, split out of `TurnRunner.run`."""

    runtime: TurnRuntime
    _challenged: Callable[..., Any]

    async def _research_context(
        self, message, thread_id: str, tool_ctx
    ) -> tuple[str, Any, str]:
        """Trigger the search when the turn needs it and render the policy block."""
        runtime = self.runtime
        challenged = await io.run(self._challenged, message, thread_id)
        trigger = trigger_reason(
            message.text,
            language=runtime.soul.language,
            mode=runtime.config.research.mode,
            challenged=challenged,
        )
        research_card = next(
            (
                card
                for card in reversed(tool_ctx.research_cards)
                if card.status == "ok" and card.sources
            ),
            None,
        )
        if trigger and research_card is None:
            if allows(tool_ctx.allowed, "web_search"):
                try:
                    hits, fetch_failed = await collect(
                        tool_ctx,
                        message.text,
                        runtime.config.research.max_results,
                    )
                    tool_ctx.used.append("web_search")
                    research_card = build_card(
                        message.text,
                        hits,
                        timezone=runtime.soul.timezone,
                        reason=trigger,
                        status="failed" if fetch_failed else None,
                    )
                except Exception:
                    log.exception(
                        "research search failed chat=%s", thread_id
                    )
                    research_card = build_card(
                        message.text,
                        [],
                        timezone=runtime.soul.timezone,
                        status="failed",
                        reason=trigger,
                    )
            else:
                research_card = build_card(
                    message.text,
                    [],
                    timezone=runtime.soul.timezone,
                    status="failed",
                    reason=trigger,
                )
            research_text = render_research(research_card)
        elif runtime.config.research.mode == "off":
            research_text = (
                "Research is off. If a claim needs current evidence, say you "
                "cannot verify it; never fill the gap from the model prior."
            )
        else:
            research_text = (
                "Research policy: stable common knowledge may be answered from "
                "your prior. If you are unsure about a current, specific, local, "
                "numeric, or disputed fact, call web_search before answering. "
                "Use the returned Research card and cite [Rn]; if it has no "
                "usable source, say you cannot verify the claim."
            )

        return trigger, research_card, research_text

    async def _verify_research(
        self,
        answer: str,
        research_card: Any,
        trigger: str,
        tool_ctx,
        roles,
        prompt_context,
        thread_id: str,
        deadline: float,
        min_call: float,
    ) -> str:
        """Verify the answer against the card and retry once with the failure reason."""
        runtime = self.runtime
        card = research_card
        if card is None and tool_ctx.research_cards:
            card = tool_ctx.research_cards[-1]
        if card is not None and (trigger or tool_ctx.research_cards):
            ok, failure = verify(
                card,
                answer,
                require_citation=runtime.config.research.require_citation,
                freshness_hours=runtime.config.research.freshness_hours,
            )
            log.info(
                "research chat=%s trigger=%s status=%s verify=%s",
                thread_id,
                trigger or "tool",
                card.status,
                failure,
            )
            if not ok:
                system = system_prompt(
                    runtime.config,
                    roles,
                    context=prompt_context,
                    parts=(*CORE_PARTS, *runtime.plugin_parts),
                )
                retry = await runtime.llm.complete(  # pyright: ignore[reportOptionalMemberAccess]
                    str(system),
                    retry_prompt(card, answer, failure),
                    max_tokens=800,
                    session=f"monoagens-research-{thread_id}",
                    deadline=deadline,
                    min_call_seconds=min_call,
                    purpose="research",
                )
                ok, failure = verify(
                    card,
                    retry,
                    require_citation=runtime.config.research.require_citation,
                    freshness_hours=runtime.config.research.freshness_hours,
                )
                log.info(
                    "research retry chat=%s verify=%s", thread_id, failure
                )
                answer = retry if ok else t(
                    "agent.research_abstain", runtime.soul.language
                )

        return answer
