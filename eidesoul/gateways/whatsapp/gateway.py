"""WhatsApp gateway: Neonize lifecycle, sending/receiving, directory, and media."""


import asyncio
import contextlib
import logging
import re
import secrets
import time
from pathlib import Path

from neonize.aioze.client import NewAClient
from neonize.aioze.events import ConnectedEv, GroupInfoEv, HistorySyncEv, MessageEv
from neonize.exc import SendMessageError
from neonize.proto.Neonize_pb2 import UndecryptableMessage as UndecryptableMessageEv
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import ContextInfo, ExtendedTextMessage
from neonize.utils.enum import ChatPresence, ChatPresenceMedia
from neonize.utils.jid import build_jid

from ...engine.config import Config, GatewaySettings, secure_db
from ...engine.context import Message, human_size
from ...engine.inbound import GatewayError, Percept
from ...engine.registry import Handler, discover
from ...engine.reply import FENCE, clean_prose, strip_blocks
from ...engine.tools import FAMILIES
from . import directory, history_sync, ingress
from .config import WhatsAppSettings
from .media import download_media, send_kind
from .profile import avatar_bytes

log = logging.getLogger(__name__)

MAX_SEND_BYTES = 100 * 1024 * 1024
GROUP_INFO_TIMEOUT = 10.0
GROUP_NAME_RETRY = 300.0
REACTION_COOLDOWN = 300.0
SENT_TTL = 900.0
MENTION_TOKEN = re.compile(r"@\+?\d[\d\s().-]{2,24}\d")
MENTION_NAME = re.compile(r"(?<![\w@])@([^\W\d_][\w.]{1,39})", re.UNICODE)


class WhatsAppGateway:
    name = "whatsapp"

    def __init__(
        self,
        config: Config,
        db,
        history,
        windows,
        *,
        settings: GatewaySettings | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.history = history
        self.windows = windows
        raw = settings or config.gateways.get("whatsapp") or GatewaySettings("whatsapp")
        self.settings = WhatsAppSettings.from_mapping(
            raw.enabled, raw.options, config.paths.config.parent
        )
        self.client: NewAClient | None = None
        self.me = None
        self.reactions: dict[str, float] = {}
        self.name_retry: dict[str, float] = {}
        self._sent: dict[str, float] = {}
        self.backfill = None
        self._stopping = False
        base = Path(__file__).parent
        self._tools = discover(__package__ + ".tools", base / "tools", "TOOL", "tool", FAMILIES)
        self._commands = discover(
            __package__ + ".commands", base / "commands", "COMMAND", "command"
        )

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    @property
    def backlog_seconds(self) -> float:
        return self.settings.backlog_seconds

    def tools(self) -> list[Handler]:
        return list(self._tools)

    def commands(self) -> list[Handler]:
        return list(self._commands)

    # ------------------------------------------------------------ lifecycle

    async def run(self, agent) -> None:
        session = self.settings.session_db
        self.client = NewAClient(
            str(session),
            props=history_sync.history_props(history_sync.needs_full_sync(self.db)),
        )
        client = self.client

        @client.event(MessageEv)
        async def on_message(accepted: NewAClient, ev) -> None:
            self.me = accepted.me or self.me
            try:
                if ev.Info.MessageSource.Chat.Server == "g.us":
                    await self.ensure_group_name(ev.Info.MessageSource.Chat.User + "@g.us")
                revoked = ingress.revoke_target(ev, self)
                if revoked is not None:
                    target_id, by_address = revoked
                    source = ev.Info.MessageSource
                    thread = f"{source.Chat.User}@{source.Chat.Server}"
                    name = directory.contact_name(self.db, by_address) or (
                        ev.Info.Pushname or ""
                    ).strip()
                    if self.windows.mark_deleted(thread, target_id, by_address, name):
                        log.info(
                            "message deleted chat=%s id=%s by=%s",
                            thread,
                            target_id,
                            name or by_address,
                        )
                    return
                inbound = ingress.event_to_inbound(ev, self)
                if inbound is None:
                    return
                await agent.handle(inbound, self)
            except Exception:
                log.exception("WhatsApp message handler failed")

        @client.event(UndecryptableMessageEv)
        async def on_undecryptable(accepted: NewAClient, ev) -> None:
            if not ev.IsUnavailable:
                return
            try:
                found = ingress.unreadable_message(ev, self)
                if found is None:
                    return
                thread, message = found
                self.windows.append(thread, message)
                log.info("unreadable message chat=%s id=%s", thread, message.id)
            except Exception:
                log.exception("unreadable message handler failed")

        @client.event(HistorySyncEv)
        async def on_history(accepted: NewAClient, ev) -> None:
            if ev.Data is None:
                return
            try:
                count, thread_ids, done = history_sync.ingest_sync(
                    self.db, self.history, ev.Data, ingress.self_jid(self.me)
                )
                for thread in thread_ids:
                    self.windows.invalidate(thread)
                log.info(
                    "history sync: %d messages, %d threads, sync=%s progress=%d%s",
                    count,
                    len(thread_ids),
                    ev.Data.syncType,
                    ev.Data.progress,
                    " (done)" if done else "",
                )
            except Exception:
                log.exception("history sync ingest failed")

        @client.event(GroupInfoEv)
        async def on_group_info(accepted: NewAClient, ev) -> None:
            if not ev.JID.User:
                return
            name = getattr(ev.Name, "Name", "").strip()
            if name:
                directory.set_chat_name(self.db, f"{ev.JID.User}@{ev.JID.Server}", name)

        @client.event(ConnectedEv)
        async def on_connected(accepted: NewAClient, ev) -> None:
            self.me = accepted.me or self.me
            secure_db(session)
            try:
                await asyncio.to_thread(directory.import_contacts, self.db, session)
            except Exception:
                log.exception("failed to refresh contacts")
            try:
                named = await directory.sync_group_names(accepted, self.db)
                if named:
                    log.info("group names synced: %d", named)
            except Exception:
                log.exception("failed to sync group names")
            log.info("Eidesoul connected to WhatsApp")

        log.info("WhatsApp: preparing session %s", session)
        await client.connect()
        secure_db(session)
        await client.idle()

    async def stop(self) -> None:
        self._stopping = True
        if self.client is not None:
            with contextlib.suppress(Exception):
                await self.client.stop()
            self.client = None

    # ---------------------------------------------------------------- send

    def _jid(self, thread: str):
        user, _, server = thread.partition("@")
        return build_jid(user, server)

    async def avatar(self, jid: str) -> tuple[bytes, str] | None:
        """Profile picture used as a fallback gender signal; None without a client or a photo."""
        if self.client is None:
            return None
        return await avatar_bytes(self.client, self.config, jid)

    def remember_sent(self, message_id: str) -> None:
        """Record an ID the bot sent; a `fromMe` echo with this ID is the bot, not the owner."""
        if not message_id:
            return
        now = time.monotonic()
        self._sent = {key: stamp for key, stamp in self._sent.items() if now - stamp < SENT_TTL}
        self._sent[message_id] = now

    def is_bot_message(self, message_id: str) -> bool:
        stamp = self._sent.get(message_id)
        return stamp is not None and time.monotonic() - stamp < SENT_TTL

    def _resolve_mentions(self, text: str, thread: str) -> tuple[str, list[str]]:
        if "@" not in text:
            return text, []
        known: dict[str, str] = {}
        names: dict[str, str] = {}
        for entry in self.windows.get(thread):
            if not entry.sender_id or not entry.sender_address:
                continue
            known.setdefault(entry.sender_id, entry.sender_address)
            label = (entry.sender_name or "").strip().casefold()
            if not label:
                continue
            for key in (label, label.split()[0]):
                current = names.get(key)
                if current is None:
                    names[key] = entry.sender_id
                elif current != entry.sender_id:
                    names[key] = ""  # ambiguous: more than one person owns that name
        if not known:
            return text, []
        mentions: list[str] = []

        def number(match: re.Match) -> str:
            token = match.group(0)
            digits = "".join(ch for ch in token if ch.isdigit())
            jid = known.get(digits)
            if jid is None:
                log.warning("unrecognized mention chat=%s token=%s", thread, token)
                return token
            if jid not in mentions:
                mentions.append(jid)
            return f"@{digits}"

        def name(match: re.Match) -> str:
            digits = names.get(match.group(1).casefold())
            jid = known.get(digits or "")
            if jid is None:
                return match.group(0)
            if jid not in mentions:
                mentions.append(jid)
            return f"@{digits}"

        return MENTION_NAME.sub(name, MENTION_TOKEN.sub(number, text)), mentions

    async def send_text(
        self, thread: str, text: str, *, reply: Percept | None = None
    ) -> str:
        if self.client is None:
            raise GatewayError("WhatsApp client is not available")
        body, mentions = self._resolve_mentions(str(text or ""), thread)
        payload = (
            ExtendedTextMessage(text=body, contextInfo=ContextInfo(mentionedJID=mentions))
            if mentions
            else body
        )
        try:
            if reply is not None and reply.raw is not None:
                sent = await self.client.reply_message(payload, reply.raw)
            else:
                sent = await self.client.send_message(self._jid(thread), payload)
        except SendMessageError as exc:
            log.exception("failed to send text to %s", thread)
            raise GatewayError(str(exc)) from exc
        except Exception as exc:
            log.exception("failed to send text to %s", thread)
            raise GatewayError(f"{type(exc).__name__}: {exc}") from exc
        message_id = getattr(sent, "ID", "") or f"text-{time.time_ns()}"
        self.remember_sent(message_id)
        return message_id

    async def send_file(
        self,
        thread: str,
        path: Path,
        caption: str = "",
        *,
        reply: Percept | None = None,
    ) -> str:
        if self.client is None:
            raise GatewayError("WhatsApp client is not available")
        # A local stat before an upload: microseconds, and the body has no io pool.
        target = Path(path).resolve()  # noqa: ASYNC240
        if not target.is_file():
            raise GatewayError(f"file not found: {path}")
        if not target.is_relative_to(self.config.workspace.resolve()):
            raise GatewayError(f"path outside the workspace: {path}")
        size = target.stat().st_size
        if size > MAX_SEND_BYTES:
            raise GatewayError(
                f"file too large ({human_size(size)}; max {human_size(MAX_SEND_BYTES)})"
            )
        kind = send_kind(target)
        jid = self._jid(thread)
        try:
            if kind == "sticker":
                sent = await self.client.send_sticker(jid, str(target))
                if caption:
                    await self.client.send_message(jid, caption)
            elif kind == "photo":
                sent = await self.client.send_image(jid, str(target), caption=caption or None)
            else:
                sent = await self.client.send_document(
                    jid, str(target), caption=caption or None, filename=target.name
                )
        except Exception as exc:
            log.exception("failed to send file %s to %s", target, thread)
            raise GatewayError(f"{type(exc).__name__}: {exc}") from exc
        message_id = getattr(sent, "ID", "") or f"file-{time.time_ns()}"
        self.remember_sent(message_id)
        return message_id

    def _sticker_paths(self) -> list[Path]:
        found: list[Path] = []
        for name in self.config.humanize.reaction_stickers:
            path = Path(str(name)).expanduser()
            path = path if path.is_absolute() else self.config.workspace / path
            try:
                if path.is_file() and send_kind(path) == "sticker":
                    found.append(path)
            except OSError:
                continue
        return found

    async def send_sticker(self, thread: str, message_id: str) -> str | None:
        if self.client is None or not self.config.humanize.reaction_stickers:
            return None
        moment = time.time()
        if moment - self.reactions.get(thread, 0.0) < REACTION_COOLDOWN:
            return None
        paths = self._sticker_paths()
        if not paths:
            log.info("reaction skipped: no reaction sticker chat=%s", thread)
            return None
        path = secrets.choice(paths)
        try:
            sent = await self.client.send_sticker(self._jid(thread), str(path))
        except Exception:
            log.warning("failed to send the reaction sticker chat=%s", thread, exc_info=True)
            return None
        self.remember_sent(getattr(sent, "ID", ""))
        self.reactions[thread] = moment
        return f"(reaction sticker: {path.name})"

    async def typing(self, thread: str, composing: bool) -> None:
        if self.client is None:
            return
        try:
            await self.client.send_chat_presence(
                self._jid(thread),
                ChatPresence.CHAT_PRESENCE_COMPOSING
                if composing
                else ChatPresence.CHAT_PRESENCE_PAUSED,
                ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT,
            )
        except Exception:
            log.debug("presence update failed", exc_info=True)

    async def prepare_media(self, thread: str, message: Message) -> None:
        if message.media is not None:
            await download_media(self.client, self.config, thread, message.id, message.media)
        if message.quote is not None and message.quote.media is not None:
            quote_id = message.quote.id or f"{message.id}-quote"
            await download_media(self.client, self.config, thread, quote_id, message.quote.media)

    # -------------------------------------------------------------- display

    def render(self, text: str) -> str:
        pieces = FENCE.split(strip_blocks(str(text or "")))
        cleaned = "".join(
            piece if piece.startswith("```") else clean_prose(piece) for piece in pieces
        )
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    def prompt_hints(self) -> str:
        handles = ", ".join(sorted(self.self_handles())) or "-"
        return (
            "## WhatsApp interface\n"
            "- Use WhatsApp formatting: *bold*, _italic_, ~strike~, ```mono```. No markdown "
            "headings, no tables.\n"
            "- Start list items with '• '; never use '- ' or numbers with dots for bullets.\n"
            "- To mention someone, copy the `mention=@…` token from their payload line verbatim "
            "(no '+', spaces, or punctuation); the runtime converts it into a real mention. "
            "Never invent a number.\n"
            f"- Your handles: {handles}. A mention of any of them is meant for you, not for "
            "another person. When you refer to yourself, use those handles.\n"
            "- Do not @mention someone in a DM unless it is genuinely necessary."
        )

    def self_handles(self) -> set[str]:
        return ingress.self_handles(self.me)

    def chat_name(self, thread: str) -> str:
        return directory.chat_name(self.db, thread)

    async def ensure_group_name(self, thread: str) -> None:
        if directory.chat_name(self.db, thread):
            return
        moment = time.monotonic()
        if moment - self.name_retry.get(thread, 0.0) < GROUP_NAME_RETRY:
            return
        self.name_retry[thread] = moment
        getter = getattr(self.client, "get_group_info", None)
        if not callable(getter):
            return
        try:
            info = await asyncio.wait_for(getter(self._jid(thread)), GROUP_INFO_TIMEOUT)
        except Exception:
            log.debug("failed to fetch the group name %s", thread, exc_info=True)
            return
        name = directory.group_name(info)
        if name:
            directory.set_chat_name(self.db, thread, name)

    def resolve_target(self, query: str):
        targets = directory.targets(self.db, query, 50)
        return directory.resolve_target(targets, query)

    def participants(self, thread: str) -> set[str] | None:
        return directory.participants(self.db, thread)
