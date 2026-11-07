"""Telegram gateway: polling or webhook ingress, native keyboards, albums, and live edits.

ADR-0010 §4.3. The body owns no protocol vocabulary beyond this package: it hands the engine a
`Percept`, renders the prompt options `BaseGateway.ui_of` resolves as an `InlineKeyboardMarkup`,
and turns a button press back into the exact chat command the engine already understands.
"""


import asyncio
import contextlib
import logging
import os
from collections.abc import Mapping
from dataclasses import replace
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
from .config import TelegramSettings
from .transport import BotAPI, HttpBotAPI

log = logging.getLogger(__name__)

ALBUM_WINDOW = 0.6
MAX_ALBUM_ITEMS = 10
FILE_LIMIT = 50 * 1024 * 1024


@register_gateway("telegram", extra="telegram")
class TelegramGateway(BaseGateway):
    """A Telegram bot over the HTTP Bot API: no SDK, no Node.js, one `asyncio` loop."""

    name = "telegram"
    extra = "telegram"

    @classmethod
    def default_settings(cls) -> dict[str, object]:
        return {
            **super().default_settings(),
            "bot_token_env": "TELEGRAM_BOT_TOKEN",
            "mode": "polling",
            "webhook_url": "",
            "webhook_secret_env": "",
            "webhook_host": "127.0.0.1",
            "webhook_port": 8443,
            "sync_commands": True,
            "show_steps": True,
            "interim_updates": True,
        }

    @classmethod
    def default_role_policy(cls, role_name: str) -> dict[str, object]:
        name = str(role_name or "").strip().lower()
        if name == "administrator":
            return {"dm": True, "groups": "all", "topics": "all", "reply_to_bot": True}
        if name == "member":
            return {
                "dm": True,
                "groups": "mention",
                "topics": "mention",
                "reply_to_bot": True,
            }
        return {"dm": False, "groups": "none", "topics": "none", "reply_to_bot": False}

    @classmethod
    def validate_role_policy(cls, block: Mapping[str, object]) -> None:
        super().validate_role_policy(block)
        if policy.Access.parse(block) is not None:
            return
        allowed = ", ".join(sorted(policy.MODES))
        raise ValueError(
            "gateway 'telegram': a role policy needs dm: bool, "
            f"groups/topics: one of [{allowed}], reply_to_bot: bool"
        )

    def __init__(
        self,
        config: Config,
        db,
        history,
        windows,
        *,
        settings: GatewaySettings | None = None,
        transport: BotAPI | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.history = history
        self.windows = windows
        raw = settings or config.gateways.get("telegram") or GatewaySettings("telegram")
        self.settings = TelegramSettings.from_mapping(raw.enabled, raw.options, os.environ)
        self.transport: BotAPI = transport or HttpBotAPI(self.settings.bot_token)
        self.self_id = ""
        self.username = ""
        self._status: dict[str, str] = {}
        self._albums: dict[str, list[Percept]] = {}
        self._album_tasks: dict[str, asyncio.Task] = {}
        self._stopping = False

    # ------------------------------------------------------------ surface

    def tools(self) -> list[Handler]:
        """No Telegram-only tools: the core tools already reach every body (AGENTS §6)."""
        return []

    def commands(self) -> list[Handler]:
        """No Telegram-only commands: the engine registry is the whole catalog."""
        return []

    def access_refusal(self, inbound: Percept) -> str:
        """ADR-0002 §4.3: the highest-priority role decides; no lower role can grant access."""
        person = IdentityStore(self.db).resolve(self.name, inbound.actor)
        role, block = self.config.roles.policy_for(
            person, self.name, room=f"{self.name}:{inbound.thread}"
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
        await self.identify()
        await self.sync_commands(agent)
        if self.settings.mode == "webhook" and self.settings.webhook_url:
            await self.install_webhook()
            from .webhook import serve

            await serve(
                lambda update: self.handle_update(update, agent),
                host=self.settings.webhook_host,
                port=self.settings.webhook_port,
                secret=self.settings.webhook_secret,
            )
            return
        await self.poll(agent)

    async def identify(self) -> None:
        """Learn this bot's own id and handle, so a mention and a reply are provable facts."""
        try:
            me = await self.transport.call("getMe")
        except GatewayError:
            log.warning("telegram getMe failed", exc_info=True)
            return
        if isinstance(me, Mapping):
            self.self_id = str(me.get("id") or "")
            self.username = str(me.get("username") or "")

    async def poll(self, agent) -> None:
        """Long polling: one `getUpdates` per round, `offset` advancing past what was handled."""
        offset = 0
        while not self._stopping:
            try:
                updates = await self.transport.call(
                    "getUpdates",
                    {
                        "offset": offset,
                        "timeout": int(self.settings.poll_timeout),
                        "allowed_updates": ["message", "edited_message", "callback_query"],
                    },
                )
            except GatewayError:
                log.warning("telegram polling failed; retrying", exc_info=True)
                await asyncio.sleep(3.0)
                continue
            for update in updates or []:
                if not isinstance(update, Mapping):
                    continue
                offset = max(offset, int(update.get("update_id") or 0) + 1)
                await self.handle_update(update, agent)

    async def stop(self) -> None:
        self._stopping = True
        for task in list(self._album_tasks.values()):
            if not task.done():
                task.cancel()
        self._album_tasks.clear()
        await self.edit_debouncer().flush()
        with contextlib.suppress(Exception):
            await self.transport.close()

    async def install_webhook(self) -> None:
        params: dict[str, object] = {
            "url": self.settings.webhook_url,
            "allowed_updates": ["message", "edited_message", "callback_query"],
        }
        if self.settings.webhook_secret:
            params["secret_token"] = self.settings.webhook_secret
        await self.transport.call("setWebhook", params)

    async def sync_commands(self, agent) -> None:
        """ADR-0010 §4.3: the registry's commands become Telegram's native `/` menu."""
        if not self.settings.sync_commands:
            return
        entries: list[dict[str, str]] = []
        for name in agent.commands.names():
            handler = agent.commands.get(name)
            text = str(getattr(handler, "description", "") or "").strip() or name
            entries.append({"command": name[:32], "description": text[:256]})
        if not entries:
            return
        try:
            await self.transport.call("setMyCommands", {"commands": entries[:100]})
        except GatewayError:
            log.warning("telegram command sync failed", exc_info=True)

    # ------------------------------------------------------------ ingress

    async def handle_update(self, update: Mapping, agent) -> None:
        """One Bot API update: a click becomes a command, a message becomes a turn."""
        found = ingress.callback_percept(update)
        if found is not None:
            percept, callback_id, message = found
            await self.answer_callback(callback_id, str(percept.payload.text))
            await self.mark_decision(percept.thread, message)
            await agent.handle(percept, self)
            return
        edited = update.get("edited_message")
        if isinstance(edited, Mapping):
            chat = edited.get("chat") or {}
            self.windows.replace_text(
                ingress.thread_of(chat, edited),
                str(edited.get("message_id") or ""),
                str(edited.get("text") or edited.get("caption") or ""),
            )
            return
        message = update.get("message")
        if not isinstance(message, Mapping):
            return
        percept = ingress.message_percept(
            message, self_id=self.self_id, username=self.username
        )
        if percept is None:
            return
        group = message.get("media_group_id")
        if group:
            await self.collect_album(str(group), percept, agent)
            return
        await agent.handle(percept, self)

    async def answer_callback(self, callback_id: str, text: str) -> None:
        if not callback_id:
            return
        with contextlib.suppress(GatewayError):
            await self.transport.call(
                "answerCallbackQuery",
                {"callback_query_id": callback_id, "text": str(text or "")[:200]},
            )

    async def mark_decision(self, thread: str, message: Mapping) -> None:
        """Drop the keyboard, so a second click cannot repeat the same decision."""
        chat_id = thread.partition(":")[0]
        message_id = message.get("message_id")
        if not chat_id or not message_id:
            return
        with contextlib.suppress(GatewayError):
            await self.transport.call(
                "editMessageReplyMarkup",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reply_markup": {"inline_keyboard": []},
                },
            )

    async def collect_album(self, key: str, percept: Percept, agent) -> None:
        """Coalesce a `media_group_id` album into one turn (ADR-0010 §4.3)."""
        self._albums.setdefault(key, []).append(percept)
        task = self._album_tasks.get(key)
        if task is None or task.done():
            self._album_tasks[key] = asyncio.create_task(self._flush_album(key, agent))

    async def _flush_album(self, key: str, agent) -> None:
        await asyncio.sleep(ALBUM_WINDOW)
        await self.flush_album(key, agent)

    async def flush_album(self, key: str, agent) -> None:
        """Send the collected album as one percept; a test may call this directly."""
        items = self._albums.pop(key, [])
        self._album_tasks.pop(key, None)
        if not items:
            return
        first = items[0]
        merged = replace(
            first.payload,
            text="\n".join(
                item.payload.text for item in items if item.payload.text.strip()
            ),
            attachments=tuple(
                media for item in items for media in item.payload.attachments
            )[:MAX_ALBUM_ITEMS],
        )
        await agent.handle(replace(first, payload=merged), self)

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
                return await self.send_file(thread, Path(str(path or "")), voice=True)
            case "react":
                payload = action.payload if isinstance(action.payload, dict) else {}
                await self.react(
                    thread,
                    str(payload.get("message_id") or ""),
                    str(payload.get("emoji") or ""),
                )
                return ""
            case "send_sticker":
                return await self.send_sticker(thread, str(action.payload or ""))
            case "typing":
                await self.typing(thread, bool(action.payload))
                return ""
            case _:
                raise GatewayError(
                    f"body '{self.name}' does not support '{action.capability}'"
                )

    def _chat(self, thread: str) -> dict[str, object]:
        """`<chat_id>` or `<chat_id>:<topic>` -> the Bot API target fields."""
        chat_id, _, topic = str(thread or "").partition(":")
        params: dict[str, object] = {"chat_id": chat_id}
        if topic:
            params["message_thread_id"] = int(topic) if topic.isdigit() else topic
        return params

    async def send_text(
        self, thread: str, text: str, *, action: Action | None = None
    ) -> str:
        block = self.ui_of(action)
        markup = render.inline_keyboard(block)
        body_text = render.plain(str(text or ""))
        if block.get("kind") == "approval":
            card = render.approval_card(block)
            if card and card not in body_text:
                body_text = f"{body_text}\n\n{card}" if body_text.strip() else card
        parts = render.chunk(body_text)
        if not parts:
            return ""
        message_id = ""
        for index, part in enumerate(parts):
            params = {
                **self._chat(thread),
                "text": render.body("send_text", part),
                "parse_mode": "HTML",
            }
            if markup and index == len(parts) - 1:
                params["reply_markup"] = markup
            result = await self.transport.call("sendMessage", params)
            message_id = str((result or {}).get("message_id") or "") if result else ""
        return message_id

    async def send_progress(self, thread: str, action: Action) -> str:
        """Edit one status bubble per turn; the debouncer caps it at one edit per window."""
        text = progress_text(action.capability, action.payload)
        if not text:
            return ""
        key = action.replace_id or action.stream_id or f"{thread}:progress"
        message_id = self._status.get(key)
        if not message_id:
            params = {
                **self._chat(thread),
                "text": render.body(action.capability, text),
                "parse_mode": "HTML",
            }
            result = await self.transport.call("sendMessage", params)
            message_id = str((result or {}).get("message_id") or "") if result else ""
            if message_id:
                self._status[key] = message_id
            return message_id

        async def apply() -> None:
            await self.edit_text(thread, message_id, action.capability, text)

        await self.edit_debouncer().submit(key, apply)
        return message_id

    async def edit_text(
        self, thread: str, message_id: str, capability: str, text: str
    ) -> None:
        if not message_id:
            return
        with contextlib.suppress(GatewayError):
            await self.transport.call(
                "editMessageText",
                {
                    **self._chat(thread),
                    "message_id": message_id,
                    "text": render.body(capability, text),
                    "parse_mode": "HTML",
                },
            )

    async def send_file(
        self, thread: str, path: Path, caption: str = "", *, voice: bool = False
    ) -> str:
        target = Path(path).resolve()  # noqa: ASYNC240
        if not target.is_file():
            raise GatewayError(f"file not found: {path}")
        if not target.is_relative_to(self.config.workspace.resolve()):
            raise GatewayError(f"path outside the workspace: {path}")
        if target.stat().st_size > FILE_LIMIT:
            raise GatewayError("file is larger than Telegram accepts (50 MB)")
        suffix = target.suffix.lower()
        if voice or suffix in (".ogg", ".opus"):
            method, field = "sendVoice", "voice"
        elif suffix in (".mp3", ".m4a", ".wav"):
            method, field = "sendAudio", "audio"
        elif suffix in (".mp4", ".mov", ".webm"):
            method, field = "sendVideo", "video"
        elif suffix in (".jpg", ".jpeg", ".png", ".webp"):
            method, field = "sendPhoto", "photo"
        else:
            method, field = "sendDocument", "document"
        params: dict[str, object] = {**self._chat(thread), field: f"file://{target}"}
        if caption:
            params["caption"] = caption[:1024]
        result = await self.transport.call(method, params)
        return str((result or {}).get("message_id") or "") if result else ""

    async def send_sticker(self, thread: str, payload: str) -> str:
        """A sticker file inside the workspace; anything else is skipped, never invented."""
        target = Path(payload or "")
        if not target.is_absolute():
            target = self.config.workspace / target
        if not target.is_file():
            return ""
        with contextlib.suppress(GatewayError):
            result = await self.transport.call(
                "sendSticker", {**self._chat(thread), "sticker": f"file://{target}"}
            )
            return str((result or {}).get("message_id") or "") if result else ""
        return ""

    async def react(self, thread: str, message_id: str, emoji: str) -> None:
        if not message_id or not emoji:
            return
        with contextlib.suppress(GatewayError):
            await self.transport.call(
                "setMessageReaction",
                {
                    **self._chat(thread),
                    "message_id": message_id,
                    "reaction": [{"type": "emoji", "emoji": emoji}],
                },
            )

    async def typing(self, thread: str, composing: bool) -> None:
        if not composing:
            return
        with contextlib.suppress(GatewayError):
            await self.transport.call(
                "sendChatAction", {**self._chat(thread), "action": "typing"}
            )

    # -------------------------------------------------------------- media

    async def prepare_media(self, thread: str, message: Message) -> None:
        """Download every attachment, then let the speech seam transcribe the voice ones."""
        for index, media in enumerate(message.attachments):
            await self._download(thread, message.id, index, media)
        await transcribe_media(self.config, message)

    async def _download(self, thread: str, message_id: str, index: int, media) -> None:
        if media.path is not None or not media.ref:
            return
        file_id = media.ref.decode("utf-8", "replace")
        target = attachment_path(
            self.config, thread, f"{message_id}-{index}", media.filename
        )
        try:
            found = await self.transport.call("getFile", {"file_id": file_id})
        except GatewayError:
            log.warning("telegram getFile failed for %s", media.filename, exc_info=True)
            return
        file_path = str((found or {}).get("file_path") or "") if found else ""
        if not file_path:
            return
        if await self.transport.download(file_path, target):
            media.path = target
            media.size = media.size or target.stat().st_size

    # ------------------------------------------------------------ display

    def render(self, text: str) -> str:
        return render.plain(str(text or "")).strip()

    def prompt_section(self) -> str:
        return (
            "## Telegram interface\n"
            "- Use Telegram HTML: <b>bold</b>, <i>italic</i>, <code>mono</code>, and "
            "<blockquote>quotes</blockquote>. No markdown headings, no tables.\n"
            "- One message holds 4096 characters; the runtime splits longer replies.\n"
            "- A forum topic keeps its own thread; do not assume the rest of the group reads it."
        )

    def self_handles(self) -> set[str]:
        return {value for value in (self.self_id, self.username) if value}

    def chat_name(self, thread: str) -> str:
        return thread

    def private_thread(self, person_id: str) -> str:
        return private_thread_of(self.db, self.name, person_id)

    async def abort(self, thread: str) -> None:
        self._status.pop(f"{thread}:progress", None)
