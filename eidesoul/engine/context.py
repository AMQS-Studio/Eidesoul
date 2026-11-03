"""Per-chat conversation context: message window, attachments, and the payload for the LLM."""

from __future__ import annotations

import base64
import logging
import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from . import lexicon
from .permissions import normalize

if TYPE_CHECKING:
    from .config import Config
    from .stores.history import HistoryStore

log = logging.getLogger(__name__)

LIVE_BUFFER = 40  # live merge buffer, not the payload
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
IMAGE_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
TEXT_MIMES = {"application/json", "application/xml", "application/yaml", "application/x-yaml"}
TEXT_EXT = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".html", ".log", ".ini",
    ".toml", ".py", ".js", ".ts", ".sh", ".sql", ".c", ".h", ".cpp", ".java", ".go", ".rs",
    ".rb", ".php", ".css",
}
OPAQUE_MIMES = ("", "application/octet-stream")
NAME_LIMIT = 80
MEDIA_LABELS = {
    "sticker": "sticker",
    "image": "image",
    "video": "video",
    "audio": "audio",
    "document": "document",
}
FOOTER = (
    "Guidance: answer the last message. Text marked [truncated] can be read in full with the read "
    "tool at the given path (inside the sandbox: /work/<path>). Binary attachments must be inspected "
    "with the sandbox tool; never invent their contents."
)


DEFAULT_VISIBILITY = 2
VISIBILITY_MAX = 4
AUTHOR_BOT = "bot"
AUTHOR_MANUAL = "manual_owner"
AUTHOR_SYSTEM = "system"
AUTHOR_UNKNOWN = "unknown"
AUTHOR_RANK = {"unknown": 0, "system": 1, "manual_owner": 2, "bot": 3}
AUTHORS = frozenset(AUTHOR_RANK)


def clamp_visibility(value: object, default: int = DEFAULT_VISIBILITY) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(0, min(VISIBILITY_MAX, number))


def entry_tag(entry: Message) -> str:
    tag = f"role={entry.author} vis=L{entry.visibility}"
    if entry.view_once:
        tag += ", view-once"
    if entry.unreadable and not (entry.text or "").strip() and entry.media is None:
        tag += ", undecryptable"
    return tag


def body_note(entry: Message) -> str:
    """Replacement body for a deleted/undecryptable line; "" for a normal line."""
    if entry.deleted:
        if entry.deleted_admin:
            return f"(message deleted by admin: {entry.deleted_by or '-'})"
        if entry.author == AUTHOR_BOT:
            return f"(message deleted by {entry.deleted_by or 'owner'})"
        return "(message deleted by the sender)"
    if entry.unreadable and not (entry.text or "").strip() and entry.media is None:
        return "(message undecryptable on this device)"
    return ""


@dataclass
class Media:
    kind: str
    mimetype: str
    filename: str
    size: int
    proto: object = field(repr=False, compare=False, default=None)
    path: Path | None = None
    error: str = ""
    animated: bool = False
    ref: bytes | None = field(default=None, repr=False, compare=False)


@dataclass
class Quote:
    id: str
    sender_name: str
    sender_id: str
    text: str
    media: Media | None = None
    when: datetime | None = None


@dataclass
class Message:
    id: str
    sender_name: str
    sender_id: str
    when: datetime
    text: str
    media: Media | None = None
    quote: Quote | None = None
    outgoing: bool = False
    sender_address: str = ""
    # "person" when the transport can mention that address, "" otherwise
    sender_kind: str = ""
    author: str = AUTHOR_UNKNOWN
    visibility: int = DEFAULT_VISIBILITY
    view_once: bool = False
    unreadable: bool = False
    deleted: bool = False
    deleted_by: str = ""
    deleted_admin: bool = False

    def __post_init__(self) -> None:
        if self.author not in AUTHORS:
            self.author = AUTHOR_UNKNOWN
        self.visibility = clamp_visibility(self.visibility)

    def mark_deleted(self, by_address: str, by_name: str = "") -> bool:
        if self.deleted:
            return False
        self.deleted = True
        self.deleted_by = str(by_name or "")
        self.deleted_admin = bool(
            self.author != AUTHOR_BOT
            and bool(by_address)
            and normalize(self.sender_address) != normalize(by_address)
        )
        return True


class WorkingMemory:
    """Per-chat message window; the full archive stays in HistoryStore."""

    def __init__(self, limit: int = LIVE_BUFFER, store: HistoryStore | None = None):
        self.limit = max(1, limit)
        self.store = store
        self._data: dict[str, list[Message]] = {}
        self._loaded: set[str] = set()

    def _ensure(self, thread_id: str) -> None:
        if self.store is None or thread_id in self._loaded:
            return
        try:
            self._data[thread_id] = self.store.recent(thread_id, self.limit)
        except Exception:
            log.exception("failed to load chat history=%s", thread_id)
            self._data.setdefault(thread_id, [])
        self._loaded.add(thread_id)

    @staticmethod
    def _merge(old: Message, new: Message) -> Message:
        upgrade = AUTHOR_RANK.get(new.author, 0) > AUTHOR_RANK.get(old.author, 0)
        richer = (
            (new.media and not old.media)
            or (new.quote and not old.quote)
            or (new.text and not old.text)
        )
        chosen = new if upgrade or richer else old
        if AUTHOR_RANK.get(old.author, 0) > AUTHOR_RANK.get(new.author, 0):
            chosen = replace(
                chosen,
                sender_name=old.sender_name,
                sender_id=old.sender_id,
                sender_address=old.sender_address,
            )
        return replace(
            chosen,
            author=new.author if upgrade else old.author,
            visibility=max(old.visibility, new.visibility),
            media=chosen.media or old.media,
            quote=chosen.quote or old.quote,
            text=chosen.text or old.text,
            view_once=old.view_once or new.view_once,
            unreadable=old.unreadable and not (new.text or new.media),
        )

    def append(self, thread_id: str, message: Message) -> None:
        self._ensure(thread_id)
        history = self._data.setdefault(thread_id, [])
        for index, old in enumerate(reversed(history)):
            if message.id and old.id == message.id:
                history[len(history) - 1 - index] = self._merge(old, message)
                break
        else:
            history.append(message)
        del history[: -self.limit]
        if self.store is not None:
            try:
                self.store.add(thread_id, message)
            except Exception:
                log.exception("failed to save message chat=%s id=%s", thread_id, message.id)

    def get(self, thread_id: str) -> list[Message]:
        self._ensure(thread_id)
        return list(self._data.get(thread_id, []))

    def find(self, thread_id: str, message_id: str) -> Message | None:
        if not message_id:
            return None
        for item in reversed(self.get(thread_id)):
            if item.id == message_id:
                return item
        if self.store is None:
            return None
        try:
            return self.store.find(thread_id, message_id)
        except Exception:
            log.exception("failed to search the old messages chat=%s id=%s", thread_id, message_id)
            return None

    def chat_name(self, thread_id: str) -> str:
        if self.store is None:
            return ""
        try:
            return self.store.chat_name(thread_id)
        except Exception:
            log.exception("failed to read the chat name=%s", thread_id)
            return ""

    def set_chat_name(self, thread_id: str, name: str) -> None:
        if self.store is None or not str(name or "").strip():
            return
        try:
            self.store.set_chat_name(thread_id, name)
        except Exception:
            log.exception("failed to save the chat name=%s", thread_id)

    def remember_name(self, thread_id: str, name: str) -> None:
        if not str(name or "").strip():
            return
        try:
            if self.store is not None and self.store.chat_name(thread_id):
                return
        except Exception:
            return
        self.set_chat_name(thread_id, name)

    def mark_deleted(
        self, thread_id: str, message_id: str, by_address: str, by_name: str = ""
    ) -> bool:
        self._ensure(thread_id)
        for index, entry in enumerate(self._data.setdefault(thread_id, [])):
            if entry.id != message_id or not entry.mark_deleted(by_address, by_name):
                continue
            if self.store is not None:
                try:
                    self.store.add(thread_id, entry)
                except Exception:
                    log.exception(
                        "failed to save the deletion chat=%s id=%s", thread_id, message_id
                    )
            return True
        if self.store is None:
            return False
        return self.store.mark_deleted(thread_id, message_id, by_address, by_name)

    def set_visibility(self, thread_id: str, message_ids: tuple[str, ...], level: int) -> int:
        self._ensure(thread_id)
        wanted = {str(item) for item in message_ids if item}
        if not wanted:
            return 0
        value = clamp_visibility(level)
        history = self._data.get(thread_id, [])
        changed = 0
        for index, entry in enumerate(history):
            if entry.id in wanted and entry.visibility < value:
                history[index] = replace(entry, visibility=value)
                changed += 1
        return changed

    def invalidate(self, thread_id: str) -> None:
        self._data.pop(thread_id, None)
        self._loaded.discard(thread_id)


INVISIBLE = dict.fromkeys(map(ord, "\u2068\u2069\u200e\u200f\u202a\u202b\u202c\u202d\u202e"), None)


def clean(text: str) -> str:
    """Strip the bidi isolation characters wrapping body mentions, normalise NBSP."""
    return (text or "").translate(INVISIBLE).replace("\u00a0", " ")


def keyword_set(text: str, language: str = "id") -> set[str]:
    """Words of >=4 letters without the pack's stopwords: used by salience and drift."""
    stopwords = lexicon.load(language).stopwords
    return {
        word
        for word in re.findall(r"[a-z0-9]{4,}", str(text or "").lower())
        if word not in stopwords
    }


def sanitize(value: str, limit: int = 60) -> str:
    cleaned = "".join(
        ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_" for ch in str(value)
    )
    return cleaned.strip("._-")[:limit] or "x"


def _flatten(text: str, limit: int = PREVIEW_CHARS) -> str:
    flat = " ".join(clean(str(text)).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def _relative(config: Config, path: Path) -> str:
    try:
        return path.relative_to(config.workspace).as_posix()
    except ValueError:
        return path.as_posix()


def text_path(config: Config, thread_id: str, message_id: str) -> Path:
    folder = config.workspace / "media" / sanitize(thread_id, 40)
    return folder / f"{sanitize(message_id or f'tmp-{time.time_ns()}', 60)}.txt"


def _is_image(media: Media) -> bool:
    mime = media.mimetype.lower()
    if mime in MIME_ALIASES:
        media.mimetype = mime = MIME_ALIASES[mime]
    if mime in IMAGE_MIMES:
        return True
    if mime in OPAQUE_MIMES:
        guessed = IMAGE_EXT.get(Path(media.filename).suffix.lower())
        if guessed:
            media.mimetype = guessed
            return True
    return False


def _is_text(media: Media) -> bool:
    mime = media.mimetype.lower()
    if mime.startswith("text/") or mime in TEXT_MIMES:
        return True
    if mime in OPAQUE_MIMES:
        return Path(media.filename).suffix.lower() in TEXT_EXT
    return False


def _clip(text: str, max_lines: int = PREVIEW_LINES, max_chars: int = PREVIEW_CHARS) -> tuple[str, int]:
    """Clip to `max_lines`/`max_chars`; the count of hidden lines is returned as a number."""
    lines = text.splitlines() or [text]
    if len(lines) <= max_lines and len(text) <= max_chars:
        return text, 0
    head = lines[:max_lines]
    joined = "\n".join(head)
    if len(joined) <= max_chars:
        return joined, len(lines) - len(head)
    cut = joined[:max_chars]
    newline = cut.rfind("\n")
    if newline > 0:
        shown = cut[:newline]
        return shown, len(lines) - len(shown.splitlines())
    return cut, len(lines)  # one giant line: no complete line is visible


def _save_full(config: Config, thread_id: str, message_id: str, text: str) -> str:
    path = text_path(config, thread_id, message_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        log.warning("failed to save the full text %s", path, exc_info=True)
        return ""
    return _relative(config, path)


def _clip_marker(remaining: int, path: str) -> str:
    if path:
        return f'  ... [truncated: {remaining} more lines; read the full text: read path="{path}"]'
    return f"  ... [truncated: {remaining} more lines; could not save the full text]"


def _media_key(config: Config, media: Media) -> str:
    return _relative(config, media.path) if media.path is not None else ""


def _text_lines(config: Config, thread_id: str, message: Message) -> list[str]:
    body = message.text.rstrip()
    preview, remaining = _clip(body)
    lines = [f"  {line}" for line in preview.splitlines()]
    if remaining:
        lines.append(_clip_marker(remaining, _save_full(config, thread_id, message.id, body)))
    return lines


def _inline_lines(media: Media, relative: str) -> list[str]:
    try:
        size = media.path.stat().st_size
    except OSError as exc:
        return [f"  [unreadable: {type(exc).__name__}]"]
    if size > INLINE_TEXT_BYTES:
        return [f"  [large file; read it with the read tool, path=\"{relative}\"]"]
    try:
        content = media.path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [f"  [unreadable: {type(exc).__name__}]"]
    preview, remaining = _clip(content, INLINE_TEXT_LINES, INLINE_TEXT_CHARS)
    lines = [f"  {line}" for line in preview.splitlines()]
    if remaining:
        lines.append(_clip_marker(remaining, relative))
    return lines


def _media_lines(
    config: Config,
    media: Media,
    number: int,
    current: bool,
    prefix: str = "attachment",
) -> list[str]:
    if media.kind == "sticker":
        label = "sticker"
    elif _is_image(media):
        label = "image"
    else:
        label = media.kind
    motion = (
        " [animated; only the first frame is visible]"
        if media.animated
        else (" [static sticker]" if media.kind == "sticker" else "")
    )
    head = (
        f"  {prefix}: {label} {media.filename} ({media.mimetype}, {human_size(media.size)}){motion}"
    )
    if media.error:
        return [f"{head} [download failed: {media.error}]"]
    if media.path is None:
        return [f"{head} [not available]"]
    relative = _relative(config, media.path)
    if number:
        return [f"{head} -> {relative} [{label} #{number}]"]
    if _is_image(media):
        return [f"{head} -> {relative} [not injected; inspect it with sandbox]"]
    if _is_text(media):
        if not current:
            return [f"{head} -> {relative} [read it with the read tool]"]
        return [f"{head} -> {relative}", *_inline_lines(media, relative)]
    hint = f"[binary; inspect it with the sandbox tool, e.g. file /work/{relative}]"
    return [f"{head} -> {relative} {hint}"]


def fallback_content(config: Config, message: Message) -> str:
    """Minimal payload when full assembly fails: the latest message text + its attachment lines."""
    lines = [f"[{entry_tag(message)}] Latest message: {message.text}"]
    if message.media is not None:
        lines.extend(_media_lines(config, message.media, 0, True))
    return "\n".join(lines)


def _quote_lines(config: Config, thread_id: str, quote: Quote) -> list[str]:
    stamp = f"[{quote.when:%Y-%m-%d %H:%M}] " if quote.when else ""
    name = _flatten(quote.sender_name, NAME_LIMIT) or "-"
    ident = quote.sender_id if quote.sender_id and quote.sender_id != quote.sender_name else ""
    who = f"{name} ({ident})" if ident else name
    body = quote.text.rstrip()
    if body.strip():
        preview, remaining = _clip(body)
        lines = [f'  ↳ replying to {stamp}{who}: "{_flatten(preview)}"']
        if remaining:
            quote_id = quote.id or f"quote-{time.time_ns()}"
            lines.append(_clip_marker(remaining, _save_full(config, thread_id, quote_id, body)))
        return lines
    if quote.media is not None:
        return [f'  ↳ replying to {stamp}{who}: "(attachment)"']
    return [f'  ↳ replying to {stamp}{who}: "(content not available)"']


def prompt_entry(entry: Message, bot_name: str, limit: int = 300) -> str:
    """One context line for the gate/appraisal: author, name, text, media, and short quotes."""
    who = (
        f"you ({bot_name})"
        if entry.author == AUTHOR_BOT
        else (
            f"{_flatten(entry.sender_name, NAME_LIMIT) or '-'} (manual owner)"
            if entry.author == AUTHOR_MANUAL
            else (entry.sender_name or entry.sender_id or "-")
        )
    )
    note = body_note(entry)
    body = note or _flatten(entry.text, limit)
    notes: list[str] = []
    if entry.media is not None:
        kind = MEDIA_LABELS.get(entry.media.kind, entry.media.kind)
        notes.append(f"[{kind}, animated]" if entry.media.animated else f"[{kind}]")
    if entry.quote is not None and not entry.deleted:
        qwho = _flatten(entry.quote.sender_name, NAME_LIMIT) or "-"
        qtext = _flatten(entry.quote.text, 120)
        if qtext:
            notes.append(f'replying to {qwho}: "{qtext}"')
        elif entry.quote.media is not None:
            kind = MEDIA_LABELS.get(entry.quote.media.kind, entry.quote.media.kind)
            notes.append(f"replying to {qwho}: ({kind})")
        else:
            notes.append(f"replying to {qwho}")
    text = " ".join([body, *notes]).strip()
    return f"[{entry_tag(entry)}] {who}: {text}" if text else ""


def _render_order(messages: list[Message]) -> list[Media]:
    """Media in render order: the quote's media before the message's own media."""
    order: list[Media] = []
    for entry in messages:
        if entry.quote is not None and entry.quote.media is not None:
            order.append(entry.quote.media)
        if entry.media is not None:
            order.append(entry.media)
    return order


def _choose_images(config: Config, current: Message, order: list[Media]) -> set[str]:
    priority: list[Media] = []
    if current.media is not None:
        priority.append(current.media)
    if current.quote is not None and current.quote.media is not None:
        priority.append(current.quote.media)
    priority.extend(reversed(order))

    chosen: set[str] = set()
    total = 0
    for media in priority:
        if len(chosen) >= MAX_IMAGES:
            break
        if media.error or media.path is None or not _is_image(media):
            continue
        key = _media_key(config, media)
        if not key or key in chosen:
            continue
        try:
            size = media.path.stat().st_size
        except OSError:
            continue
        if not size or size > MAX_IMAGE_BYTES or total + size > MAX_IMAGE_TOTAL:
            continue
        chosen.add(key)
        total += size
    return chosen


def _plan_images(
    config: Config, current: Message, order: list[Media]
) -> tuple[dict[str, int], list[Media]]:
    """Number the selected images in render order; return the key->number map and that order."""
    chosen = _choose_images(config, current, order)
    numbers: dict[str, int] = {}
    planned: list[Media] = []
    for media in order:
        key = _media_key(config, media)
        if key in chosen and key not in numbers:
            numbers[key] = len(planned) + 1
            planned.append(media)
    return numbers, planned


def _message_lines(
    config: Config,
    thread_id: str,
    entry: Message,
    numbers: dict[str, int],
    current: Message,
) -> list[str]:
    stamp = f"[{entry.when:%Y-%m-%d %H:%M}]"
    tag = entry_tag(entry)
    if entry.author != AUTHOR_BOT and entry.sender_id and entry.sender_kind == "person":
        tag = f"{tag}, mention=@{entry.sender_id}"
    if entry.author == AUTHOR_BOT:
        name = _flatten(entry.sender_name or config.soul.name, NAME_LIMIT) or "-"
    else:
        name = _flatten(entry.sender_name, NAME_LIMIT) or "-"
    ident = entry.sender_id if entry.sender_id and entry.sender_id != entry.sender_name else ""
    who = f"{name} ({ident})" if ident else name
    suffix = f"{tag}, you" if entry.author == AUTHOR_BOT else tag
    head = f"{stamp} {who} [{suffix}]:"
    lines = [head]
    note = body_note(entry)
    if note:
        lines.append(f"  {note}")
    elif entry.text.strip():
        lines.extend(_text_lines(config, thread_id, entry))
    elif entry.media is not None:
        lines.append("  (no text)")
    if entry.quote is not None and not entry.deleted:
        lines.extend(_quote_lines(config, thread_id, entry.quote))
        if entry.quote.media is not None:
            media = entry.quote.media
            number = numbers.get(_media_key(config, media), 0)
            lines.extend(
                _media_lines(
                    config, media, number, entry is current, prefix="quoted attachment"
                )
            )
    if entry.media is not None and not entry.deleted:
        number = numbers.get(_media_key(config, entry.media), 0)
        lines.extend(_media_lines(config, entry.media, number, entry is current))
    return lines


async def build_user_content(
    config: Config,
    thread_id: str,
    messages: list[Message],
    current: Message,
    chat_name: str = "",
) -> str | list[dict]:
    """Assemble the payload: context header, then each message (name, number, time, text, quote)."""
    order = _render_order(messages)
    numbers, planned = _plan_images(config, current, order)

    label = f' "{_flatten(chat_name, NAME_LIMIT)}"' if chat_name.strip() else ""
    lines = [f"Thread{label} ({len(messages)} messages, oldest → newest; local time):"]
    for entry in messages:
        lines.append("")
        lines.extend(_message_lines(config, thread_id, entry, numbers, current))

    parts: list[dict] = []
    for media in planned:
        number = numbers[_media_key(config, media)]
        try:
            raw = media.path.read_bytes()
        except OSError as exc:
            lines.append(f"Image #{number} could not be attached ({type(exc).__name__})")
            continue
        encoded = base64.b64encode(raw).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{media.mimetype};base64,{encoded}", "detail": "high"},
            }
        )

    if planned:
        label = f"#{1}..#{len(planned)}" if len(planned) > 1 else "#1"
        lines.append(f"Images {label} are attached in order after this text.")
    lines.append("")
    lines.append(FOOTER)

    text = "\n".join(lines)
    if not parts:
        return text
    return [{"type": "text", "text": text}, *parts]
