"""Discord gateway: guild channels, threads, forum posts, components, and live edits.

ADR-0010 §4.4. The body renders the prompt options `BaseGateway.ui_of` resolves as an `Embed`
plus a component row, routes a click back as the exact chat command, and edits one status
message per turn.
"""


import contextlib
import logging
import os
from collections.abc import Mapping
from pathlib import Path

from ...engine.access.identity import IdentityStore
from ...engine.config import Config, GatewaySettings
from ...engine.inbound import Action, GatewayError, Percept
from ...engine.registry import Handler
from ...engine.turn.context import Message
from ..base import (
    BaseGateway,
    attachment_path,
    private_thread_of,
    progress_text,
    register_gateway,
    transcribe_media,
)
from . import ingress, policy, render
from .config import DiscordSettings
from .transport import DiscordPyTransport, DiscordTransport, Handlers

log = logging.getLogger(__name__)

FILE_LIMIT = 25 * 1024 * 1024


@register_gateway("discord", extra="discord")
class DiscordGateway(BaseGateway):
    """A Discord bot: one client, one dispatcher, native embeds and components."""

    name = "discord"
    extra = "discord"

    @classmethod
    def default_settings(cls) -> dict[str, object]:
        return {
            **super().default_settings(),
            "bot_token_env": "DISCORD_BOT_TOKEN",
            "sync_commands": True,
            "show_steps": True,
            "interim_updates": True,
        }

    @classmethod
    def default_role_policy(cls, role_name: str) -> dict[str, object]:
        name = str(role_name or "").strip().lower()
        if name == "administrator":
            return {
                "dm": True,
                "channels": "all",
                "threads": "all",
                "reply_to_bot": True,
            }
        if name == "member":
            return {
                "dm": True,
                "channels": "mention",
                "threads": "mention",
                "reply_to_bot": True,
            }
        return {"dm": False, "channels": "none", "threads": "none", "reply_to_bot": False}

    @classmethod
    def validate_role_policy(cls, block: Mapping[str, object]) -> None:
        super().validate_role_policy(block)
        if policy.Access.parse(block) is not None:
            return
        allowed = ", ".join(sorted(policy.MODES))
        raise ValueError(
            "gateway 'discord': a role policy needs dm: bool, "
            f"channels/threads: one of [{allowed}], reply_to_bot: bool"
        )

    def __init__(
        self,
        config: Config,
        db,
        history,
        windows,
        *,
        settings: GatewaySettings | None = None,
        transport: DiscordTransport | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.history = history
        self.windows = windows
        raw = settings or config.gateways.get("discord") or GatewaySettings("discord")
        self.settings = DiscordSettings.from_mapping(raw.enabled, raw.options, os.environ)
        self.transport: DiscordTransport = transport or DiscordPyTransport(
            self.settings.bot_token
        )
        self.self_id = ""
        self._agent = None
        self._status: dict[str, str] = {}

    # ------------------------------------------------------------ surface

    def tools(self) -> list[Handler]:
        """No Discord-only tools: the core tools already reach every body (AGENTS §6)."""
        return []

    def commands(self) -> list[Handler]:
        """No Discord-only commands: the engine registry is the whole catalog."""
        return []

    def access_refusal(self, inbound: Percept) -> str:
        """ADR-0002 §4.3: the highest-priority role decides; no lower role can grant access."""
        person = IdentityStore(self.db).resolve(self.name, inbound.actor)
        role, block = self.config.roles.policy_for(person, self.name)
        access = policy.Access.parse(block)
        if access is None:
            log.info(
                "gateway_policy_missing gateway=%s role=%s chat=%s",
                self.name,
                role.name,
                inbound.thread,
            )
            return "policy_missing"
        reason = access.refusal(inbound.hints)
        if reason:
            log.info(
                "gateway_policy_denied gateway=%s role=%s chat=%s reason=%s",
                self.name,
                role.name,
                inbound.thread,
                reason,
            )
        return reason

    def normalize_address(self, raw: str) -> str:
        return str(raw or "").strip()

    # ---------------------------------------------------------- lifecycle

    async def run(self, agent) -> None:
        self._agent = agent
        await self.transport.start(
            Handlers(
                ready=self.on_ready,
                message=lambda payload: self.on_message(payload, agent),
                interaction=lambda payload: self.on_interaction(payload, agent),
            )
        )

    async def on_ready(self) -> None:
        """The client is connected: learn its own id and sync the native `/` catalog."""
        agent = self._agent
        if agent is None:
            return
        me = getattr(getattr(self.transport, "_client", None), "user", None)
        self.self_id = str(getattr(me, "id", "") or "")
        if not self.settings.sync_commands:
            return
        entries = []
        for name in agent.commands.names():
            handler = agent.commands.get(name)
            text = str(getattr(handler, "description", "") or "").strip() or name
            entries.append({"command": name, "description": text})
        with contextlib.suppress(GatewayError):
            await self.transport.sync_commands(entries)

    async def stop(self) -> None:
        await self.edit_debouncer().flush()
        with contextlib.suppress(Exception):
            await self.transport.close()

    # ------------------------------------------------------------ ingress

    async def on_message(self, payload: Mapping, agent) -> None:
        author = payload.get("author") or {}
        if isinstance(author, Mapping) and str(author.get("id") or "") == self.self_id:
            return
        percept = ingress.message_percept(payload, self_id=self.self_id)
        if percept is None:
            return
        await agent.handle(percept, self)

    async def on_interaction(self, payload: Mapping, agent) -> None:
        found = ingress.interaction_percept(payload)
        if found is None:
            return
        percept, _, message_id = found
        await self.mark_decision(percept.thread, message_id)
        await agent.handle(percept, self)

    async def mark_decision(self, thread: str, message_id: str) -> None:
        """Strip the buttons after a click, so the decision cannot be repeated by accident."""
        if not thread or not message_id:
            return
        with contextlib.suppress(GatewayError):
            await self.transport.edit(thread, message_id, {"components": []})

    # ---------------------------------------------------------------- send

    async def dispatch(self, action: Action) -> str:
        """Message Dispatcher: this body's verbs, one refusal for anything else."""
        thread = action.address.thread
        match action.capability:
            case "send_text":
                return await self.send_text(
                    thread, str(action.payload or ""), action=action
                )
            case "send_thinking" | "send_step" | "send_interim":
                return await self.send_progress(thread, action)
            case "send_file":
                payload = action.payload if isinstance(action.payload, dict) else {}
                return await self.send_file(
                    thread,
                    Path(str(payload.get("path") or "")),
                    str(payload.get("caption") or ""),
                )
            case "send_voice":
                payload = action.payload if isinstance(action.payload, dict) else {}
                path = payload.get("path") if payload else action.payload
                # Discord plays any audio attachment; there is no voice-note flag to set.
                return await self.send_file(thread, Path(str(path or "")))
            case "react":
                payload = action.payload if isinstance(action.payload, dict) else {}
                await self.react(
                    thread,
                    str(payload.get("message_id") or ""),
                    str(payload.get("emoji") or ""),
                )
                return ""
            case "typing":
                return ""
            case _:
                raise GatewayError(
                    f"body '{self.name}' does not support '{action.capability}'"
                )

    async def send_text(
        self, thread: str, text: str, *, action: Action | None = None
    ) -> str:
        block = self.ui_of(action)
        payload: dict = {"content": "", "embed": None, "components": []}
        if block:
            payload["embed"] = render.embed(block)
            payload["components"] = render.components(block)
        parts = render.chunk(render.plain(str(text or "")), self.settings.message_limit)
        if not parts and payload["embed"] is None:
            return ""
        message_id = ""
        for index, part in enumerate(parts or [""]):
            last = index == len(parts) - 1
            result = await self.transport.send(
                thread,
                {
                    "content": part,
                    "embed": payload["embed"] if last else None,
                    "components": payload["components"] if last else [],
                },
            )
            message_id = str((result or {}).get("id") or "") if result else message_id
        return message_id

    async def send_progress(self, thread: str, action: Action) -> str:
        """One status message per turn, edited under the debouncer (ADR-0010 §4.8)."""
        text = progress_text(action.capability, action.payload)
        if not text:
            return ""
        key = action.replace_id or action.stream_id or f"{thread}:progress"
        message_id = self._status.get(key)
        if not message_id:
            result = await self.transport.send(
                thread, {"content": self._status_body(action.capability, text)}
            )
            message_id = str((result or {}).get("id") or "") if result else ""
            if message_id:
                self._status[key] = message_id
            return message_id

        async def apply() -> None:
            with contextlib.suppress(GatewayError):
                await self.transport.edit(
                    thread,
                    message_id,
                    {"content": self._status_body(action.capability, text)},
                )

        await self.edit_debouncer().submit(key, apply)
        return message_id

    @staticmethod
    def _status_body(capability: str, text: str) -> str:
        """Discord hides raw reasoning behind a spoiler; a step stays a plain line."""
        if capability == "send_thinking":
            return f"> ||{text}||"
        return f"> {text}"

    async def send_file(self, thread: str, path: Path, caption: str = "") -> str:
        target = Path(path).resolve()  # noqa: ASYNC240
        if not target.is_file():
            raise GatewayError(f"file not found: {path}")
        if not target.is_relative_to(self.config.workspace.resolve()):
            raise GatewayError(f"path outside the workspace: {path}")
        if target.stat().st_size > FILE_LIMIT:
            raise GatewayError("file is larger than Discord accepts (25 MB)")
        result = await self.transport.upload(thread, target, caption)
        return str((result or {}).get("id") or "") if result else ""

    async def react(self, thread: str, message_id: str, emoji: str) -> None:
        """A native emoji reaction; a refusal is advisory, never a failed turn."""
        if not message_id or not emoji:
            return
        with contextlib.suppress(GatewayError):
            await self.transport.react(thread, message_id, emoji)

    # -------------------------------------------------------------- media

    async def prepare_media(self, thread: str, message: Message) -> None:
        """Download every attachment, then let the speech seam transcribe the voice ones."""
        for index, media in enumerate(message.attachments):
            await self._download(thread, message.id, index, media)
        await transcribe_media(self.config, message)

    async def _download(self, thread: str, message_id: str, index: int, media) -> None:
        if media.path is not None or not media.ref:
            return
        url = media.ref.decode("utf-8", "replace")
        if not url.startswith(("http://", "https://")):
            return
        target = attachment_path(
            self.config, thread, f"{message_id}-{index}", media.filename
        )
        if await self.transport.download(url, target):
            media.path = target
            media.size = media.size or target.stat().st_size

    # ------------------------------------------------------------ display

    def render(self, text: str) -> str:
        return render.plain(str(text or "")).strip()

    def prompt_section(self) -> str:
        return (
            "## Discord interface\n"
            "- Use Discord markdown: **bold**, *italic*, `mono`, > quote, and ``` fences.\n"
            "- One message holds 2000 characters; the runtime splits longer replies and keeps "
            "code fences closed.\n"
            "- A thread or forum post is its own conversation; do not assume the parent "
            "channel reads it."
        )

    def self_handles(self) -> set[str]:
        return {self.self_id} if self.self_id else set()

    def chat_name(self, thread: str) -> str:
        return thread

    def private_thread(self, person_id: str) -> str:
        return private_thread_of(self.db, self.name, person_id)

    async def abort(self, thread: str) -> None:
        self._status.pop(f"{thread}:progress", None)
