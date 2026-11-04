"""The turn state machine (ADR-0013, ADR-0037 §4.3): claim, decide, draft, review, send.

One turn per call, checkpointed by step name; a resumed turn starts from `turns.step` and takes
the same path. The service never imports `agent.py`; the host is the `Agent` it is bound to.
"""


import asyncio
import contextlib
import logging
import random
from dataclasses import asdict, replace

from .. import guard, io
from ..character.character import effective_limits, polish, regulate
from ..character.knowledge import select_facts
from ..character.patterns import load_lexicon, room_register, select_patterns
from ..i18n import t
from ..inbound import CONSENT, Action, Address, GatewayCore, Percept, prompt_section
from ..memory import MemoryContext
from ..runtime import ACTIVE_GENERATION
from . import assembly
from .context import (
    Message,
    build_user_content,
    fallback_content,
    keyword_set,
    prompt_entry,
)
from .decision import Appraisal, Decision, RelationshipDelta, decide_turn, decision_from_row
from .prompts import CORE_PARTS, CRISIS_SAFETY, PromptContext, away_block, system_prompt
from .reply import plan_chunks
from .steps import SideEffectAlreadyRan

log = logging.getLogger(__name__)

TYPING_REFRESH = 8
DECISION_HISTORY = 30
DECISION_FALLBACK_HISTORY = 20
DECISION_LINE_CHARS = 500


class TurnRunner:
    """The turn state machine: claim, decide, draft, review, checkpoint, send."""

    def __init__(self, agent) -> None:
        self.agent = agent

    def __getattr__(self, name: str):
        return getattr(self.agent, name)

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
        actions: list[Action] = []
        message = inbound.payload
        thread_id = inbound.thread
        min_call = float(self.config.turns.min_call_seconds)
        generation = int(getattr(self.lease, "generation", 0) or 0)
        lease_seconds = max(60.0, float(self.config.turns.timeout_seconds) + 30.0)
        queue = contextlib.nullcontext() if is_command else self._thread_lock(thread_id)
        async with queue:
            if not is_command and (running := asyncio.current_task()) is not None:
                self._turn_tasks[thread_id] = running
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
                crisis = guard.is_crisis(text, self.soul.language)
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
                        transcript=self.decision_transcript(thread_id),
                        emotion=emotion,
                        relationship=emotion_block,
                        direct=bool(inbound.hints.get("dm")),
                        mentioned=bool(inbound.hints.get("mention")),
                        replied=bool(inbound.hints.get("reply")),
                        away=away,
                        vibe=vibe,
                        soul=self.soul, feelings=emotion is not None,
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
                                    address=Address(gateway.name, thread_id),
                                    capability="send_sticker",
                                    payload=label,
                                    approval_class=CONSENT,
                                    id=f"turn:{turn_id}:sticker",
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
                        if decision is None or self.llm is None:  # pragma: no cover
                            raise RuntimeError("a reply turn needs a decision and a model")
                        specs = self._specs(roles, gateway)
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
                                actions_block = self.ledger.actions_context(tz=self.soul.timezone)
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
                                    catalog_tools=self.tool_index.names(gateway.name),
                                    max_chars=int(self.config.skills.max_chars),
                                    app=str(inbound.hints.get("app") or ""),
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
                        limits = effective_limits(self.config.character)
                        style = self.soul.style
                        lexicon = load_lexicon(style.patterns)
                        register = room_register(
                            [entry.text for entry in messages[-6:] if entry.text.strip()],
                            lexicon,
                        )
                        patterns = select_patterns(
                            style.max_patterns,
                            register=register,
                            limits=limits,
                            language=self.soul.language,
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
                            app=str(inbound.hints.get("app") or ""),
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
                        prompt_context = PromptContext(
                            config=self.config,
                            roles=roles,
                            runtime=self.runtime,
                            query=message.text,
                            thread_id=thread_id,
                            person_id=inbound.actor,
                            direct=bool(inbound.hints.get("dm")),
                            body_section=prompt_section(gateway),
                            language=self.soul.language,
                            limits=limits,
                            patterns=patterns,
                            facts=facts,
                            style=style_block,
                            vibe=vibe,
                            topic=topic_block,
                            away=away,
                            emotion=emotion_block,
                            memory=memory_block,
                            actions=actions_block,
                            skills=skill_block,
                            tools=self.tool_index.registry(gateway.name),
                            commands=self.commands,
                            bodies=self.tool_index.other_bodies(gateway.name),
                            sender=message.sender_name,
                            mode=decision.mode,
                            tone=decision.tone,
                            decision_reason=decision.reason,
                            safety=safety,
                            person_style=person_style,
                            bonuses=bonuses,
                        )
                        answer = await self.llm.run(
                            system_prompt(
                                self.config,
                                roles,
                                context=prompt_context,
                                parts=(*CORE_PARTS, *self.plugin_parts),
                            ),
                            content,
                            specs,
                            tool_ctx,
                            session=f"eidesoul-{thread_id}",
                            turn_id=turn_id,
                            deadline=deadline,
                            min_call_seconds=min_call,
                            on_step=self.steps.recorder(turn_id, "drafted"),
                            lookup_step=self.steps.lookup(turn_id),
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
                                latest=message.text,
                                session=f"eidesoul-character-{thread_id}",
                                is_professional=bool(
                                    decision is not None and decision.mode == "professional"
                                ),
                                deadline=deadline,
                                min_call_seconds=min_call,
                            )
                            log.info(
                                "level chat=%s status=%s",
                                thread_id,
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
                    if stop.startswith("refused:"):
                        self._interrupt(
                            turn_id, f"send refused: {stop.split(':', 1)[1]}", force=True
                        )
                        await self._notice_interrupted(turn_id, thread_id, gateway)
                        return actions
                    if stop.startswith("failed"):
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

    async def keep_typing(self, thread_id: str, gateway: GatewayCore) -> None:
        while True:
            await asyncio.sleep(TYPING_REFRESH)
            await self._typing(gateway, thread_id, True)

    def decision_transcript(self, thread_id: str) -> str:
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
