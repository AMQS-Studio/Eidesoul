"""WhatsApp profile avatar: a fallback gender signal, not the primary one.

Only used when `engine.character.gender_avatar` is on and the typing style gives no answer.
One download per person per 30 days, 2 MB at most, stored under `workspace/avatars/` with
mode 0600. Every failure means "unknown".
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

from neonize.utils.jid import build_jid

from ...engine.config import Config

log = logging.getLogger(__name__)

AVATAR_TTL = 30 * 24 * 3600
AVATAR_MAX_BYTES = 2_000_000
AVATAR_TIMEOUT = 10.0


def avatar_path(config: Config, jid: str) -> Path:
    digest = hashlib.sha256(str(jid or "").encode("utf-8")).hexdigest()[:24]
    return Path(config.workspace) / "avatars" / f"{digest}.bin"


def sniff_mime(raw: bytes) -> str:
    """Guess the image type from magic bytes; anything else is treated as unknown."""
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _fresh(path: Path, now: float) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0 and now - path.stat().st_mtime < AVATAR_TTL
    except OSError:
        return False


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "eidesoul"})
    with urllib.request.urlopen(request, timeout=AVATAR_TIMEOUT) as response:
        return response.read(AVATAR_MAX_BYTES + 1)


async def avatar_bytes(client, config: Config, jid: str) -> tuple[bytes, str] | None:
    """(image bytes, mime) or None; never raises to the caller."""
    if client is None or not str(jid or "").strip():
        return None
    target = avatar_path(config, jid)
    if _fresh(target, time.time()):
        try:
            raw = target.read_bytes()
        except OSError:
            raw = b""
        mime = sniff_mime(raw)
        if raw and mime:
            return (raw, mime)
    try:
        user, _, server = str(jid).partition("@")
        info = await client.get_profile_picture(build_jid(user, server or "s.whatsapp.net"))
    except Exception:
        log.debug("avatar: get_profile_picture failed for %s", jid[-6:], exc_info=True)
        return None
    url = str(getattr(info, "URL", "") or "")
    if not url:
        return None
    try:
        raw = await asyncio.to_thread(_download, url)
    except (OSError, urllib.error.URLError, TimeoutError):
        log.debug("avatar: download failed for %s", jid[-6:], exc_info=True)
        return None
    mime = sniff_mime(raw)
    if not raw or len(raw) > AVATAR_MAX_BYTES or not mime:
        return None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        target.chmod(0o600)
    except OSError:
        log.debug("avatar: failed to cache the file", exc_info=True)
    return (raw, mime)
