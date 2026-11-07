"""Progress streams a gateway opted into: they ride the same outbound door as a reply.

ADR-0010 §4.1: one turn owns one status bubble. `stream_id`/`replace_id` are the same
deterministic value for every progress action of a turn, so a body that can edit a message
updates that bubble in place instead of appending a new one; a body that cannot edit simply
sends the payload as its own text. The action `id` stays unique per emission, so the outbox
ledger still refuses to send the same step twice after a resume.
"""


import logging
from collections.abc import Mapping

from ..inbound import Action, Address

log = logging.getLogger(__name__)

PROGRESS_CAPABILITIES = ("send_thinking", "send_step", "send_interim")
PROGRESS_FLAGS = ("show_thinking", "show_steps", "interim_updates")
STREAM_ID = "turn:{turn_id}:progress"


def stream_id(turn_id: str) -> str:
    """The one status-bubble id of a turn (ADR-0010 §4.1)."""
    return STREAM_ID.format(turn_id=str(turn_id or ""))


def _settings_flag(settings, name: str) -> bool:
    """The operator's declaration, read from the body's own settings object."""
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


def progress_flag(gateway, name: str) -> bool:
    """A progress stream is opt-in: the chat command wins, then the body's settings."""
    decide = getattr(gateway, "progress_enabled", None)
    if callable(decide):
        return bool(decide(name))
    return _settings_flag(getattr(gateway, "settings", None), name)


def apply_gateway_flag(gateway, name: str, enabled: bool) -> bool:
    """Turn one progress stream on or off on the live body; False when it cannot hold it."""
    if name not in PROGRESS_FLAGS:
        raise ValueError(f"unknown progress flag: {name!r}")
    setter = getattr(gateway, "set_progress", None)
    if not callable(setter):
        return False
    setter(name, bool(enabled))
    return True


def _progress_callbacks(dispatch, gateway, thread_id: str, turn_id: str) -> dict:
    """The LLM progress streams this gateway asked to render (ADR-0001 §4.2).

    Progress is delivery like any other: it goes through the door with the same-room origin
    and a deterministic id, so a resumed turn never emits the same step twice.
    """
    address = Address(str(getattr(gateway, "name", "") or ""), thread_id)
    bubble = stream_id(turn_id)
    counter = {"next": 0}

    async def emit(capability: str, payload) -> None:
        action = Action(
            address=address,
            capability=capability,
            payload=payload,
            id=f"turn:{turn_id}:progress:{counter['next']}",
            stream_id=bubble,
            replace_id=bubble,
        )
        counter["next"] += 1
        try:
            await dispatch("", address, action, origin=address)
        except Exception:  # progress is advisory; the turn continues without it
            log.debug("progress %s failed chat=%s", capability, thread_id, exc_info=True)

    callbacks: dict = {}
    if progress_flag(gateway, "interim_updates"):
        callbacks["send_interim"] = lambda text: emit("send_interim", text)
    if progress_flag(gateway, "show_thinking"):
        callbacks["send_thinking"] = lambda text: emit("send_thinking", text)
    if progress_flag(gateway, "show_steps"):
        callbacks["send_step"] = lambda step: emit("send_step", step)
    return callbacks
