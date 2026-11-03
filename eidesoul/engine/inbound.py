"""The embodiment-neutral seam: a body hands in a `Percept`, the engine emits `Action`s.

A body is a package under `eidesoul/gateways/<name>/`; it talks to the engine only through the
types and protocols here. Three protocols keep the contract honest:

* `GatewayCore` — mandatory: lifecycle, delivery of text, display, and its own catalog.
* `Rich` — optional: files, stickers, typing, media preparation.
* `Directory` — optional: target resolution and thread membership.

`Percept` carries body facts, never transport rules. `hints` is an open mapping; the engine reads
`dm`, `mention`, `reply`, `group`, `presence` only where a decision needs them and never requires
a hint the body cannot supply. The known keys:

* `dm` — the thread is a direct conversation.
* `mention` — this turn was addressed by an explicit mention.
* `reply` — this turn replies to one of the body's own messages.
* `group` — the thread has more than two participants.
* `presence` — the body observed activity without a message.
* `self` — the message came from the body's own account (never a turn).
* `roles` — role names the body declares for this turn (local console only).

Minimal body::

    class Echo:
        name = "echo"

        def tools(self): return []
        def commands(self): return []
        async def run(self, agent): ...
        async def stop(self): ...
        async def send_text(self, thread, text, *, reply=None): ...
        def render(self, text): return text
        def prompt_hints(self): return ""
        def self_handles(self): return set()
        # `chat_name` defaults to the thread id through `CoreDefaults`.
"""


from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from .context import Message
from .registry import Handler

HINT_KEYS = frozenset({"dm", "mention", "reply", "group", "presence", "self", "roles"})

CONSENT = "consent"
AUTHORIZATION = "authorization"
APPROVAL_CLASSES = frozenset({CONSENT, AUTHORIZATION})

MESSAGE = "message"
TASK = "task"


class GatewayError(RuntimeError):
    """Transport failure that is safe to show to the model or user."""


class StandbyError(RuntimeError):
    """A send was refused because this instance is not the primary (ADR-0017 §4.4)."""


@dataclass(frozen=True, slots=True)
class Percept:
    """One body-supplied event: who, where, what, and the facts the body can prove."""

    source: str
    thread: str
    actor: str
    kind: str
    payload: Message
    hints: Mapping[str, object] = field(default_factory=dict)
    raw: object = None  # transport event, used by the gateway for quotes


@dataclass(frozen=True, slots=True)
class Action:
    """One thing the turn did: a capability applied to a target under an approval class."""

    capability: str
    target: str
    payload: object
    approval_class: str = CONSENT

    def __post_init__(self) -> None:
        if self.approval_class not in APPROVAL_CLASSES:
            raise ValueError(
                f"approval_class must be one of {sorted(APPROVAL_CLASSES)}, got "
                f"{self.approval_class!r}"
            )


class CoreDefaults:
    """Defaults a body gets by inheriting; `GatewayCore` requires the rest."""

    name: str = ""
    backlog_seconds: float = 0.0

    def chat_name(self, thread: str) -> str:
        """Default label: the opaque thread id itself."""
        return thread


@runtime_checkable
class GatewayCore(Protocol):
    """Mandatory body surface: lifecycle, text delivery, display, catalog."""

    name: str

    def tools(self) -> list[Handler]: ...

    def commands(self) -> list[Handler]: ...

    async def run(self, agent) -> None: ...

    async def stop(self) -> None: ...

    async def send_text(
        self, thread: str, text: str, *, reply: Percept | None = None
    ) -> str: ...

    def render(self, text: str) -> str: ...

    def prompt_hints(self) -> str: ...

    def self_handles(self) -> set[str]: ...

    def chat_name(self, thread: str) -> str:
        """Default: the thread id, when a body has no label for it."""
        return thread


@runtime_checkable
class Rich(Protocol):
    """Optional body surface: media and presence."""

    async def send_file(
        self, thread: str, path: Path, caption: str = "", *, reply: Percept | None = None
    ) -> str: ...

    async def send_sticker(self, thread: str, message_id: str) -> str | None: ...

    async def typing(self, thread: str, composing: bool) -> None: ...

    async def prepare_media(self, thread: str, message: Message) -> None: ...


@runtime_checkable
class Directory(Protocol):
    """Optional body surface: thread names and membership, never guessed."""

    def resolve_target(
        self, query: str
    ) -> tuple[tuple[str, str] | None, list[tuple[str, str]]]: ...

    def participants(self, thread: str) -> set[str] | None: ...


CORE_MEMBERS = (
    "name",
    "tools",
    "commands",
    "run",
    "stop",
    "send_text",
    "render",
    "prompt_hints",
    "self_handles",
    "chat_name",
)


def missing_core(gateway: object) -> list[str]:
    """Members of `GatewayCore` the object does not provide (empty means conforming)."""
    return [member for member in CORE_MEMBERS if not hasattr(gateway, member)]


def ensure_core(gateway: object) -> None:
    """Boot check: fail with the missing member named, not on the first message."""
    from .config import ConfigError

    name = str(getattr(gateway, "name", "") or type(gateway).__name__)
    missing = missing_core(gateway)
    if missing:
        raise ConfigError(
            f"gateway '{name}' does not meet GatewayCore: missing {', '.join(missing)}"
        )
    if not isinstance(gateway, GatewayCore):
        raise ConfigError(f"gateway '{name}' does not meet GatewayCore")


def capabilities(gateway: object) -> dict[str, bool]:
    """Presence of the optional surfaces, for the one boot log line."""
    return {"rich": isinstance(gateway, Rich), "directory": isinstance(gateway, Directory)}


def capability_report(gateways) -> dict[str, dict[str, bool]]:
    return {str(gateway.name): capabilities(gateway) for gateway in gateways}


def directory_refusal(gateway) -> str:
    """The capability half of the cross-thread door: "" when the body can resolve threads."""
    if gateway is None:
        return "gateway is not available"
    if not isinstance(gateway, Directory):
        return "this body has no directory; a cross-thread target is refused"
    return ""


def refusal(gateway, thread: str, requester: str) -> str:
    """Cross-thread evidence (ADR-0018 §4.2): "" when allowed, a reason otherwise.

    Fail closed: a body without `Directory`, or one that does not know the members, never gets
    permission by default. Only a participant set that names the requester allows the crossing.
    """
    reason = directory_refusal(gateway)
    if reason:
        return reason
    members = gateway.participants(thread)
    if members is None:
        return "this body cannot prove who is in that thread; the request is refused"
    if requester and requester not in members:
        return "the sender is not a participant of that thread"
    return ""
