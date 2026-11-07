"""Telegram transport: the Bot API over `httpx`, and the one seam a test can fake.

`BotAPI` is the whole wire vocabulary a body needs: one `call` per method, one `download` for a
file, one `close`. `HttpBotAPI` is the production implementation; a test hands in any object
with the same three methods and never touches the network.
"""


import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from ...engine import io
from ...engine.inbound import GatewayError

log = logging.getLogger(__name__)

API_ROOT = "https://api.telegram.org"
FILE_ROOT = "https://api.telegram.org/file"
TIMEOUT = 60.0


def _write_bytes(target: Path, content: bytes) -> None:
    """One download's write, off the event loop (ADR-0003 §4.1)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


@runtime_checkable
class BotAPI(Protocol):
    """The Bot API surface this body uses, and nothing more."""

    async def call(self, method: str, params: Mapping[str, object] | None = None) -> Any: ...

    async def download(self, file_path: str, target: Path) -> bool: ...

    async def close(self) -> None: ...


class HttpBotAPI:
    """The Bot API over one pooled `httpx.AsyncClient`; the token never appears in a log line."""

    def __init__(
        self,
        token: str,
        *,
        api_root: str = API_ROOT,
        file_root: str = FILE_ROOT,
        timeout: float = TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.token = str(token or "").strip()
        self.api_root = str(api_root).rstrip("/")
        self.file_root = str(file_root).rstrip("/")
        self._client = client
        self._timeout = float(timeout)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def call(self, method: str, params: Mapping[str, object] | None = None) -> Any:
        if not self.token:
            raise GatewayError("gateway 'telegram': no bot token is configured")
        url = f"{self.api_root}/bot{self.token}/{method}"
        try:
            response = await self._http().post(url, json=dict(params or {}))
            payload = response.json()
        except Exception as exc:  # a transport failure is safe to show, the token is not
            raise GatewayError(f"telegram {method} failed: {type(exc).__name__}") from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            reason = ""
            if isinstance(payload, dict):
                reason = str(payload.get("description") or "")
            raise GatewayError(f"telegram {method} refused: {reason or 'unknown error'}")
        return payload.get("result")

    async def download(self, file_path: str, target: Path) -> bool:
        """Fetch one file by its `file_path`; False on any failure, never an exception."""
        if not self.token or not file_path:
            return False
        url = f"{self.file_root}/bot{self.token}/{file_path}"
        try:
            response = await self._http().get(url)
            if response.status_code != 200 or not response.content:
                return False
            await io.run(_write_bytes, target, response.content)
        except Exception:
            log.warning("telegram download failed for %s", target.name, exc_info=True)
            return False
        return True

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
