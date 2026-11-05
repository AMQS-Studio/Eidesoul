"""Progress streams a gateway opted into: they ride the same outbound door as a reply."""


import logging
from collections.abc import Mapping

from ..inbound import Action, Address

log = logging.getLogger(__name__)


def _gateway_flag(gateway, name: str) -> bool:
    """A progress stream is opt-in: the gateway's own settings must declare it."""
    settings = getattr(gateway, "settings", None)
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


def _progress_callbacks(dispatch, gateway, thread_id: str, turn_id: str) -> dict:
    """The LLM progress streams this gateway asked to render (ADR-0001 §4.2).

    Progress is delivery like any other: it goes through the door with the same-room origin
    and a deterministic id, so a resumed turn never emits the same step twice.
    """
    address = Address(str(getattr(gateway, "name", "") or ""), thread_id)
    counter = {"next": 0}

    async def emit(capability: str, payload) -> None:
        action = Action(
            address=address,
            capability=capability,
            payload=payload,
            id=f"turn:{turn_id}:progress:{counter['next']}",
        )
        counter["next"] += 1
        try:
            await dispatch("", address, action, origin=address)
        except Exception:  # progress is advisory; the turn continues without it
            log.debug("progress %s failed chat=%s", capability, thread_id, exc_info=True)

    callbacks: dict = {}
    if _gateway_flag(gateway, "interim_updates"):
        callbacks["send_interim"] = lambda text: emit("send_interim", text)
    if _gateway_flag(gateway, "show_thinking"):
        callbacks["send_thinking"] = lambda text: emit("send_thinking", text)
    if _gateway_flag(gateway, "show_steps"):
        callbacks["send_step"] = lambda step: emit("send_step", step)
    return callbacks
