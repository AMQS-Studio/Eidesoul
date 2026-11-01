"""Discord-style permissions: roles from numbers, glob patterns, permissions per role."""

from __future__ import annotations

from .registry import Handler, Registry, allows

__all__ = [
    "allows",
    "command_allowed",
    "command_patterns",
    "commands_for",
    "normalize",
    "tool_allowed",
    "tool_patterns",
    "tools_for",
]


def normalize(value: str) -> str:
    """Canonical form for role and address comparison; gateways hand in identities
    already in their canonical shape."""
    return str(value or "").strip().casefold()


def _as_roles(roles) -> tuple:
    return roles if isinstance(roles, tuple) else (roles,)


def _patterns(roles, attribute: str) -> tuple[str, ...]:
    found: list[str] = []
    for role in roles:
        found.extend(getattr(role, attribute, ()) or ())
    return tuple(dict.fromkeys(found))


def tool_patterns(roles) -> tuple[str, ...]:
    return _patterns(_as_roles(roles), "tools")


def command_patterns(roles) -> tuple[str, ...]:
    return _patterns(_as_roles(roles), "commands")


def tool_allowed(roles, name: str) -> bool:
    return allows(tool_patterns(roles), name)


def command_allowed(roles, name: str) -> bool:
    return allows(command_patterns(roles), name)


def tools_for(registry: Registry, roles) -> list[Handler]:
    return registry.entries(tool_patterns(roles))


def commands_for(registry: Registry, roles) -> list[Handler]:
    return registry.entries(command_patterns(roles))
