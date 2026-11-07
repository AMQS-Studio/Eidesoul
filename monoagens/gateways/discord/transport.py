"""Discord transport: the `discord.py` seam, imported lazily so the extra stays optional.

`DiscordTransport` is the whole wire vocabulary the gateway uses. `DiscordPyTransport` is the
production implementation: it flattens SDK objects into the plain mappings `ingress.py` reads,
and routes them through the same handlers a test fake calls directly.
"""


import importlib
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ...engine import io
from ...engine.inbound import GatewayError

log = logging.getLogger(__name__)

INTENTS_MESSAGE = "message"
INTERACTION_COMPONENT = 3


@dataclass(frozen=True)
class Handlers:
    """What the transport calls when the platform says something."""

    ready: Callable[[], Awaitable[None]]
    message: Callable[[Mapping], Awaitable[None]]
    interaction: Callable[[Mapping], Awaitable[None]]


@runtime_checkable
class DiscordTransport(Protocol):
    """The Discord surface this body uses, and nothing more."""

    async def start(self, handlers: Handlers) -> None: ...

    async def close(self) -> None: ...

    async def send(self, channel_id: str, payload: Mapping) -> Mapping | None: ...

    async def edit(self, channel_id: str, message_id: str, payload: Mapping) -> None: ...

    async def upload(self, channel_id: str, path: Path, caption: str) -> Mapping | None: ...

    async def download(self, url: str, target: Path) -> bool: ...

    async def react(self, channel_id: str, message_id: str, emoji: str) -> None: ...

    async def sync_commands(self, commands: Sequence[Mapping]) -> None: ...


def _sdk() -> Any:
    try:
        import discord  # pyright: ignore[reportMissingImports]

        importlib.import_module("discord.app_commands")
        importlib.import_module("discord.ext.commands")
    except ImportError as exc:  # the extra is optional; the boot message names it
        raise GatewayError(
            "gateway 'discord' needs its extra: install monoagens[discord]"
        ) from exc
    return discord


def message_payload(message: Any) -> Mapping:
    """A `discord.Message` as the plain mapping `ingress.message_percept` reads."""
    reference = getattr(message, "reference", None)
    quoted = ""
    author_id = ""
    if reference is not None:
        quoted = str(getattr(reference, "message_id", "") or "")
        resolved = getattr(reference, "resolved", None)
        author_id = str(getattr(getattr(resolved, "author", None), "id", "") or "")
    return {
        "id": str(getattr(message, "id", "") or ""),
        "channel_id": str(getattr(message, "channel_id", "") or ""),
        "guild_id": str(getattr(getattr(message, "guild", None), "id", "") or ""),
        "thread_id": str(getattr(getattr(message, "channel", None), "id", "") or "")
        if getattr(getattr(message, "channel", None), "parent_id", None)
        else "",
        "author": {
            "id": str(getattr(getattr(message, "author", None), "id", "") or ""),
            "name": str(getattr(getattr(message, "author", None), "name", "") or ""),
            "bot": bool(getattr(getattr(message, "author", None), "bot", False)),
        },
        "content": str(getattr(message, "content", "") or ""),
        "timestamp": getattr(getattr(message, "created_at", None), "isoformat", lambda: "")(),
        "mentions": [
            {"id": str(getattr(item, "id", "") or "")}
            for item in (getattr(message, "mentions", None) or ())
        ],
        "attachments": [
            {
                "id": str(getattr(item, "id", "") or ""),
                "filename": str(getattr(item, "filename", "") or ""),
                "content_type": str(getattr(item, "content_type", "") or ""),
                "size": int(getattr(item, "size", 0) or 0),
                "url": str(getattr(item, "url", "") or ""),
            }
            for item in (getattr(message, "attachments", None) or ())
        ],
        "reference": {"message_id": quoted, "author_id": author_id},
    }


def interaction_payload(interaction: Any) -> Mapping | None:
    """A component `Interaction` as the plain mapping `ingress.interaction_percept` reads."""
    if int(getattr(interaction, "type", 0) or 0) != INTERACTION_COMPONENT:
        return None
    data = getattr(interaction, "data", None) or {}
    message = getattr(interaction, "message", None)
    channel = getattr(interaction, "channel", None) or getattr(message, "channel", None)
    return {
        "id": str(getattr(interaction, "id", "") or ""),
        "channel_id": str(getattr(channel, "id", "") or ""),
        "guild_id": str(getattr(getattr(interaction, "guild", None), "id", "") or ""),
        "thread_id": str(getattr(channel, "id", "") or "")
        if getattr(channel, "parent_id", None)
        else "",
        "user": {
            "id": str(getattr(getattr(interaction, "user", None), "id", "") or ""),
            "name": str(getattr(getattr(interaction, "user", None), "name", "") or ""),
        },
        "data": {
            "custom_id": str(data.get("custom_id") or ""),
            "values": list(data.get("values") or []),
        },
        "message": {"id": str(getattr(message, "id", "") or "")},
    }


def _write_bytes(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


class DiscordPyTransport:
    """The production transport: one `discord.py` client, lazily imported."""

    def __init__(self, token: str) -> None:
        self.token = str(token or "").strip()
        self._client: Any = None
        self._handlers: Handlers | None = None

    async def start(self, handlers: Handlers) -> None:
        if not self.token:
            raise GatewayError("gateway 'discord': no bot token is configured")
        discord = _sdk()
        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.ext.commands.Bot(command_prefix="!", intents=intents)
        self._client = client
        self._handlers = handlers

        @client.event
        async def on_ready() -> None:
            await handlers.ready()

        @client.event
        async def on_message(message: Any) -> None:
            if getattr(message.author, "bot", False):
                return
            await handlers.message(message_payload(message))

        @client.event
        async def on_interaction(interaction: Any) -> None:
            payload = interaction_payload(interaction)
            if payload is not None:
                await handlers.interaction(payload)

        await client.start(self.token)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def _channel(self, channel_id: str) -> Any:
        if self._client is None:
            raise GatewayError("discord client is not running")
        target = int(channel_id)
        found = self._client.get_channel(target)
        return found if found is not None else await self._client.fetch_channel(target)

    def _view(self, payload: Mapping) -> Any:
        rows = payload.get("components") or ()
        if not rows:
            return None
        discord = _sdk()
        view = discord.ui.View(timeout=None)
        for row in rows:
            for item in (row or {}).get("components") or ():
                view.add_item(
                    discord.ui.Button(
                        style=item.get("style", 2),
                        label=item.get("label", ""),
                        custom_id=item.get("custom_id", ""),
                    )
                )
        return view

    async def send(self, channel_id: str, payload: Mapping) -> Mapping | None:
        channel = await self._channel(channel_id)
        embed = payload.get("embed")
        sent = await channel.send(
            content=payload.get("content") or None,
            embed=_sdk().Embed.from_dict(embed) if embed else None,
            view=self._view(payload),
        )
        return {"id": str(getattr(sent, "id", "") or "")}

    async def edit(self, channel_id: str, message_id: str, payload: Mapping) -> None:
        channel = await self._channel(channel_id)
        message = await channel.fetch_message(int(message_id))
        embed = payload.get("embed")
        await message.edit(
            content=payload.get("content") or None,
            embed=_sdk().Embed.from_dict(embed) if embed else None,
            view=self._view(payload),
        )

    async def upload(self, channel_id: str, path: Path, caption: str) -> Mapping | None:
        channel = await self._channel(channel_id)
        sent = await channel.send(
            content=caption or None, file=_sdk().File(str(path), filename=path.name)
        )
        return {"id": str(getattr(sent, "id", "") or "")}

    async def react(self, channel_id: str, message_id: str, emoji: str) -> None:
        channel = await self._channel(channel_id)
        message = await channel.fetch_message(int(message_id))
        await message.add_reaction(str(emoji))

    async def download(self, url: str, target: Path) -> bool:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(url)
            if response.status_code != 200 or not response.content:
                return False
            await io.run(_write_bytes, target, response.content)
        except Exception:
            log.warning("discord download failed for %s", target.name, exc_info=True)
            return False
        return True

    async def sync_commands(self, commands: Sequence[Mapping]) -> None:
        """Register the engine catalog as Discord application commands, then sync the tree."""
        client = self._client
        handlers = self._handlers
        tree = getattr(client, "tree", None)
        if tree is None or handlers is None:
            raise GatewayError("discord client is not ready for a command sync")
        discord = _sdk()
        added = 0
        for entry in commands:
            name = str(entry.get("command") or "").strip().lower()
            if not name or tree.get_command(name) is not None:
                continue
            description = str(entry.get("description") or name).strip()[:100] or name
            tree.add_command(
                discord.app_commands.Command(
                    name=name[:32],
                    description=description,
                    callback=self._slash_callback(name, handlers),
                )
            )
            added += 1
        await tree.sync()
        total = len(tree.get_commands())
        log.info("discord slash commands synced: %d new, %d total", added, total)

    def _slash_callback(self, name: str, handlers: Handlers) -> Any:
        """One slash invocation becomes the same interaction payload a button produces."""

        async def callback(interaction: Any, argument: str = "") -> None:
            payload = interaction_payload(interaction) or {}
            command = f"/{name} {argument}".strip()
            payload = {
                **payload,
                "data": {"custom_id": f"cmd:{command}", "values": []},
                "message": {"id": str(getattr(interaction, "id", "") or "")},
            }
            await handlers.interaction(payload)

        return callback
