"""Gateway extension contract: one base class and one registration decorator (ADR-0046 §4.5).

A body owns the four facts the operator surface needs from it, without the core importing a
transport:

* `default_settings` — the `gateways.<name>` options, including the progress flags;
* `default_role_policy` — one role's block, written in the body's own vocabulary;
* `validate_role_policy` — refuse a block the body cannot interpret;
* `check_access` — the one decision the core asks before a turn starts.

`register_gateway` maps a name and its install extra to the class, so `gateway_index()` can offer
that metadata before a transport extra is imported. Importing this module imports no transport.
"""


import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..engine import io
from ..engine.i18n import t
from ..engine.inbound import CoreDefaults, Percept
from ..engine.turn.context import Message

log = logging.getLogger(__name__)

NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

ATTACHMENT_ROOT = "attachments"

# ADR-0010 §4.1: the engine emits the readable badge `/approve #42`; the body turns that into
# buttons at its own boundary, so the core never learns a UI vocabulary.
APPROVAL_BADGE = re.compile(r"/approve\s*#?(\d+)")

# ADR-0010 §4.1: the progress streams every body understands; the default is off.
PROGRESS_FLAGS = ("show_thinking", "show_steps", "interim_updates")
# ADR-0010 §8: one in-place edit per thread per window, so a chatty model never trips a
# platform rate limit; the last payload of the window wins.
EDIT_DEBOUNCE_SECONDS = 1.5


class EditDebouncer:
    """Coalesce rapid in-place edits per thread (ADR-0010 §4.1, §8).

    `submit` applies immediately when the window is open; inside the window it keeps the latest
    payload and applies it once the window closes, so a body edits at most once per
    `window` seconds per key and never leaves the bubble on a stale step.
    """

    def __init__(
        self,
        window: float = EDIT_DEBOUNCE_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.window = max(0.0, float(window))
        self._clock = clock
        self._last: dict[str, float] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._latest: dict[str, Callable[[], Awaitable[None]]] = {}

    def due(self, key: str) -> bool:
        """True when an edit for `key` is outside the debounce window."""
        stamp = self._last.get(key)
        return stamp is None or self._clock() - stamp >= self.window

    def mark(self, key: str) -> None:
        self._last[key] = self._clock()

    async def submit(self, key: str, apply: Callable[[], Awaitable[None]]) -> bool:
        """Run `apply` now when due, else keep the latest and run it when the window closes."""
        if self.due(key):
            self.mark(key)
            await apply()
            return True
        self._latest[key] = apply
        task = self._tasks.get(key)
        if task is None or task.done():
            self._tasks[key] = asyncio.create_task(self._trailing(key))
        return False

    async def _trailing(self, key: str) -> None:
        await asyncio.sleep(self.window)
        self.mark(key)
        apply = self._latest.pop(key, None)
        if apply is None:
            return
        try:
            await apply()
        except Exception:  # a dropped edit is advisory; the turn continues
            return

    def forget(self, key: str) -> None:
        """Drop a thread's pending trailing edit (the bubble is gone or the turn ended)."""
        task = self._tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()
        self._latest.pop(key, None)
        self._last.pop(key, None)

    async def flush(self) -> None:
        """Cancel every trailing edit; called on stop so no task outlives the body."""
        for key in list(self._tasks):
            self.forget(key)


def progress_text(capability: str, payload) -> str:
    """One readable line for a progress action (ADR-0010 §4.1).

    The engine hands over transport-neutral facts (`thinking` text, a step mapping, interim
    prose); each body styles the line natively afterwards. Empty means nothing to show.
    """
    if capability == "send_thinking":
        body = " ".join(str(payload or "").split())
        return f"💭 {body}" if body else ""
    if capability == "send_step":
        step = payload if isinstance(payload, Mapping) else {}
        tool = str(step.get("tool") or "").strip()
        status = str(step.get("status") or "").strip()
        if not tool:
            return ""
        return f"🔧 {tool} -> {status}" if status else f"🔧 {tool}"
    if capability == "send_interim":
        body = str(payload or "").strip()
        return f"💬 {body}" if body else ""
    return ""


def option_list(raw: object) -> list[dict]:
    """Normalize a prompt's option list to `[{id, label, command, style}]`; junk is dropped."""
    items = raw if isinstance(raw, (list, tuple)) else ()
    found: list[dict] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        command = str(item.get("command") or "").strip()
        label = str(item.get("label") or item.get("id") or "").strip()
        if not command or not label:
            continue
        found.append(
            {
                "id": str(item.get("id") or command),
                "label": label,
                "command": command,
                "style": str(item.get("style") or ""),
            }
        )
    return found


def _as_id(value: object) -> int:
    """A positive approval id, or 0 when the value is not one."""
    if not isinstance(value, int | str):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def safe_name(value: str, limit: int = 60) -> str:
    """A filesystem-safe fragment of a transport id or a file name."""
    cleaned = "".join(
        ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_" for ch in str(value)
    )
    return cleaned.strip("._-")[:limit] or "x"


def attachment_path(
    config, thread_id: str, message_id: str, filename: str
) -> Path:
    """Where a body stores one downloaded attachment: inside the workspace, never beside it."""
    folder = Path(config.workspace) / ATTACHMENT_ROOT / safe_name(thread_id, 40)
    return folder / f"{safe_name(message_id, 60)}-{safe_name(filename, 80)}"


def private_thread_of(db, gateway: str, person_id: str) -> str:
    """The one DM thread this body holds for a person, read from the archive.

    ADR-0033 §4.5: a body reports the private thread it can prove; an ambiguous or absent DM
    answers "" so the caller sends nothing rather than guessing a room.
    """
    from sqlalchemy import text

    person = str(person_id or "").strip()
    if db is None or not person:
        return ""
    try:
        found = db.fetch_one(
            text(
                "SELECT t.thread_id FROM threads AS t "
                "JOIN thread_people AS p ON p.thread_id = t.thread_id "
                "WHERE t.kind = 'dm' AND t.gateway = :gateway AND p.person_id = :person "
                "GROUP BY t.thread_id HAVING count(*) = 1 LIMIT 1"
            ),
            {"gateway": str(gateway or ""), "person": person},
        )
    except Exception:
        log.debug("private thread lookup failed gateway=%s", gateway, exc_info=True)
        return ""
    return str(found["thread_id"]) if found else ""


async def transcribe_media(config, message: Message) -> None:
    """Voice and audio attachments become text when the operator configured STT.

    ADR-0010 §4.2: the transcript joins the message text, so the turn reads it like any other
    sentence. A missing provider, a missing file, or a failing provider leaves the media in
    place and never fails the turn.
    """
    speech = getattr(config, "speech", None)
    if speech is None or not str(getattr(speech.stt, "provider", "") or ""):
        return
    spoken = [
        media
        for media in message.attachments
        if media.kind in ("audio", "voice") and media.path is not None
    ]
    if not spoken:
        return
    try:
        provider = speech.stt_provider()
    except Exception:
        log.warning("STT provider is not usable; keeping the media", exc_info=True)
        return
    found: list[str] = []
    for media in spoken:
        try:
            text = str(await io.run(provider.transcribe, media.path) or "").strip()
        except Exception:
            log.warning("STT failed for %s", media.filename, exc_info=True)
            continue
        if text:
            found.append(text)
    if found:
        message.text = "\n".join([message.text, *found]).strip()


class BaseGateway(CoreDefaults):
    """The defaults a body inherits; a body still supplies the core surface it promises.

    The hooks are classmethods because the operator surface asks about a gateway before one
    exists: rendering `config.yaml`, seeding a role's policy block, validating that block. The
    access decision is the one hook that needs the running body, so it stays an instance method.
    """

    name: str = ""
    extra: str = ""
    # The body's own `gateways.<name>` options and host config, set by its implementation; the
    # base reads the three progress flags and the reply language from them and nothing else.
    settings: Any = None
    config: Any = None

    @classmethod
    def default_settings(cls) -> dict[str, object]:
        """The `gateways.<name>` options this body understands, `enabled` included.

        The progress flags are declared by every body because the turn loop asks each body
        whether it renders interim narration, raw thinking, or tool steps; the default is off.
        """
        return {
            "enabled": True,
            "show_thinking": False,
            "show_steps": False,
            "interim_updates": False,
        }

    @classmethod
    def default_config(cls) -> dict[str, object]:
        """The whole `gateways.<name>` block the operator surface renders for this body."""
        return dict(cls.default_settings())

    @classmethod
    def default_role_policy(cls, role_name: str) -> dict[str, object]:
        """One role's default block; an empty block grants nothing this body must honor."""
        return {}

    @classmethod
    def tool_aliases(cls) -> dict[str, str]:
        """Transport-specific names this body still accepts, mapped to the core tool.

        The alias lives here, never in the engine: the core stays free of transport
        vocabulary while a prompt or skill written for an older body keeps working.
        """
        return {}

    @classmethod
    def validate_role_policy(cls, block: Mapping[str, object]) -> None:
        """Refuse a block outside this body's vocabulary; raise, never silently ignore."""
        if not isinstance(block, Mapping):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError(f"gateway '{cls.name}': a role policy block must be a mapping")

    def check_access(self, block: Mapping[str, object] | None, percept: Percept) -> bool:
        """Fail closed: a body that grants access declares how in its own override."""
        return False

    def language(self) -> str:
        """The operator's reply language, for a body that renders its own labels."""
        soul = getattr(self.config, "soul", None)
        return str(getattr(soul, "language", "") or "en")

    def approval_id(self, action) -> int:
        """The approval an outbound text asks to decide, read from the engine's own badge."""
        if action is None or str(getattr(action, "capability", "")) != "send_text":
            return 0
        found = APPROVAL_BADGE.search(str(getattr(action, "payload", "") or ""))
        return int(found.group(1)) if found else 0

    def approval_options(self, approval_id: int) -> list[dict]:
        """The localized Approve/Deny pair for one pending approval (ADR-0010 §4.1)."""
        number = int(approval_id)
        language = self.language()
        return [
            {
                "id": f"approve:{number}",
                "label": t("approval.button_approve", language),
                "command": f"/approve #{number}",
                "style": "primary",
            },
            {
                "id": f"deny:{number}",
                "label": t("approval.button_deny", language),
                "command": f"/deny #{number}",
                "style": "danger",
            },
        ]

    def ui_of(self, action) -> dict:
        """The prompt block of one outbound action, transport-neutral.

        The shape is `{"kind", "approval_id", "title", "summary", "buttons"}`. An explicit
        `meta["ui"]` (or a bare `meta["options"]`) wins; otherwise the engine's own
        `/approve #<id>` badge becomes the Approve/Deny pair here, at the body's boundary.
        """
        raw_meta = action.meta if action is not None else {}
        meta = raw_meta if isinstance(raw_meta, Mapping) else {}
        raw_block = meta.get("ui")
        block = raw_block if isinstance(raw_block, Mapping) else {}
        options = option_list(
            meta.get("options") or block.get("buttons") or block.get("choices")
        )
        number = self.approval_id(action) or _as_id(block.get("approval_id"))
        if not options and number:
            options = self.approval_options(number)
        if not options and not block:
            return {}
        return {
            "kind": str(block.get("kind") or ("approval" if number else "options")),
            "approval_id": number,
            "title": str(block.get("title") or ""),
            "summary": str(block.get("summary") or ""),
            "buttons": options,
        }

    def prompt_options(self, action) -> list[dict]:
        """The pressable options of one outbound action (empty when there are none)."""
        return list(self.ui_of(action).get("buttons") or ())

    def progress_flags(self) -> dict[str, bool]:
        """Runtime overrides of the progress flags; empty until `/thinking` or `/steps` sets one."""
        found = self.__dict__.get("_progress_flags")
        if not isinstance(found, dict):
            found = {}
            self.__dict__["_progress_flags"] = found
        return found

    def progress_enabled(self, name: str) -> bool:
        """A stream is on when the chat command said so, else the operator's setting."""
        override = self.progress_flags().get(name)
        if override is not None:
            return bool(override)
        settings = self.settings
        if settings is None:
            return False
        if isinstance(settings, Mapping):
            return bool(settings.get(name))
        value = getattr(settings, name, None)
        if value is None:
            options = getattr(settings, "options", None)
            if isinstance(options, Mapping):
                value = options.get(name)
        return bool(value)

    def set_progress(self, name: str, enabled: bool) -> None:
        """Record a chat-command override for one progress stream (never the operator file)."""
        if name not in PROGRESS_FLAGS:
            raise ValueError(f"unknown progress flag: {name!r}")
        self.progress_flags()[name] = bool(enabled)

    def edit_debouncer(self) -> EditDebouncer:
        """The one edit debouncer of this body, created on first use."""
        found = self.__dict__.get("_edits")
        if not isinstance(found, EditDebouncer):
            found = EditDebouncer(EDIT_DEBOUNCE_SECONDS)
            self.__dict__["_edits"] = found
        return found

    async def abort(self, thread: str) -> None:
        """Stop whatever this body is still streaming in one thread.

        The default streams nothing, so the structural `Abortable` contract is satisfied
        without a transport call; a streaming body overrides this and cancels its own work.
        """
        return None


@dataclass(frozen=True, slots=True)
class GatewayRegistration:
    """One registered body: its class and the install extra that carries its transport."""

    name: str
    gateway: type
    extra: str = ""


_REGISTERED: dict[str, GatewayRegistration] = {}


def register_gateway(name: str, *, extra: str = "") -> Callable[[type], type]:
    """Register a gateway body under `name`; `extra` is the install extra that provides it.

    Re-importing a module (a reload) replaces the same class identity; a second class under a
    name that is already taken is a boot-time conflict, not a silent overwrite.
    """
    key = str(name or "").strip()
    if not NAME.fullmatch(key):
        raise ValueError(f"invalid gateway name: {name!r}")

    def decorate(gateway: Any) -> Any:
        if not isinstance(gateway, type):
            raise ValueError(f"gateway '{key}' must be a class, not {type(gateway).__name__}")
        declared = str(getattr(gateway, "name", "") or "")
        if declared and declared != key:
            raise ValueError(
                f"gateway class declares name '{declared}', registered as '{key}'"
            )
        current = _REGISTERED.get(key)
        if current is not None and current.gateway is not gateway:
            identity = (current.gateway.__module__, current.gateway.__qualname__)
            if identity != (gateway.__module__, gateway.__qualname__):
                raise ValueError(f"gateway '{key}' is already registered by {current.gateway!r}")
        _REGISTERED[key] = GatewayRegistration(key, gateway, str(extra or "").strip())
        return gateway

    return decorate


def registered_gateways() -> dict[str, GatewayRegistration]:
    """A copy of the registry; callers never mutate discovery state."""
    return dict(_REGISTERED)


def restore_gateways(snapshot: Mapping[str, GatewayRegistration]) -> None:
    """Restore a snapshot taken with `registered_gateways()` (test isolation, reload)."""
    _REGISTERED.clear()
    _REGISTERED.update(snapshot)
