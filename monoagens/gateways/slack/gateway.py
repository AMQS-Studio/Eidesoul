"""Slack gateway: Socket Mode, Block Kit buttons, `thread_ts` routing, and live updates.

ADR-0010 §4.5. The body renders the prompt options `BaseGateway.ui_of` resolves as Block Kit,
updates the message in place on a click, and keeps one status message per thread edited under
the debouncer.
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
from .config import SlackSettings
from .transport import Handlers, SlackBoltTransport, SlackTransport

log = logging.getLogger(__name__)

FILE_LIMIT = 1024 * 1024 * 1024


@register_gateway("slack", extra="slack")
class SlackGateway(BaseGateway):
    """A Slack app: Socket Mode by default, Block Kit for decisions, threads preserved."""

    name = "slack"
    extra = "slack"

    @classmethod
    def default_settings(cls) -> dict[str, object]:
        return {
            **super().default_settings(),
            "bot_token_env": "SLACK_BOT_TOKEN",
            "app_token_env": "SLACK_APP_TOKEN",
            "signing_secret_env": "",
            "mode": "socket",
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
            "gateway 'slack': a role policy needs dm: bool, "
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
        transport: SlackTransport | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.history = history
        self.windows = windows
        raw = settings or config.gateways.get("slack") or GatewaySettings("slack")
        self.settings = SlackSettings.from_mapping(raw.enabled, raw.options, os.environ)
        self.transport: SlackTransport = transport or SlackBoltTransport(
            self.settings.bot_token, self.settings.app_token, self.settings.mode
        )
        self.self_id = ""
        self._status: dict[str, str] = {}

    # ------------------------------------------------------------ surface

    def tools(self) -> list[Handler]:
        """No Slack-only tools: the core tools already reach every body (AGENTS §6)."""
        return []

    def commands(self) -> list[Handler]:
        """No Slack-only commands: the engine registry is the whole catalog."""
        return []

    def access_refusal(self, inbound: Percept) -> str:
        """ADR-0002 §4.3: the highest-priority role decides; no lower role can grant access."""
        person = IdentityStore(self.db).resolve(self.name, inbound.actor)
        role, block = self.config.roles.policy_for(
            person,
            self.name,
            room=f"{self.name}:{inbound.thread}",
            participants=self.room_participants,
        )
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
        await self.transport.start(
            Handlers(
                ready=self.on_ready,
                message=lambda body: self.on_message(body, agent),
                action=lambda body: self.on_action(body, agent),
            )
        )

    async def on_ready(self) -> None:
        """Learn the bot's own user id; Slack declares slash commands in the app manifest."""
        self.self_id = str(getattr(self.transport, "bot_user_id", "") or "")
        log.info("slack gateway ready user=%s", self.self_id or "-")

    async def stop(self) -> None:
        await self.edit_debouncer().flush()
        with contextlib.suppress(Exception):
            await self.transport.close()

    # ------------------------------------------------------------ ingress

    async def on_message(self, body: Mapping, agent) -> None:
        event = body.get("event") if isinstance(body.get("event"), Mapping) else body
        if not isinstance(event, Mapping):
            return
        if str(event.get("bot_id") or "") or str(event.get("subtype") or "") in (
            "bot_message",
            "message_deleted",
        ):
            return
        if str(event.get("subtype") or "") == "message_changed":
            inner = event.get("message")
            if isinstance(inner, Mapping):
                self.windows.replace_text(
                    ingress.thread_of(event),
                    str(inner.get("ts") or ""),
                    str(inner.get("text") or ""),
                )
            return
        percept = ingress.message_percept(event, self_id=self.self_id)
        if percept is None:
            return
        await agent.handle(percept, self)

    async def on_action(self, body: Mapping, agent) -> None:
        found = ingress.action_percept(body)
        if found is None:
            return
        percept, _response_url, message_ts = found
        await self.mark_decision(percept.thread, message_ts, str(percept.payload.text))
        await agent.handle(percept, self)

    async def mark_decision(self, thread: str, message_ts: str, command: str) -> None:
        """Replace the buttons with the decision, so a second click cannot repeat it."""
        channel, _, thread_ts = str(thread or "").partition(":")
        if not channel or not message_ts:
            return
        with contextlib.suppress(GatewayError):
            await self.transport.update(
                channel,
                message_ts,
                f"✅ {command}",
                [{"type": "context", "elements": [{"type": "mrkdwn", "text": command}]}],
            )
        if thread_ts:
            with contextlib.suppress(GatewayError):
                await self.transport.set_status(channel, thread_ts, "")

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

    @staticmethod
    def _split(thread: str) -> tuple[str, str]:
        channel, _, thread_ts = str(thread or "").partition(":")
        return channel, thread_ts

    async def send_text(
        self, thread: str, text: str, *, action: Action | None = None
    ) -> str:
        channel, thread_ts = self._split(thread)
        block = self.ui_of(action)
        blocks = render.approval_blocks(block) if block else []
        parts = render.chunk(render.plain(str(text or "")), self.settings.message_limit)
        if not parts and not blocks:
            return ""
        ts = ""
        for index, part in enumerate(parts or [""]):
            last = index == len(parts) - 1
            result = await self.transport.post(
                channel,
                part,
                blocks if last else None,
                thread_ts,
            )
            ts = str((result or {}).get("ts") or "") if result else ts
        return ts

    async def send_progress(self, thread: str, action: Action) -> str:
        """One status message per thread, edited under the debouncer (ADR-0010 §4.8)."""
        text = progress_text(action.capability, action.payload)
        if not text:
            return ""
        channel, thread_ts = self._split(thread)
        key = action.replace_id or action.stream_id or f"{thread}:progress"
        blocks = render.progress_blocks(action.capability, text)
        message_ts = self._status.get(key)
        if not message_ts:
            result = await self.transport.post(channel, text, blocks, thread_ts)
            message_ts = str((result or {}).get("ts") or "") if result else ""
            if message_ts:
                self._status[key] = message_ts
            if thread_ts:
                with contextlib.suppress(GatewayError):
                    await self.transport.set_status(channel, thread_ts, text)
            return message_ts

        async def apply() -> None:
            with contextlib.suppress(GatewayError):
                await self.transport.update(channel, message_ts, text, blocks)

        await self.edit_debouncer().submit(key, apply)
        return message_ts

    async def send_file(self, thread: str, path: Path, caption: str = "") -> str:
        channel, thread_ts = self._split(thread)
        target = Path(path).resolve()  # noqa: ASYNC240
        if not target.is_file():
            raise GatewayError(f"file not found: {path}")
        if not target.is_relative_to(self.config.workspace.resolve()):
            raise GatewayError(f"path outside the workspace: {path}")
        if target.stat().st_size > FILE_LIMIT:
            raise GatewayError("file is larger than Slack accepts")
        result = await self.transport.upload(channel, target, caption, thread_ts)
        return str((result or {}).get("ts") or "") if result else ""

    async def react(self, thread: str, message_id: str, emoji: str) -> None:
        """A native reaction on one message; a refusal is advisory, never a failed turn."""
        channel, _ = self._split(thread)
        if not channel or not message_id or not emoji:
            return
        with contextlib.suppress(GatewayError):
            await self.transport.react(channel, message_id, emoji)

    # -------------------------------------------------------------- media

    async def prepare_media(self, thread: str, message: Message) -> None:
        """Download every file, then let the speech seam transcribe the audio ones."""
        for index, media in enumerate(message.attachments):
            await self._download(thread, message.id, index, media)
        await transcribe_media(self.config, message)

    async def _download(self, thread: str, message_id: str, index: int, media) -> None:
        if media.path is not None or not media.ref:
            return
        url = media.ref.decode("utf-8", "replace")
        if not url:
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
            "## Slack interface\n"
            "- Use Slack mrkdwn: *bold*, _italic_, `mono`, > quote, and ``` fences.\n"
            "- A thread (`thread_ts`) is its own conversation; do not assume the parent "
            "channel reads it.\n"
            "- Long replies are split by the runtime; keep code blocks closed."
        )

    def self_handles(self) -> set[str]:
        return {self.self_id} if self.self_id else set()

    def chat_name(self, thread: str) -> str:
        return thread

    def private_thread(self, person_id: str) -> str:
        return private_thread_of(self.db, self.name, person_id)

    async def abort(self, thread: str) -> None:
        self._status.pop(f"{thread}:progress", None)
