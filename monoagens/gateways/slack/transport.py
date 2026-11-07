"""Slack transport: Socket Mode / Events API behind a narrow, fakeable seam.

`slack_bolt` and `slack_sdk` are imported lazily inside `SlackBoltTransport.start`, so the extra
stays optional. The transport flattens each Bolt body into the plain mappings `ingress.py` reads
and calls the same handlers a test fake calls directly.
"""


import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ...engine import io
from ...engine.inbound import GatewayError

log = logging.getLogger(__name__)

ACTION_PREFIX = "cmd:"
STATUS_LIMIT = 200


@dataclass(frozen=True)
class Handlers:
    """What the transport calls when the platform says something."""

    ready: Callable[[], Awaitable[None]]
    message: Callable[[Mapping], Awaitable[None]]
    action: Callable[[Mapping], Awaitable[None]]


@runtime_checkable
class SlackTransport(Protocol):
    """The Slack surface this body uses, and nothing more."""

    async def start(self, handlers: Handlers) -> None: ...

    async def close(self) -> None: ...

    async def post(
        self,
        channel: str,
        text: str,
        blocks: Sequence[Mapping] | None = None,
        thread_ts: str = "",
    ) -> Mapping | None: ...

    async def update(
        self, channel: str, ts: str, text: str, blocks: Sequence[Mapping] | None = None
    ) -> None: ...

    async def upload(
        self, channel: str, path: Path, caption: str, thread_ts: str = ""
    ) -> Mapping | None: ...

    async def download(self, url: str, target: Path) -> bool: ...

    async def react(self, channel: str, ts: str, emoji: str) -> None: ...

    async def set_status(self, channel: str, thread_ts: str, status: str) -> None: ...


def _write_bytes(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def flatten(body: Mapping) -> Mapping:
    """A Bolt event body -> the mapping `ingress.message_percept` reads."""
    event = body.get("event")
    if not isinstance(event, Mapping):
        return dict(body)
    return {
        **dict(event),
        "channel_type": event.get("channel_type") or body.get("channel_type") or "",
        "team": body.get("team_id") or event.get("team") or "",
        "event_ts": body.get("event_ts") or event.get("event_ts") or "",
    }


class SlackBoltTransport:
    """The production transport: one Bolt `AsyncApp`, lazily imported."""

    def __init__(self, bot_token: str, app_token: str = "", mode: str = "socket") -> None:
        self.bot_token = str(bot_token or "").strip()
        self.app_token = str(app_token or "").strip()
        self.mode = str(mode or "socket")
        self._app: Any = None
        self._handler: Any = None
        self._client: Any = None
        self.bot_user_id = ""

    async def start(self, handlers: Handlers) -> None:
        if not self.bot_token:
            raise GatewayError("gateway 'slack': no bot token is configured")
        try:
            from slack_bolt.async_app import AsyncApp  # pyright: ignore[reportMissingImports]
        except ImportError as exc:
            raise GatewayError(
                "gateway 'slack' needs its extra: install monoagens[slack]"
            ) from exc
        app = AsyncApp(token=self.bot_token)
        self._app = app
        self._client = app.client

        @app.event("message")
        async def on_message(body: Mapping) -> None:
            await handlers.message(flatten(body))

        @app.event("app_mention")
        async def on_mention(body: Mapping) -> None:
            await handlers.message(flatten(body))

        @app.action({"type": "block_actions"})
        async def on_action(ack, body: Mapping) -> None:
            await ack()
            await handlers.action(dict(body))

        try:
            identity = await app.client.auth_test()
            self.bot_user_id = str(identity.get("user_id") or "")
        except Exception:
            log.warning("slack auth.test failed; mentions stay textual", exc_info=True)
        await handlers.ready()
        if self.mode == "socket":
            if not self.app_token:
                raise GatewayError("gateway 'slack': Socket Mode needs SLACK_APP_TOKEN")
            try:
                from slack_bolt.adapter.socket_mode.async_handler import (  # pyright: ignore
                    AsyncSocketModeHandler,
                )
            except ImportError as exc:
                raise GatewayError("gateway 'slack': Socket Mode adapter is missing") from exc
            self._handler = AsyncSocketModeHandler(app, self.app_token)
            await self._handler.start_async()
            return
        raise GatewayError(
            "gateway 'slack': events mode needs an HTTP adapter; run Socket Mode instead"
        )

    async def close(self) -> None:
        if self._handler is not None:
            await self._handler.close_async()
            self._handler = None
        self._app = None
        self._client = None

    async def post(
        self,
        channel: str,
        text: str,
        blocks: Sequence[Mapping] | None = None,
        thread_ts: str = "",
    ) -> Mapping | None:
        if self._client is None:
            raise GatewayError("slack client is not running")
        params: dict[str, object] = {"channel": channel, "text": text or " "}
        if blocks:
            params["blocks"] = list(blocks)
        if thread_ts:
            params["thread_ts"] = thread_ts
        found = await self._client.chat_postMessage(**params)
        return {"ts": str(found.get("ts") or "")} if isinstance(found, Mapping) else None

    async def update(
        self, channel: str, ts: str, text: str, blocks: Sequence[Mapping] | None = None
    ) -> None:
        if self._client is None:
            raise GatewayError("slack client is not running")
        params: dict[str, object] = {"channel": channel, "ts": ts, "text": text or " "}
        if blocks:
            params["blocks"] = list(blocks)
        await self._client.chat_update(**params)

    async def upload(
        self, channel: str, path: Path, caption: str, thread_ts: str = ""
    ) -> Mapping | None:
        if self._client is None:
            raise GatewayError("slack client is not running")
        found = await self._client.files_upload_v2(
            channel=channel,
            file=str(path),
            filename=path.name,
            initial_comment=caption or None,
            thread_ts=thread_ts or None,
        )
        return {"ts": str(found.get("ts") or "")} if isinstance(found, Mapping) else None

    async def download(self, url: str, target: Path) -> bool:
        import httpx

        if not url:
            return False
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(
                    url, headers={"Authorization": f"Bearer {self.bot_token}"}
                )
            if response.status_code != 200 or not response.content:
                return False
            await io.run(_write_bytes, target, response.content)
        except Exception:
            log.warning("slack download failed for %s", target.name, exc_info=True)
            return False
        return True

    async def react(self, channel: str, ts: str, emoji: str) -> None:
        if self._client is None:
            raise GatewayError("slack client is not running")
        await self._client.reactions_add(
            channel=channel, timestamp=ts, name=str(emoji).strip(":")
        )

    async def set_status(self, channel: str, thread_ts: str, status: str) -> None:
        if self._client is None or not thread_ts:
            return
        await self._client.assistant_threads_setStatus(
            channel_id=channel, thread_ts=thread_ts, status=str(status or "")[:STATUS_LIMIT]
        )
