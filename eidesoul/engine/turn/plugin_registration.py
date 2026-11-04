"""Registration checks shared by the plugin loader and the prompt registry (ADR-0034).

The loader imports no plugin before these checks pass: a prompt part must be declared, static,
namespaced, and under the cap; handlers must match their declaration and must not shadow a core
name. Errors are plain `ValueError`s so the loader can wrap them in its own `PluginError`.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from .prompts import PLUGIN_NAME, PROMPT_PART_MAX, PromptPart


class RegistrationError(ValueError):
    """A plugin declaration or registration value breaks the contract."""


def prompt_parts(
    result: Mapping[str, Any], plugin_name: str, declared: Sequence[str]
) -> list[PromptPart]:
    value = result.get("prompts") or []
    if not isinstance(value, (list, tuple)):
        raise RegistrationError("register() prompts must be a list of PromptPart")
    found: list[PromptPart] = []
    names: set[str] = set()
    for part in value:
        if not isinstance(part, PromptPart):
            raise RegistrationError("register() prompts contains a non-PromptPart value")
        if not PLUGIN_NAME.fullmatch(part.name):
            raise RegistrationError(f"plugin prompt name must be namespaced: {part.name!r}")
        if not part.name.startswith(f"plugin:{plugin_name}:"):
            raise RegistrationError(f"plugin prompt is outside its namespace: {part.name!r}")
        if not part.stable:
            raise RegistrationError(f"plugin prompt {part.name} must be stable")
        if part.produce is not None:
            raise RegistrationError(f"plugin prompt {part.name} must be literal text")
        if not part.text.strip():
            raise RegistrationError(f"plugin prompt {part.name} is empty")
        if len(part.text) > PROMPT_PART_MAX:
            raise RegistrationError(
                f"plugin prompt {part.name} exceeds {PROMPT_PART_MAX} characters"
            )
        if part.name in names:
            raise RegistrationError(f"duplicate plugin prompt: {part.name}")
        names.add(part.name)
        found.append(part)
    expected = {f"plugin:{plugin_name}:{slug}" for slug in declared}
    actual = {part.name for part in found}
    if expected != actual:
        missing = ", ".join(sorted(expected - actual)) or "-"
        extra = ", ".join(sorted(actual - expected)) or "-"
        raise RegistrationError(
            f"prompts do not match provides: missing {missing}; undeclared {extra}"
        )
    return found


def declared(kind: str, expected_values: Sequence[str], found: Sequence[Any]) -> None:
    expected = set(expected_values)
    actual = {handler.name for handler in found}
    if expected != actual:
        missing = ", ".join(sorted(expected - actual)) or "-"
        extra = ", ".join(sorted(actual - expected)) or "-"
        raise RegistrationError(
            f"{kind} do not match provides: missing {missing}; undeclared {extra}"
        )


def collisions(kind: str, handlers: Sequence[Any], reserved: set[str]) -> None:
    for handler in handlers:
        values = (handler.name, *handler.aliases)
        for value in values:
            if value in reserved:
                raise RegistrationError(f"{kind} '{value}' collides with an existing {kind}")
        reserved.update(values)
