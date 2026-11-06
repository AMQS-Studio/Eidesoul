"""The turn state machine (ADR-0013, ADR-0037 §4.3): claim, decide, draft, review, send.

One turn per call, checkpointed by step name; a resumed turn starts from `turns.step` and takes
the same path. The service never imports `agent.py`; it is injected with the `TurnRuntime`, the
`TurnCoordinator`, the `Delivery`, and the `TurnSteps` bookkeeping.
"""


import asyncio
import contextlib
import logging
import random
from dataclasses import asdict, replace

from .. import guard, io
from ..i18n import t
from ..inbound import CONSENT, Action, Address, GatewayCore, Percept, prompt_section
from ..memory import MemoryContext
from ..runtime import ACTIVE_GENERATION
from .approval_flow import ApprovalFlow
from .confirmations import with_tool_result
from .context import (
    Message,
    build_user_content,
    fallback_content,
    keyword_set,
    prompt_entry,
)
from .decision import Appraisal, Decision, RelationshipDelta, decide_turn, decision_from_row
from .delivery import bind_origin, reset_origin
from .progress import _progress_callbacks
from .prompts import CORE_PARTS, CRISIS_SAFETY, PromptContext, away_block, system_prompt
from .reply import plan_chunks
from .research_flow import ResearchFlow
from .review import DECISION_LINE_CHARS, TurnReview
from .state import TurnState, actor_of
from .steps import SideEffectAlreadyRan

log = logging.getLogger(__name__)













class TurnRunner(ApprovalFlow, ResearchFlow, TurnReview):
    """The turn state machine: claim, decide, draft, review, send."""

    def __init__(self, runtime, coordinator, delivery, steps) -> None:
        super().__init__(runtime, coordinator, delivery)
        self.steps = steps

    # --------------------------------------------------------- approvals



    # ------------------------------------------------------------ state

    async def _checkpoint(self, state: TurnState, step: str, **fields) -> None:
        """Persist one checkpoint and record it in the turn's own state."""
        await io.run(self.runtime.turns.checkpoint, state.turn_id, step, **fields)
        state.checkpoints[step] = self.runtime.clock()

    async def _learn_style(self, state: TurnState, reply: str) -> None:
        """Feed a delivered reply to the style profile; a failure never fails the turn."""
        style = self.runtime.style
        if style is None or not self.runtime.config.soul.voice.style.enabled:
            return
        text = str(reply or "").strip()
        if not text:
            return
        try:
            await io.run(
                style.learn,
                text,
                thread_id=state.thread_id,
                person_id=state.sender_id,
            )
        except Exception:
            log.warning("style: failed to learn turn=%s", state.turn_id, exc_info=True)

    # -------------------------------------------------------------- run

    async def run(
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
        runtime = self.runtime
        actions: list[Action] = []
        message = inbound.payload
        thread_id = inbound.thread
        running = asyncio.current_task()
        min_call = float(runtime.config.turns.min_call_seconds)
        lease = runtime.lease()
        generation = int(getattr(lease, "generation", 0) or 0)
        lease_seconds = max(60.0, float(runtime.config.turns.timeout_seconds) + 30.0)
        queue = (
            contextlib.nullcontext() if is_command else self.coordinator.thread_lock(thread_id)
        )
        state = TurnState(
            percept=inbound,
            gateway=gateway,
            turn_id=turn_id,
            thread_id=thread_id,
            sender_id=actor_of(inbound),
            role_policy=runtime.role_policy(inbound),
            started_at=runtime.clock(),
            attachments=message.attachments,
        )
        async with queue:
            if not is_command and running is not None:
                self.coordinator.register(thread_id, running, gateway, active=True)
            claimed = claimed or await io.run(
                runtime.turns.claim,
                turn_id,
                owner=getattr(lease, "instance_id", "") or "local",
                generation=generation,
                lease_seconds=lease_seconds,
                boot_epoch=runtime.boot_epoch,
            )
            if claimed is None:
                log.info("turn %s is terminal or owned elsewhere", turn_id)
                if not is_command and running is not None:
                    self.coordinator.release(thread_id, running, active=True)
                return actions
            if runtime.draining():
                await self.coordinator.interrupt(turn_id, "shutdown")
                if not is_command and running is not None:
                    self.coordinator.release(thread_id, running, active=True)
                return actions
            token = ACTIVE_GENERATION.set(generation)
            origin_token = bind_origin(
                Address(str(getattr(gateway, "name", "") or ""), str(thread_id))
            )
            try:
                step = str(claimed["step"] or "received")
                state.checkpoints[step] = state.started_at
                attempt = int(claimed["attempt"] or 0)
                if attempt > int(runtime.config.turns.max_attempts):
                    await self.coordinator.interrupt(
                        turn_id,
                        "resume abandoned: attempts exhausted",
                    )
                    await self.delivery.notice_interrupted(turn_id, thread_id, gateway)
                    return actions
                if step in {"received", "decided"} and await io.run(
                    runtime.turns.side_effect_done, turn_id
                ):
                    await self.coordinator.interrupt(
                        turn_id,
                        "non-idempotent action already executed",
                    )
                    await self.delivery.notice_interrupted(turn_id, thread_id, gateway)
                    return actions
                skip_decision = step in {"decided", "drafted", "reviewed", "sending"}
                skip_draft = step in {"drafted", "reviewed", "sending"}
                skip_review = step in {"reviewed", "sending"}
                resuming = step != "received"
                resume_reply = str(
                    claimed.get("reviewed_text") or claimed.get("draft_text") or ""
                )

                text = message.text
                crisis = guard.is_crisis(text, runtime.soul.language)
                roles = state.role_policy
                if not is_command and await runtime.privacy.handle_pending(inbound, gateway):
                    await self.coordinator.finish(turn_id)
                    return actions
                if not is_command and not crisis and runtime.llm is None:
                    await self.coordinator.finish(turn_id)
                    return actions
                approval_block = ""
                if not is_command and not resuming:
                    approval_block = await self._resume_approvals(state)
                head_word, _, rest = body[1:].partition(" ") if is_command else ("", "", "")
                name = head_word.lower()
                direct = (
                    bool(inbound.hints.get("dm"))
                    or bool(inbound.hints.get("mention"))
                    or bool(inbound.hints.get("reply"))
                )

                missed = (
                    runtime.backlog.pop(thread_id, 0)
                    if not is_command and not resuming
                    else 0
                )
                away = away_block(missed)
                vibe = ""
                if (
                    runtime.vibe is not None
                    and not is_command
                    and not crisis
                    and not resuming
                    and text.strip()
                ):
                    try:
                        vibe = await runtime.vibe.observe(thread_id, text)
                    except Exception:
                        log.exception("failed to update vibe chat=%s", thread_id)
                emotion_block = ""
                emotion = None
                if not is_command and runtime.affect is not None and not resuming:
                    try:
                        # ADR-0017 §4.1: affect reads/writes are synchronous store calls.
                        emotion = await io.run(
                            runtime.affect.turn_start, thread_id, message.id
                        )
                    except Exception:
                        log.exception("failed to open the emotion turn chat=%s", thread_id)
                    try:
                        emotion_block = await io.run(
                            runtime.affect.prompt, thread_id, inbound.actor
                        )
                    except Exception:
                        log.exception("failed to read the emotion state chat=%s", thread_id)

                called = bool(inbound.hints.get("mention")) or bool(
                    inbound.hints.get("reply")
                )
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
                    if emotion is not None and runtime.affect is not None:
                        try:
                            people = await io.run(
                                runtime.affect.known_people, thread_id, 20
                            )
                            annoyance = next(
                                (
                                    float(row["annoyance"])
                                    for row in people
                                    if row["person_id"] == inbound.actor
                                ),
                                0.0,
                            )
                        except Exception:
                            log.debug(
                                "failed to read annoyance chat=%s", thread_id, exc_info=True
                            )
                    # ADR-0017 §4.1: the transcript comes from the synchronous archive.
                    transcript = await io.run(self.decision_transcript, thread_id)
                    decision = await decide_turn(
                        runtime.llm,
                        thread_id=thread_id,
                        chat_name=gateway.chat_name(thread_id),
                        current=prompt_entry(message, runtime.soul.name, DECISION_LINE_CHARS),
                        transcript=transcript,
                        emotion=emotion,
                        relationship=emotion_block,
                        direct=bool(inbound.hints.get("dm")),
                        mentioned=bool(inbound.hints.get("mention")),
                        replied=bool(inbound.hints.get("reply")),
                        away=away,
                        vibe=vibe,
                        soul=runtime.soul,
                        feelings=emotion is not None,
                        deadline=deadline,
                        min_call_seconds=min_call,
                        annoyance=annoyance,
                    )

                if not is_command and runtime.affect is not None and not resuming:
                    try:
                        await io.run(
                            runtime.affect.turn_end,
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
                            "turn ignored chat=%s addressed=%s reason=%s",
                            thread_id,
                            decision.addressed,
                            decision.reason,
                        )
                        await self.coordinator.finish(turn_id)
                        return actions
                    if decision is not None and decision.act == "react":
                        label = await self.delivery.sticker(
                            thread_id, message.id, gateway=gateway
                        )
                        if label:
                            actions.append(
                                Action(
                                    address=Address(gateway.name, thread_id),
                                    capability="send_sticker",
                                    payload=label,
                                    approval_class=CONSENT,
                                    id=f"turn:{turn_id}:sticker",
                                )
                            )
                        await self.coordinator.finish(turn_id)
                        return actions

                if not is_command and not skip_decision:
                    await self._checkpoint(
                        state,
                        "decided",
                        decision_json=asdict(decision) if decision is not None else None,
                    )
                await self.delivery.typing(gateway, thread_id, True)
                typing = asyncio.create_task(self.keep_typing(thread_id, gateway))
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
                    messages: list[Message] = []
                    tool_ctx = None
                    if skip_draft:
                        reply = resume_reply
                    elif is_command:
                        reply = await runtime.command(
                            name,
                            rest.strip(),
                            thread_id,
                            state.sender_id,
                            roles,
                            gateway,
                            inbound,
                        )
                    else:
                        if decision is None or runtime.llm is None:  # pragma: no cover
                            raise RuntimeError("a reply turn needs a decision and a model")
                        specs = runtime.specs(roles, gateway)
                        mood = None
                        if emotion is not None:
                            mood = (
                                (float(emotion["pleasure"]) + 1.0) / 2.0,
                                float(emotion["arousal"]),
                            )
                        memory_block = ""
                        style_block = topic_block = person_style = ""
                        if runtime.memory is not None:
                            try:
                                memory_ctx = await runtime.memory.context(
                                    person_id=inbound.actor,
                                    thread_id=thread_id,
                                    query=message.text,
                                    direct=bool(inbound.hints.get("dm")),
                                    mood=mood,
                                    max_items=runtime.config.memory.max_items,
                                    max_chars=runtime.config.memory.max_chars,
                                )
                                memory_block = memory_ctx.text
                            except Exception:
                                log.exception("failed to read memory chat=%s", thread_id)
                        actions_block = ""
                        if runtime.ledger is not None:
                            try:
                                actions_block = await io.run(
                                    runtime.ledger.actions_context, tz=runtime.soul.timezone
                                )
                            except Exception:
                                log.exception(
                                    "failed to read the action ledger chat=%s", thread_id
                                )
                        skill_block = ""
                        # A transport hint is attacker-controlled; only the gateway's declared
                        # app may scope skills and tool context (ADR-0053 §4.1.4).
                        app = ""
                        if runtime.skills is not None:
                            try:
                                app = runtime.skills.declared_app(
                                    gateway.name, str(inbound.hints.get("app") or "")
                                )
                            except Exception:
                                log.exception("failed to resolve the declared app")
                        if runtime.skills is not None:
                            try:
                                skill_block = await io.run(
                                    runtime.skills.retrieve,
                                    message.text,
                                    thread_id=thread_id,
                                    person_id=inbound.actor,
                                    held_tools=tuple(spec.name for spec in specs),
                                    catalog_tools=runtime.tool_index.names(gateway.name),
                                    max_chars=int(runtime.config.skills.max_chars),
                                    app=app,
                                )
                            except Exception:
                                log.exception("failed to retrieve procedures")
                        if runtime.affect is not None:
                            try:
                                topic_block = await io.run(
                                    runtime.affect.topic_block, thread_id
                                )
                            except Exception:
                                log.exception(
                                    "failed to read the chat topic chat=%s", thread_id
                                )
                        if runtime.style is not None:
                            try:
                                style_block = await io.run(
                                    runtime.style.profile, thread_id
                                )
                                person_style = await io.run(
                                    runtime.style.person_profile, inbound.actor
                                )
                            except Exception:
                                log.debug(
                                    "style: failed to read the chat profile chat=%s",
                                    thread_id,
                                    exc_info=True,
                                )
                        messages = await io.run(self._thread_view, thread_id, message)
                        for entry in messages:
                            with contextlib.suppress(Exception):
                                await self.delivery.prepare_media(gateway, thread_id, entry)
                        try:
                            content = await build_user_content(
                                runtime.config,
                                thread_id,
                                messages,
                                message,
                                chat_name=gateway.chat_name(thread_id),
                            )
                        except Exception:
                            log.exception(
                                "failed to assemble the payload chat=%s", thread_id
                            )
                            content = fallback_content(runtime.config, message)
                        content = with_tool_result(content, approval_block)
                        tool_ctx = runtime.tool_context(
                            thread_id,
                            state.sender_id,
                            roles,
                            gateway,
                            request=message.text,
                            person_id=inbound.actor,
                            direct=bool(inbound.hints.get("dm")),
                            app=app,
                            turn_id=turn_id,
                        )
                        trigger, research_card, research_text = (
                            await self._research_context(message, thread_id, tool_ctx)
                        )
                        bonuses: dict[str, float] = {}
                        if runtime.affect is not None:
                            try:
                                people = await io.run(
                                    runtime.affect.known_people, thread_id, 20
                                )
                                person_row = next(
                                    (
                                        row
                                        for row in people
                                        if row["person_id"] == inbound.actor
                                    ),
                                    None,
                                )
                                pending_topic = await io.run(
                                    runtime.affect.pending, thread_id
                                )
                                bonuses = {
                                    "topic": 0.3 if pending_topic > 0 else 0.0,
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
                            and keyword_set(message.text, runtime.soul.language)
                            & keyword_set(memory_block, runtime.soul.language)
                            else 0.0
                        )
                        prompt_context = PromptContext(
                            config=runtime.config,
                            roles=roles,
                            runtime=runtime.facts,
                            query=message.text,
                            thread_id=thread_id,
                            person_id=inbound.actor,
                            direct=bool(inbound.hints.get("dm")),
                            body_section=prompt_section(gateway),
                            language=runtime.soul.language,
                            style=style_block,
                            vibe=vibe,
                            topic=topic_block,
                            away=away,
                            emotion=emotion_block,
                            memory=memory_block,
                            actions=actions_block,
                            skills=skill_block,
                            tools=runtime.tool_index.registry(gateway.name),
                            commands=runtime.commands,
                            bodies=runtime.tool_index.other_bodies(gateway.name),
                            sender=message.sender_name,
                            mode=decision.mode,
                            tone=decision.tone,
                            decision_reason=decision.reason,
                            safety=safety,
                            research=research_text,
                            research_trigger=trigger,
                            person_style=person_style,
                            bonuses=bonuses,
                        )
                        answer = await runtime.llm.run(
                            system_prompt(
                                runtime.config,
                                roles,
                                context=prompt_context,
                                parts=(*CORE_PARTS, *runtime.plugin_parts),
                            ),
                            content,
                            specs,
                            tool_ctx,
                            session=f"monoagens-{thread_id}",
                            turn_id=turn_id,
                            deadline=deadline,
                            min_call_seconds=min_call,
                            on_step=self.steps.recorder(turn_id, "drafted"),
                            lookup_step=self.steps.lookup(turn_id),
                            **_progress_callbacks(
                                runtime.dispatch, gateway, thread_id, turn_id
                            ),
                            temperature=(
                                runtime.soul.voice.sampling.professional_temperature
                                if decision is not None and decision.mode == "professional"
                                else runtime.soul.voice.sampling.daily_temperature
                            ),
                            sampling=runtime.soul.voice.sampling,
                        )
                        answer = await self._verify_research(
                            answer,
                            research_card,
                            trigger,
                            tool_ctx,
                            roles,
                            prompt_context,
                            thread_id,
                            deadline,
                            min_call,
                        )
                        reply = (
                            answer
                            if answer.strip() or not tool_ctx.sent
                            else t("agent.sent_ack", runtime.soul.language)
                        )
                        state.draft = reply or ""
                        await self._checkpoint(state, "drafted", draft_text=state.draft)

                    if not is_command and not skip_review and reply is not None:
                        if skip_draft:
                            # Resumed from "drafted": rebuild the recent lines polish reads.
                            messages = await io.run(self._thread_view, thread_id, message)
                        state.memories = [
                            *memory_ctx.hits,
                            *(tool_ctx.memory_hits if tool_ctx else ()),
                        ]
                        reply = await self._review(
                            inbound,
                            reply,
                            gateway,
                            hits=state.memories,
                            cross_reads=tool_ctx.cross_reads if tool_ctx else (),
                            decision=decision,
                            crisis=crisis,
                            sent=bool(tool_ctx.sent) if tool_ctx else False,
                            messages=messages,
                            deadline=deadline,
                            min_call=min_call,
                        )
                        if reply is None:
                            await self.coordinator.finish(turn_id)
                            return actions
                        reply = await self._approval_badge(
                            reply, thread_id, state.sender_id, state.started_at
                        )
                        state.draft = reply or ""
                        await self._checkpoint(state, "reviewed", reviewed_text=state.draft)

                    professional = bool(
                        decision is not None and decision.mode == "professional"
                    )
                    humanize = replace(
                        runtime.soul.voice.humanize,
                        enabled=bool(runtime.soul.voice.humanize.enabled),
                    )
                    chunks, pace, quote_all = plan_chunks(
                        reply,
                        gateway=gateway,
                        language=runtime.soul.language,
                        is_command=is_command,
                        professional=professional,
                        humanize=humanize,
                    )
                    planned_chunks = int(claimed["planned_chunks"] or 0)
                    if resume_reply and planned_chunks and len(chunks) != planned_chunks:
                        # The stored chunk plan no longer matches: sending would duplicate.
                        await self.coordinator.interrupt(
                            turn_id,
                            "resume mismatch: chunk plan changed",
                        )
                        await self.delivery.notice_interrupted(turn_id, thread_id, gateway)
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
                        partial=str(runtime.config.turns.partial_delivery or "skip"),
                        humanize=humanize,
                    )
                    actions.extend(sent_actions)
                    if stop.startswith("refused:"):
                        await self.coordinator.interrupt(
                            turn_id,
                            f"send refused: {stop.split(':', 1)[1]}",
                        )
                        await self.delivery.notice_interrupted(turn_id, thread_id, gateway)
                        return actions
                    if stop.startswith("failed"):
                        await io.run(
                            runtime.turns.defer,
                            turn_id,
                            "send failed",
                            delay_seconds=min(
                                max(0.1, float(runtime.config.llm.retry.cap_seconds)),
                                max(
                                    float(runtime.config.worker.retry.backoff_seconds),
                                    float(runtime.config.llm.retry.base_seconds),
                                )
                                * (2 ** max(0, attempt - 1))
                                * (
                                    1.0
                                    + random.uniform(
                                        0.0, float(runtime.config.llm.retry.jitter)
                                    )
                                ),
                            ),
                        )
                    else:
                        touch = getattr(runtime.memory, "record_uses", None)
                        if touch is not None and memory_ctx.hits:
                            try:
                                await io.run(
                                    touch, turn_id, [hit.id for hit in memory_ctx.hits]
                                )
                            except Exception:
                                log.exception(
                                    "failed to record memory use chat=%s", thread_id
                                )
                        await self.coordinator.finish(turn_id, planned=len(chunks))
                        if not is_command and reply is not None:
                            await self._learn_style(state, reply)
                    runtime.facts.pending_approvals = await io.run(
                        runtime.approvals.count_pending
                    )
                    await io.run(runtime.approvals.expire)
                finally:
                    typing.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await typing
                    await self.delivery.typing(gateway, thread_id, False)
            except asyncio.CancelledError:
                reason = self.coordinator.reason(turn_id)
                await self.coordinator.interrupt(turn_id, reason)
                raise
            except SideEffectAlreadyRan as exc:
                log.warning("turn %s interrupted: %s", turn_id, exc)
                await self.coordinator.interrupt(turn_id, str(exc))
                await self.delivery.notice_interrupted(turn_id, thread_id, gateway)
                return actions
            finally:
                ACTIVE_GENERATION.reset(token)
                reset_origin(origin_token)
                if not is_command and running is not None:
                    self.coordinator.release(thread_id, running, active=True)
        return actions
