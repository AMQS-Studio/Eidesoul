"""WhatsApp media: file content detection, attachment download, and message proto reads."""


import contextlib
import logging
import mimetypes
from pathlib import Path

from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message
from neonize.utils import extract_text

from ...engine.config import Config
from ...engine.turn.context import Media, Quote, clean, human_size

log = logging.getLogger(__name__)

PREVIEW_LINES = 12
PREVIEW_CHARS = 1500
INLINE_TEXT_LINES = 80
INLINE_TEXT_CHARS = 6000
INLINE_TEXT_BYTES = 512 * 1024
MAX_IMAGES = 6
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_TOTAL = 10 * 1024 * 1024
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MIME_ALIASES = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg", "image/x-png": "image/png"}
MEDIA_FIELDS = ("imageMessage", "videoMessage", "audioMessage", "documentMessage", "stickerMessage")
WRAPPERS = (
    "viewOnceMessage",
    "viewOnceMessageV2",
    "viewOnceMessageV2Extension",
    "ephemeralMessage",
    "documentWithCaptionMessage",
)
FALLBACK_MIME = {
    "image": "image/jpeg",
    "video": "video/mp4",
    "audio": "audio/ogg",
    "document": "application/octet-stream",
    "sticker": "image/webp",
}
EXT_FOR_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "video/mp4": ".mp4",
    "text/plain": ".txt",
    "application/json": ".json",
    "application/zip": ".zip",
}
PHOTO_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")
STICKER_MAX_SIDE = 512
VOICE_EXT = frozenset({".ogg", ".opus"})
AUDIO_EXT = frozenset({".mp3", ".m4a", ".wav", ".aac", ".flac"})
VIDEO_EXT = frozenset({".mp4", ".mov", ".webm", ".mkv"})


def _webp_shape(head: bytes) -> tuple[int, int, bool] | None:
    if len(head) < 30 or head[:4] != b"RIFF" or head[8:12] != b"WEBP":
        return None
    chunk = head[12:16]
    if chunk == b"VP8X":
        flags = head[20]
        width = int.from_bytes(head[24:27], "little") + 1
        height = int.from_bytes(head[27:30], "little") + 1
        return width, height, bool(flags & 0x12)
    if chunk == b"VP8 ":
        width = int.from_bytes(head[26:28], "little") & 0x3FFF
        height = int.from_bytes(head[28:30], "little") & 0x3FFF
        return width, height, False
    if chunk == b"VP8L":
        bits = int.from_bytes(head[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1, bool(bits >> 28)
    return None


def send_kind(path: Path) -> str:
    """Classify a file for the send path: `sticker`, `photo`, `voice`, `audio`, `video`, `file`.

    Image kinds come from the bytes, because a WebP sticker is not a photo and a JPEG is never
    a sticker. Voice notes, plain audio, and video are recognised by extension, which is the
    same fact the platform routes on.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(30)
    except OSError:
        head = b""
    if head.startswith(PHOTO_MAGIC):
        return "photo"
    shape = _webp_shape(head) if head else None
    if shape is not None:
        width, height, marked = shape
        return "sticker" if marked and max(width, height) <= STICKER_MAX_SIDE else "photo"
    suffix = Path(path).suffix.lower()
    if suffix in VOICE_EXT:
        return "voice"
    if suffix in AUDIO_EXT:
        return "audio"
    if suffix in VIDEO_EXT:
        return "video"
    return "file"


def sanitize(value: str, limit: int = 60) -> str:
    cleaned = "".join(
        ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_" for ch in str(value)
    )
    return cleaned.strip("._-")[:limit] or "x"


VIEW_ONCE_WRAPPERS = ("viewOnceMessage", "viewOnceMessageV2", "viewOnceMessageV2Extension")


def views_once(message) -> bool:
    for name in VIEW_ONCE_WRAPPERS:
        wrapper = getattr(message, name, None)
        if wrapper is not None and wrapper.ListFields():
            return True
    return False


def unwrap_message(message):
    for _ in range(5):
        inner = None
        for name in WRAPPERS:
            wrapper = getattr(message, name, None)
            if wrapper is not None and wrapper.ListFields():
                inner = wrapper.message
                break
        if inner is None:
            break
        message = inner
    return message


def contexts(message):
    body = unwrap_message(message)
    for field_name in ("extendedTextMessage", *MEDIA_FIELDS):
        content = getattr(body, field_name, None)
        if content is None or not content.ListFields():
            continue
        context = getattr(content, "contextInfo", None)
        if context is not None:
            yield context


def message_text(message) -> str:
    body = unwrap_message(message)
    parts = [clean(extract_text(body) or "")]
    parts.extend(_structured_text(body))
    return clean(" ".join(part for part in parts if part.strip()))


# ADR-0010 §4.2: a location, a contact card, and a poll carry no prose; the engine still
# needs one readable line for the window, the archive, and the prompt.
STRUCTURED_FIELDS = (
    "locationMessage",
    "liveLocationMessage",
    "contactMessage",
    "contactsArrayMessage",
    "pollCreationMessage",
    "pollCreationMessageV2",
    "pollCreationMessageV3",
)


def _location_text(content) -> str:
    latitude = float(getattr(content, "degreesLatitude", 0.0) or 0.0)
    longitude = float(getattr(content, "degreesLongitude", 0.0) or 0.0)
    label = str(
        getattr(content, "name", "") or getattr(content, "address", "") or ""
    ).strip()
    coords = f"{latitude:.6f}, {longitude:.6f}"
    return f"[location: {coords} — {label}]" if label else f"[location: {coords}]"


def _contact_text(content) -> str:
    name = str(getattr(content, "displayName", "") or "").strip()
    return f"[contact: {name}]" if name else "[contact]"


def _contacts_text(content) -> str:
    found = [
        str(getattr(item, "displayName", "") or "").strip()
        for item in getattr(content, "contacts", [])
    ]
    names = [name for name in found if name]
    return f"[contacts: {', '.join(names)}]" if names else "[contacts]"


def _poll_text(content) -> str:
    name = str(getattr(content, "name", "") or "").strip()
    options = [
        str(getattr(item, "optionName", "") or "").strip()
        for item in getattr(content, "options", [])
    ]
    choices = " / ".join(option for option in options if option)
    if name and choices:
        return f"[poll: {name} — {choices}]"
    return f"[poll: {name}]" if name else "[poll]"


def _structured_text(body) -> list[str]:
    """One line per structured stanza the plain extractor cannot read (ADR-0010 §4.2)."""
    found: list[str] = []
    for field_name in STRUCTURED_FIELDS:
        content = getattr(body, field_name, None)
        if content is None or not content.ListFields():
            continue
        if field_name in ("locationMessage", "liveLocationMessage"):
            found.append(_location_text(content))
        elif field_name == "contactMessage":
            found.append(_contact_text(content))
        elif field_name == "contactsArrayMessage":
            found.append(_contacts_text(content))
        else:
            found.append(_poll_text(content))
    return found


def _base_name(message_id: str, content) -> str:
    raw = getattr(content, "fileName", "") or getattr(content, "title", "") or ""
    name = sanitize(Path(str(raw)).name, 80) if raw else ""
    return name or sanitize(message_id, 60) or "file"


def describe_media(message, message_id: str) -> Media | None:
    body = unwrap_message(message)
    for field_name in MEDIA_FIELDS:
        content = getattr(body, field_name, None)
        if content is None or not content.ListFields():
            continue
        kind = field_name[: -len("Message")].lower()
        declared = (getattr(content, "mimetype", "") or "").split(";")[0].strip().lower()
        name = _base_name(message_id, content)
        mime = declared or mimetypes.guess_type(name)[0] or FALLBACK_MIME.get(kind, "")
        mime = MIME_ALIASES.get(mime, mime)
        if not Path(name).suffix:
            name = f"{name}{EXT_FOR_MIME.get(mime, '')}"
        return Media(
            kind=kind,
            mimetype=mime or "application/octet-stream",
            filename=name,
            size=int(getattr(content, "fileLength", 0) or 0),
            proto=message,
            animated=bool(getattr(content, "isAnimated", False)) if kind == "sticker" else False,
        )
    return None


def quote_from_proto(message, message_id: str) -> Quote | None:
    from .identity import normalize_jid

    for context in contexts(message):
        quoted = context.quotedMessage
        if not quoted.ListFields():
            continue
        text = message_text(quoted)
        media = describe_media(quoted, context.stanzaID or f"{message_id}-quote")
        if not text.strip() and media is None:
            continue
        participant = context.participant or ""
        sender_id = normalize_jid(participant) if participant else ""
        return Quote(
            id=context.stanzaID,
            sender_name=sender_id or participant,
            sender_id=sender_id,
            text=text,
            media=media,
        )
    return None


def event_time(timestamp: int):
    """A transport epoch (seconds or milliseconds) as an aware local datetime."""
    from datetime import UTC, datetime

    try:
        value = int(timestamp or 0)
    except (TypeError, ValueError):
        value = 0
    if value > 10**12:
        value //= 1000
    if value <= 0:
        return datetime.now(UTC).astimezone().replace(microsecond=0)
    try:
        return datetime.fromtimestamp(value, UTC).astimezone()
    except (OverflowError, OSError, ValueError):
        return datetime.now(UTC).astimezone().replace(microsecond=0)


def _relative(config: Config, path: Path) -> str:
    try:
        return path.relative_to(config.workspace).as_posix()
    except ValueError:
        return path.as_posix()


def attachment_path(config: Config, thread_id: str, message_id: str, filename: str) -> Path:
    folder = config.workspace / "attachments" / sanitize(thread_id, 40)
    return folder / f"{sanitize(message_id, 60)}-{sanitize(filename, 80)}"


async def download_media(
    client, config: Config, thread_id: str, message_id: str, media: Media
) -> None:
    """Download an attachment once; failures land in `media.error` and are never raised."""
    if media.error:
        return
    if media.path is not None:
        try:
            if media.path.is_file() and media.path.stat().st_size:
                return
        except OSError:
            pass
        media.path = None
    if media.proto is None and media.ref:
        try:
            media.proto = Message.FromString(bytes(media.ref))
        except Exception:
            media.error = "unreadable proto"
            return
    target = attachment_path(config, thread_id, message_id, media.filename)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.stat().st_size:
            media.path = target
            media.size = media.size or target.stat().st_size
            return
        if client is None:
            media.error = "client is not available"
            return
        if media.proto is None:
            media.error = "proto is not available"
            return
        await client.download_any(media.proto, str(target))
        if not target.is_file() or not target.stat().st_size:
            with contextlib.suppress(OSError):
                target.unlink(missing_ok=True)
            media.error = "empty download"
            return
        media.path = target
        media.size = media.size or target.stat().st_size
    except Exception as exc:
        media.error = f"{type(exc).__name__}: {exc}"[:200]
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)


__all__ = [
    "IMAGE_MIMES",
    "INLINE_TEXT_BYTES",
    "MAX_IMAGES",
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_TOTAL",
    "attachment_path",
    "contexts",
    "describe_media",
    "download_media",
    "event_time",
    "human_size",
    "message_text",
    "quote_from_proto",
    "sanitize",
    "send_kind",
    "unwrap_message",
    "views_once",
]
