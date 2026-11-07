"""Telegram webhook mode: a minimal `asyncio` listener for one POST per update.

The listener terminates nothing: an operator fronts it with a TLS reverse proxy, which is the
normal self-hosted deployment. `parse_request` is pure, so the refusal rules (method, secret
header, JSON body) are tested without a socket.
"""


import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping

log = logging.getLogger(__name__)

HEAD_LIMIT = 64 * 1024
BODY_LIMIT = 8 * 1024 * 1024
SECRET_HEADER = "x-telegram-bot-api-secret-token"
OK = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
DENIED = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
BAD = b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


def parse_request(head: bytes, body: bytes, secret: str = "") -> dict | None:
    """The JSON update of one request, or None when the request must be refused."""
    text = head.decode("latin-1", "replace")
    request = text.split("\r\n", 1)[0].split()
    if len(request) < 2 or request[0].upper() != "POST":
        return None
    headers: dict[str, str] = {}
    for line in text.split("\r\n")[1:]:
        name, _, value = line.partition(":")
        if name.strip():
            headers[name.strip().lower()] = value.strip()
    if secret and headers.get(SECRET_HEADER) != str(secret):
        return None
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


async def _read_body(reader: asyncio.StreamReader, head: bytes) -> bytes:
    text = head.decode("latin-1", "replace")
    length = 0
    for line in text.split("\r\n")[1:]:
        name, _, value = line.partition(":")
        if name.strip().lower() == "content-length":
            with contextlib.suppress(ValueError):
                length = int(value.strip())
    if length <= 0:
        return b""
    return await reader.readexactly(min(length, BODY_LIMIT))


async def handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    handler: Callable[[Mapping], Awaitable[None]],
    secret: str = "",
) -> None:
    """Read one request, answer it, and hand the update to `handler`."""
    try:
        head = await reader.readuntil(b"\r\n\r\n")
        if len(head) > HEAD_LIMIT:
            writer.write(DENIED)
            return
        body = await _read_body(reader, head)
        payload = parse_request(head, body, secret)
        if payload is None:
            writer.write(DENIED if secret else BAD)
            await writer.drain()
            return
        writer.write(OK)
        await writer.drain()
        await handler(payload)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        return
    except Exception:
        log.warning("telegram webhook request failed", exc_info=True)
    finally:
        with contextlib.suppress(Exception):
            writer.close()


async def serve(
    handler: Callable[[Mapping], Awaitable[None]],
    *,
    host: str,
    port: int,
    secret: str = "",
) -> None:
    """Serve webhook updates until the task is cancelled."""
    server = await asyncio.start_server(
        lambda reader, writer: handle_connection(reader, writer, handler, secret),
        host,
        port,
    )
    log.info("telegram webhook listening on %s:%d", host, port)
    async with server:
        await server.serve_forever()
