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


import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..engine.inbound import CoreDefaults, Percept

NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class BaseGateway(CoreDefaults):
    """The defaults a body inherits; a body still supplies the core surface it promises.

    The hooks are classmethods because the operator surface asks about a gateway before one
    exists: rendering `config.yaml`, seeding a role's policy block, validating that block. The
    access decision is the one hook that needs the running body, so it stays an instance method.
    """

    name: str = ""
    extra: str = ""

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
