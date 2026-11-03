"""Agent: orchestration of one conversation turn without any transport."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from . import assembly, guard
from .approvals import ApprovalStore
from .character import effective_limits, polish, regulate
from .commands import REGISTRY as CORE_COMMANDS
from .commands import CommandContext
from .config import Config
from .context import (
    AUTHOR_BOT,
    TEXT_EXT,
    Message,
    WorkingMemory,
    build_user_content,
    clean,
    fallback_content,
    keyword_set,
    prompt_entry,
)
from .decision import (
    Appraisal,
    Decision,
    RelationshipDelta,
    decide,
    ensure_reply,
    ground_tone,
)
from .guard import SafetyPolicy, SandboxGuard, build_safety
from .i18n import reload_catalogs, t
from .identity import IdentityStore
from .inbound import (
    AUTHORIZATION,
    CONSENT,
    Action,
    GatewayCore,
    GatewayError,
    Percept,
    Rich,
)
from .knowledge import select_facts
from .llm import LLM
from .memory import MemoryBackend, MemoryContext, memory_status
from .patterns import load_lexicon, room_register, select_patterns
from .permissions import command_allowed, commands_for, tool_patterns, tools_for
from .privacy import PrivacyService
from .prompts import CRISIS_SAFETY, away_block, character_block, research_block, system_prompt
from .registry import Handler, Registry
from .reply import human_chunks, split_text, typing_delay
from .roles import Role
from .runtime import RuntimeFacts
from .target import classify, combine, score_style
from .tools import REGISTRY as CORE_TOOLS
from .tools import ToolContext

log = logging.getLogger(__name__)

TYPING_REFRESH = 8
RESTART_FIELDS = ("workspace", "gateways", "sandbox", "web_search", "mcp", "database", "paths")
DECISION_HISTORY = 30
DECISION_FALLBACK_HISTORY = 20
DECISION_LINE_CHARS = 500
MAX_TEXT = 10_000
MENTION_HEAD_RE = r"^@([^\s@]+)\s*"


def command_body(text: str, bot_name: str, handles: set[str]) -> str:
    """Strip a leading bot mention: '@Eidesoul /mood' -> '/mood'."""
    import re

    body = str(text or "").lstrip()
    pattern = re.compile(MENTION_HEAD_RE)
    match = pattern.match(body)
    while match is not None:
        handle = match.group(1).lstrip("+")
        digits = "".join(ch for ch in handle if ch.isdigit()) or handle.lower()
        if handle.casefold() != str(bot_name or "").casefold() and digits not in handles:
            break
        body = body[match.end() :]
        match = pattern.match(body)
    return body


def _stale(answered: dict[str, frozenset[str]], thread_id: str, message: Message) -> bool:
    return bool(message.id) and message.id in answered.get(thread_id, frozenset())


FILE_TEXT_SIZE = 512 * 1024
FILE_TEXT_CHARS = 4000


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
        currents=None,
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
        self.currents = currents
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
        self.turn_lock = asyncio.Lock()
        self.tasks: set[asyncio.Task] = set()
        self.answered: dict[str, frozenset[str]] = {}
        self.backlog: dict[str, int] = {}
        self.quiet: dict[str, float] = {}
        handlers = list(CORE_TOOLS.by_name.values())
        for gateway in self.gateways:
            handlers.extend(gateway.tools())
        self.tools = Registry(handlers, "tool")
        handlers = list(CORE_COMMANDS.by_name.values())
        for gateway in self.gateways:
            handlers.extend(gateway.commands())
        self.commands = Registry(handlers, "command")
        self.runtime = runtime or RuntimeFacts(
            db_label=getattr(db, "label", ""), paths=config.paths
        )
        self.runtime.paths = config.paths
        self.runtime.db_label = getattr(db, "label", "")
        self.runtime.gateways = tuple(gateway.name for gateway in self.gateways)
        self.runtime.memory_status = memory_status(memory)
        self.privacy = PrivacyService(self)

    # ------------------------------------------------------------- util

    def spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def _role_set(self, inbound: Percept) -> tuple[Role, ...]:
        declared = inbound.hints.get("roles") or ()
        names = (declared,) if isinstance(declared, str) else tuple(str(item) for item in declared)
        if names:
            return self.config.roles.override(names)
        return self.config.roles.for_person(inbound.actor)

    @staticmethod
    def _sender(inbound: Percept) -> str:
        payload = inbound.payload
        return payload.sender_id or payload.sender_address or inbound.thread

    async def _target(
        self, inbound: Percept, gateway: GatewayCore, messages, message
    ) -> tuple[str, float, str]:
        """Target gender: manual override, then typing style, avatar only when requested."""
        recent = [
            entry.text
            for entry in messages[-8:]
            if entry.sender_id == message.sender_id and entry.text.strip()
        ]
        style = score_style(recent)
        stored = ("", 0.0, "")
        if self.affect is not None:
            try:
                stored = self.affect.gender(inbound.actor)
            except Exception:
                log.debug("gender: failed to read the stored value", exc_info=True)
        if stored[0] and stored[2] == "manual":
            return stored
        avatar = None
        if style[0] == "unknown" and self.config.character.gender_avatar:
            grab = getattr(gateway, "avatar", None)
            if callable(grab):
                try:
                    shot = await grab(message.sender_address)
                except Exception:
                    log.debug("gender: avatar fetch failed", exc_info=True)
                    shot = None
                if shot:
                    avatar = await classify(
                        self.llm, shot[0], shot[1], locale=self.config.soul.locale
                    )
        target, confidence, source = combine(
            style, avatar, gate=self.config.character.gender_gate
        )
        if self.affect is not None and (target, confidence, source) != tuple(stored):
            try:
                self.affect.set_gender(inbound.actor, target, confidence, source)
            except Exception:
                log.debug("gender: failed to save the label", exc_info=True)
        return (target, confidence, source)

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
        specs = tools_for(self.tools, roles) + (self.hub.specs(roles) if self.hub else [])
        return CommandContext(
            agent=self,
            gateway=gateway,
            roles=roles,
            thread_id=thread_id,
            sender=sender,
            direct=bool(inbound.hints.get("dm", False)) if inbound is not None else True,
            args=args,
            commands=tuple(commands_for(self.commands, roles)),
            tools=tuple(specs),
            inbound=inbound,
        )

    def _specs(self, roles: tuple[Role, ...]) -> list[Handler]:
        specs = list(tools_for(self.tools, roles))
        if self.hub is not None:
            specs += self.hub.specs(roles)
        return specs

    def _decision_transcript(self, thread_id: str) -> str:
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

    # ------------------------------------------------------------ outbound

    def _self_sender_id(self, gateway: GatewayCore) -> str:
        return next(iter(sorted(gateway.self_handles())), "")

    async def deliver_text(
        self,
        thread_id: str,
        text: str,
        *,
        gateway: GatewayCore,
        reply: Percept | None = None,
    ) -> str:
        message_id = await gateway.send_text(thread_id, text, reply=reply)
        self.windows.append(
            thread_id,
            Message(
                id=message_id,
                sender_name=self.soul.name,
                sender_id=self._self_sender_id(gateway),
                when=datetime.now(),
                text=text,
                outgoing=True,
                author=AUTHOR_BOT,
            ),
        )
        return message_id

    async def deliver_file(
        self,
        thread_id: str,
        path: Path,
        caption: str = "",
        *,
        gateway: GatewayCore,
        reply: Percept | None = None,
    ) -> str:
        path = Path(path)
        if not isinstance(gateway, Rich):
            return await self.deliver_text(
                thread_id,
                file_fallback_text(path, caption, self.soul.language),
                gateway=gateway,
                reply=reply,
            )
        message_id = await gateway.send_file(thread_id, path, caption, reply=reply)
        note = f"(file terkirim: {path.name}{f' — {caption}' if caption else ''})"
        self.windows.append(
            thread_id,
            Message(
                id=message_id,
                sender_name=self.soul.name,
                sender_id=self._self_sender_id(gateway),
                when=datetime.now(),
                text=note,
                outgoing=True,
                author=AUTHOR_BOT,
            ),
        )
        return message_id

    async def deliver_sticker(
        self, thread_id: str, message_id: str, *, gateway: GatewayCore
    ) -> str | None:
        if not isinstance(gateway, Rich):
            return None
        label = await gateway.send_sticker(thread_id, message_id)
        if label:
            self.windows.append(
                thread_id,
                Message(
                    id=f"sticker-{time.time_ns()}",
                    sender_name=self.soul.name,
                    sender_id=self._self_sender_id(gateway),
                    when=datetime.now(),
                    text=label,
                    outgoing=True,
                    author=AUTHOR_BOT,
                ),
            )
        return label

    async def _typing(self, gateway: GatewayCore, thread_id: str, composing: bool) -> None:
        """A body without `Rich` has no presence indicator; skip, never fail."""
        if isinstance(gateway, Rich):
            await gateway.typing(thread_id, composing)

    async def _prepare_media(self, gateway: GatewayCore, thread_id: str, entry: Message) -> None:
        if isinstance(gateway, Rich):
            await gateway.prepare_media(thread_id, entry)

    async def send_text(self, thread_id: str, text: str, *, gateway: GatewayCore | None) -> str:
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
                await self.deliver_text(thread_id, chunk, gateway=gateway)
        except GatewayError as exc:
            return f"error: failed to send text: {exc}"
        name = gateway.chat_name(thread_id)
        suffix = f" ({len(chunks)} parts)" if len(chunks) > 1 else ""
        return f"ok: text sent to {name or thread_id}{suffix}"

    async def send_file(
        self, thread_id: str, path: Path, caption: str = "", *, gateway: GatewayCore | None
    ) -> str:
        if gateway is None:
            return "error: gateway is not available"
        try:
            await self.deliver_file(thread_id, Path(path), caption, gateway=gateway)
        except GatewayError as exc:
            return f"error: failed to send file: {exc}"
        except OSError as exc:
            return f"error: {type(exc).__name__}: {exc}"
        return f"ok: file sent: {Path(path).name}"

    # ------------------------------------------------------------- turn

    def _identify(self, inbound: Percept, gateway: GatewayCore) -> Percept:
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

    async def handle(self, inbound: Percept, gateway: GatewayCore) -> list[Action]:
        """One turn. Returns the actions the turn took; the engine has no transport branch."""
        actions: list[Action] = []
        message = inbound.payload
        thread_id = inbound.thread
        self.windows.append(thread_id, message)
        if self.ambient is not None:
            self.ambient.note(thread_id, message)
        if message.outgoing or bool(inbound.hints.get("self")):
            return actions
        age = time.time() - message.when.timestamp()
        backlog = float(getattr(gateway, "backlog_seconds", 0.0) or 0.0)
        if backlog > 0 and age > backlog:
            self.backlog[thread_id] = self.backlog.get(thread_id, 0) + 1
            log.info("old message chat=%s age=%.0fs: archive only", thread_id, age)
            return actions

        # Only a message this turn acts on earns an identity: the bot's own echo must not mint one.
        inbound = self._identify(inbound, gateway)
        text = message.text
        crisis = guard.is_crisis(text)
        roles = self._role_set(inbound)
        handle_set = gateway.self_handles()
        body = command_body(text, self.soul.name, handle_set)
        is_command = body.startswith("/")
        if not is_command and await self.privacy.handle_pending(
            inbound, gateway, self._sender(inbound)
        ):
            return actions
        if not is_command and not crisis and self.quiet.get(thread_id, 0.0) > time.time():
            log.info("quiet chat=%s: message recorded without a reply", thread_id)
            return actions
        if not is_command and not crisis and self.llm is None:
            return actions
        head_word, _, rest = body[1:].partition(" ") if is_command else ("", "", "")
        name = head_word.lower()
        direct = (
            bool(inbound.hints.get("dm"))
            or bool(inbound.hints.get("mention"))
            or bool(inbound.hints.get("reply"))
        )

        queue = contextlib.nullcontext() if is_command else self.turn_lock
        async with queue:
            if not is_command and _stale(self.answered, thread_id, message):
                log.info("stale message chat=%s: already covered by an earlier burst", thread_id)
                return actions
            missed = self.backlog.pop(thread_id, 0) if not is_command else 0
            away = away_block(missed)
            vibe = ""
            if self.vibe is not None and not is_command and not crisis and text.strip():
                try:
                    vibe = await self.vibe.observe(thread_id, text)
                except Exception:
                    log.exception("failed to update vibe chat=%s", thread_id)
            emotion_block = ""
            turn = None
            if not is_command and self.affect is not None:
                try:
                    turn = self.affect.turn_start(thread_id, message.id)
                except Exception:
                    log.exception("failed to open the emotion turn chat=%s", thread_id)
                try:
                    emotion_block = self.affect.prompt(thread_id, inbound.actor)
                except Exception:
                    log.exception("failed to read the emotion state chat=%s", thread_id)

            called = bool(inbound.hints.get("mention")) or bool(inbound.hints.get("reply"))
            unprompted = (
                not bool(inbound.hints.get("dm")) and not is_command and not crisis and not called
            )
            decision: Decision | None
            safety = ""
            if crisis:
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
                decision = await decide(
                    self.llm,
                    thread_id=thread_id,
                    chat_name=gateway.chat_name(thread_id),
                    current=prompt_entry(message, self.soul.name, DECISION_LINE_CHARS),
                    transcript=self._decision_transcript(thread_id),
                    emotion=turn,
                    relationship=emotion_block,
                    direct=bool(inbound.hints.get("dm")),
                    mentioned=bool(inbound.hints.get("mention")),
                    replied=bool(inbound.hints.get("reply")),
                    away=away,
                    vibe=vibe,
                    soul=self.soul,
                )
                if decision is None:
                    log.warning("empty decision, continuing without a tone: %s", thread_id)
                    decision = Decision(
                        addressed="me" if direct else "unknown",
                        act="reply" if direct else "ignore",
                        mode="daily",
                        tone="curt",
                        bubbles=1,
                        appraisal=Appraisal("neutral", 0.0, 0.0, 0.0),
                        relationship=RelationshipDelta(),
                        reason="model did not answer",
                    )
                elif direct:
                    decision = ensure_reply(decision)
                if turn is not None:
                    annoyed = 0.0
                    if self.affect is not None:
                        try:
                            annoyed = next(
                                (
                                    float(row["annoyance"])
                                    for row in self.affect.known_people(thread_id, 20)
                                    if row["person_id"] == inbound.actor
                                ),
                                0.0,
                            )
                        except Exception:
                            log.debug("failed to read annoyance chat=%s", thread_id, exc_info=True)
                    grounded = ground_tone(decision, turn, annoyed)
                    if grounded.tone != decision.tone:
                        log.info(
                            "tone %s->%s chat=%s: feeling does not match",
                            decision.tone,
                            grounded.tone,
                            thread_id,
                        )
                    decision = grounded

            if not is_command and self.affect is not None:
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
                if decision.act == "ignore":
                    log.info(
                        "diam chat=%s addressed=%s reason=%s",
                        thread_id,
                        decision.addressed,
                        decision.reason,
                    )
                    return actions
                if decision.act == "react":
                    label = await self.deliver_sticker(thread_id, message.id, gateway=gateway)
                    if label:
                        actions.append(
                            Action(
                                capability="send_sticker",
                                target=thread_id,
                                payload=label,
                                approval_class=CONSENT,
                            )
                        )
                    return actions

            await self._typing(gateway, thread_id, True)
            typing = asyncio.create_task(self._keep_typing(thread_id, gateway))
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
                covered = frozenset({message.id})
                reply: str | None = None
                can_memory = False
                memory_block = style_block = topic_block = person_style = ""
                memory_ctx = MemoryContext()
                if is_command:
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
                    specs = self._specs(roles)
                    can_memory = any(spec.name == "memory" for spec in specs)
                    cross_send = any(spec.name == "send_to" for spec in specs)
                    mood = None
                    if turn is not None:
                        mood = ((float(turn["pleasure"]) + 1.0) / 2.0, float(turn["arousal"]))
                    try:
                        memory_ctx = await self.memory.context(
                            person_id=inbound.actor,
                            thread_id=thread_id,
                            query=message.text,
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
                            actions_block = self.ledger.actions_context()
                        except Exception:
                            log.exception("failed to read the action ledger chat=%s", thread_id)
                    skill_block = ""
                    if self.skills is not None and any(spec.name == "skill" for spec in specs):
                        try:
                            skill_block = self.skills.index()
                        except Exception:
                            log.exception("failed to read the skill index")
                    if self.affect is not None:
                        try:
                            topic_block = self.affect.topic_block(thread_id)
                        except Exception:
                            log.exception("failed to read the chat topic chat=%s", thread_id)
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
                    covered = frozenset(entry.id for entry in messages if entry.id)
                    target, confidence, source = await self._target(
                        inbound, gateway, messages, message
                    )
                    limits = effective_limits(self.config.character, target, confidence)
                    style = self.soul.style
                    lexicon = load_lexicon(style.patterns)
                    register = room_register(
                        [entry.text for entry in messages[-6:] if entry.text.strip()], lexicon
                    )
                    patterns = select_patterns(
                        style.max_patterns,
                        register=register,
                        target=target,
                        limits=limits,
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
                    character = character_block(
                        limits,
                        target,
                        confidence,
                        gate=self.config.character.gender_gate,
                        jabs=self.config.character.roast_jabs,
                        patterns=patterns,
                        facts=facts,
                        language=self.soul.language,
                    )
                    currents_block = ""
                    if self.currents is not None:
                        try:
                            currents_block = self.currents.block(time.time(), 6)
                        except Exception:
                            log.debug("currents: failed to read the context", exc_info=True)
                    try:
                        content = await build_user_content(
                            self.config,
                            thread_id,
                            messages,
                            message,
                            chat_name=gateway.chat_name(thread_id),
                        )
                    except Exception:
                        log.exception("failed to assemble the payload chat=%s", thread_id)
                        content = fallback_content(self.config, message)
                    tool_ctx = self.tool_context(
                        thread_id,
                        self._sender(inbound),
                        roles,
                        gateway,
                        request=message.text,
                        person_id=inbound.actor,
                        direct=bool(inbound.hints.get("dm")),
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
                                    if person_row and int(person_row["interactions"]) >= 15
                                    else 0.0
                                ),
                                "style": (
                                    0.15
                                    if person_row and float(person_row["familiarity"]) >= 0.3
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
                    answer = await self.llm.run(
                        system_prompt(
                            self.config,
                            roles,
                            runtime=self.runtime,
                            gateway_hints=gateway.prompt_hints(),
                            memory=memory_block,
                            actions=actions_block,
                            skills=skill_block,
                            cross_send=cross_send,
                            topic=topic_block,
                            away=away,
                            vibe=vibe,
                            emotion=emotion_block,
                            style=style_block,
                            person_style=person_style,
                            safety=safety,
                            character=character,
                            research=research_block(self.config.soul.locale),
                            currents=currents_block,
                            sender=message.sender_name,
                            direct=bool(inbound.hints.get("dm")),
                            mode=decision.mode,
                            tone=decision.tone,
                            decision_reason=decision.reason,
                            query=message.text,
                            bonuses=bonuses,
                        ),
                        content,
                        specs,
                        tool_ctx,
                        session=f"eidesoul-{thread_id}",
                    )
                    reply = (
                        answer
                        if answer.strip() or not tool_ctx.sent
                        else t("agent.sent_ack", self.soul.language)
                    )
                    daily = decision is None or decision.mode != "professional"
                    if (
                        daily
                        and not is_command
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
                        )
                        log.info(
                            "character chat=%s status=%s",
                            thread_id,
                            "rewrite" if reply != before else "pass",
                        )
                        if not reply.strip():
                            reply = before
                    if reply and not is_command and not crisis:
                        before = reply
                        reply = await regulate(
                            self.llm,
                            self.config,
                            reply,
                            target=target,
                            confidence=confidence,
                            latest=message.text,
                            session=f"eidesoul-character-{thread_id}",
                            is_professional=bool(
                                decision is not None and decision.mode == "professional"
                            ),
                        )
                        log.info(
                            "level chat=%s target=%s/%s status=%s",
                            thread_id,
                            target,
                            f"{confidence:.2f}",
                            "regulated" if reply != before else "pass",
                        )
                    review = await self.privacy.review(
                        inbound,
                        reply,
                        [*memory_ctx.hits, *tool_ctx.memory_hits],
                        gateway,
                    )
                    if review.handled:
                        return actions
                    reply = review.reply
                    if reply and tool_ctx.cross_reads and guard.cross_chat_leak(
                        reply, tool_ctx.cross_reads
                    ):
                        log.warning("cross-chat verbatim reply blocked chat=%s", thread_id)
                        reply = t("agent.cross_chat_blocked", self.soul.language)

                professional = bool(decision is not None and decision.mode == "professional")
                humanize = self.config.humanize
                if is_command or professional:
                    chunks = split_text(gateway.render(reply or "")) or [
                        t("agent.empty_reply", self.soul.language)
                    ]
                    pace = False
                    quote_all = True
                else:
                    planned = max(1, int(humanize.max_bubbles))
                    chunks = [
                        gateway.render(chunk)
                        for chunk in human_chunks(reply or "", max_parts=planned)
                    ] or [t("agent.empty_reply", self.soul.language)]
                    pace = bool(humanize.enabled)
                    quote_all = not pace
                delivered = True
                delivered_ids: list[str] = []
                for index, chunk in enumerate(chunks):
                    if pace:
                        await self._typing(gateway, thread_id, True)
                        await asyncio.sleep(typing_delay(chunk, humanize))
                    try:
                        delivered_ids.append(
                            await self.deliver_text(
                                thread_id,
                                chunk,
                                gateway=gateway,
                                reply=inbound if (quote_all or index == 0) else None,
                            )
                        )
                        actions.append(
                            Action(
                                capability="send_text",
                                target=thread_id,
                                payload=chunk,
                                approval_class=(
                                    CONSENT if thread_id == inbound.thread else AUTHORIZATION
                                ),
                            )
                        )
                    except GatewayError:
                        log.exception("failed to send the reply to %s", thread_id)
                        delivered = False
                        break
                    if pace and index < len(chunks) - 1:
                        await asyncio.sleep(
                            max(0.0, float(humanize.between_delay)) + random.uniform(0, 0.4)
                        )
                if delivered and covered:
                    self.answered[thread_id] = covered
                self.runtime.pending_approvals = self.approvals.count_pending()
                self.approvals.expire()
                capture = (
                    reply
                    and not is_command
                    and not crisis
                    and can_memory
                    and self.config.memory.auto_capture
                    and len(str(message.text or "").strip())
                    >= int(self.config.memory.capture_min_chars)
                    and not guard.is_injection(message.text, self.soul.language)
                )
                if capture:
                    self.spawn(
                        self.privacy.capture_turn(
                            inbound, reply or "", gateway, tuple(delivered_ids)
                        )
                    )
            finally:
                typing.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await typing
                await self._typing(gateway, thread_id, False)
        return actions

    async def _keep_typing(self, thread_id: str, gateway: GatewayCore) -> None:
        while True:
            await asyncio.sleep(TYPING_REFRESH)
            await self._typing(gateway, thread_id, True)

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
        if not name:
            return t("agent.help_hint", self.soul.language)
        handler = self.commands.get(name)
        recipe = None
        if handler is None and self.recipes is not None:
            with contextlib.suppress(Exception):
                recipe = self.recipes.get(name)
        if handler is None and recipe is None:
            return t("agent.unknown_command", self.soul.language, name=name)
        names = {role.name for role in roles}
        denied = t(
            "agent.command_denied",
            self.soul.language,
            name=name,
            roles=", ".join(sorted(names)),
        )
        if recipe is not None and not (names & set(recipe.roles)):
            return denied
        if handler is not None and not command_allowed(roles, handler.name):
            return denied
        if recipe is not None:
            from .stores.recipes import run_recipe

            ctx = self.tool_context(thread_id, sender, roles, gateway, request=args)
            if recipe.kind == "recipe":
                return await run_recipe(recipe, args, ctx)
            if self.llm is None:
                return t("agent.no_llm", self.soul.language)
            specs = self._specs(roles)
            return await self.llm.run(
                system_prompt(
                    self.config,
                    roles,
                    runtime=self.runtime,
                    gateway_hints=gateway.prompt_hints(),
                    mode="professional",
                ),
                f"{recipe.prompt}\n\nArguments: {args}".strip(),
                specs,
                ctx,
                session=f"eidesoul-recipe-{thread_id}-{name}",
            )
        ctx = self.command_context(thread_id, sender, roles, gateway, args, inbound)
        return await handler.fn(ctx)

    # ------------------------------------------------------------ hidup

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
            "affect.enabled": config.affect.enabled,
            "affect.vibe.enabled": config.affect.vibe.enabled,
            "affect.vibe.attack": config.affect.vibe.attack,
            "ambient.enabled": config.ambient.enabled,
            "style.enabled": config.style.enabled,
        }

    async def reload(self) -> str:
        """Reload config/soul/role; reject changes that need a restart."""
        from .config import ConfigError, load_config

        reload_catalogs()
        loaded = load_config(
            self.config.paths.config, documents=self.documents, require_key=False
        )
        before = self._restart_tokens(self.config)
        after = self._restart_tokens(loaded)
        changed = [key for key in before if before[key] != after[key]]
        if changed:
            raise ConfigError("restart required: " + ", ".join(changed))
        old_llm = self.config.llm
        self.config = loaded
        self.soul = loaded.soul
        if self.llm is not None and loaded.llm != old_llm:
            self.llm = LLM(loaded)
        if self.skills is not None:
            self.skills.max_index_chars = max(200, int(loaded.skills.max_index_chars))
        if self.style is not None:
            self.style.config = loaded.style
        if self.affect is not None:
            self.affect.config = loaded.affect
        if self.vibe is not None:
            self.vibe.config = loaded.affect.vibe
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
        return "config/soul/roles reloaded"

    async def apply_settings_payload(self, payload: dict) -> str:
        from .tools.settings import apply_payload

        return await apply_payload(self, payload)

    async def close(self) -> None:
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
