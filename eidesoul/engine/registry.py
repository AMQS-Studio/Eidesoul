"""Handler registry: metadata (name, aliases, description) plus folder auto-discovery."""


import fnmatch
import importlib
import inspect
import pkgutil
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HandlerFn = Callable[..., Awaitable[str]]
NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class RegistryError(RuntimeError):
    """A handler breaks the contract, or a name/alias is duplicated. Every error is collected."""


@dataclass(frozen=True, slots=True)
class Handler:
    """One callable unit (command or tool) with its default permissions."""

    name: str
    description: str
    fn: HandlerFn | None = None
    aliases: tuple[str, ...] = ()
    usage: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    pattern: str = ""
    hidden: bool = False
    family: str = ""
    max_output_chars: int = 20_000  # ADR-0021 §4.1: per tool, cut by `execute`

    @property
    def perm(self) -> str:
        return self.pattern or self.name


def allows(patterns: tuple[str, ...], name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


# ADR-0024 §4.2: the validator's whole vocabulary. `description`/`title` are annotations, not
# assertions, so they are allowed everywhere; anything else is a boot failure, never ignored.
KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "enum",
        "items",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "pattern",
        "additionalProperties",
        "description",
        "title",
    }
)
TYPES = frozenset({"string", "integer", "number", "boolean", "array", "object", "null"})


def _keyword_errors(schema: dict, label: str) -> list[str]:
    """A keyword outside the supported subset must fail boot, not be silently ignored."""
    errors: list[str] = []
    unknown = sorted(set(schema) - KEYWORDS)
    if unknown:
        errors.append(f"{label}: unsupported keyword(s): {', '.join(unknown)}")
    kind = schema.get("type")
    if kind is not None and kind not in TYPES:
        errors.append(f"{label}.type must be one of: {', '.join(sorted(TYPES))}")
    enum = schema.get("enum")
    if enum is not None and (not isinstance(enum, list) or not enum):
        errors.append(f"{label}.enum must be a non-empty list")
    for key in ("minimum", "maximum", "minLength", "maxLength"):
        value = schema.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            errors.append(f"{label}.{key} must be a number")
    pattern = schema.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str):
            errors.append(f"{label}.pattern must be a string")
        else:
            try:
                re.compile(pattern)
            except re.error as exc:
                errors.append(f"{label}.pattern is not a valid regex: {exc}")
    if schema.get("type") == "array" and not isinstance(schema.get("items"), dict):
        errors.append(f"{label}.items must be an object schema for type 'array'")
    return errors


def _schema_errors(parameters: object) -> list[str]:
    if not isinstance(parameters, dict):
        return ["parameters must be an object schema"]
    errors: list[str] = []
    if parameters.get("type") != "object":
        errors.append("parameters.type must be 'object'")
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        errors.append("parameters.properties must be an object")
        properties = {}
    errors.extend(_keyword_errors(parameters, "parameters"))
    for key, value in properties.items():
        if not isinstance(value, dict):
            errors.append(f"parameters.properties['{key}'] must be an object")
            continue
        errors.extend(_keyword_errors(value, f"parameters.properties['{key}']"))
        items = value.get("items")
        if isinstance(items, dict):
            errors.extend(
                _keyword_errors(items, f"parameters.properties['{key}'].items")
            )
    required = parameters.get("required")
    if required is not None:
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            errors.append("parameters.required must be list[str]")
        else:
            unknown = [item for item in required if item not in properties]
            if unknown:
                joined = ", ".join(unknown)
                errors.append(f"parameters.required contains unknown properties: {joined}")
    additional = parameters.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        errors.append("parameters.additionalProperties must be a boolean")
    return errors


def validate(
    handler: Handler,
    source: str,
    kind: str,
    families: frozenset[str] = frozenset(),
) -> list[str]:
    """Check the handler contract; return the error list prefixed with `<source>.py: `."""
    errors: list[str] = []

    def add(message: str) -> None:
        errors.append(f"{source}.py: {message}")

    if handler.name != source:
        add(f"handler name '{handler.name}' must match the file name '{source}'")
    if not NAME.fullmatch(handler.name):
        add(f"invalid name: '{handler.name}'")
    if not handler.description.strip():
        add("description is required")
    for alias in handler.aliases:
        if not NAME.fullmatch(alias):
            add(f"invalid alias: '{alias}'")
        elif alias == handler.name:
            add(f"alias '{alias}' matches the handler name")
    if handler.fn is not None and not callable(handler.fn):
        add("fn must be callable")
    if handler.fn is not None and callable(handler.fn) and not inspect.iscoroutinefunction(
        handler.fn
    ):
        # ADR-0024 §4.5: both callers `await` the handler; a sync one is a boot-time bug.
        add("fn must be async")
    if handler.fn is None:
        if kind == "command":
            add("command requires fn")
        elif kind == "tool" and not handler.pattern:
            add("tool requires fn")
    if kind == "tool":
        if handler.family not in families:
            choices = ", ".join(sorted(families))
            add(f"invalid family: '{handler.family}'; pick one of: {choices}")
        if handler.fn is not None:
            schema_errors = _schema_errors(handler.parameters)
            errors.extend(f"{source}.py: {message}" for message in schema_errors)
    return errors


class Registry:
    """Canonical name + aliases -> Handler; permissions always come explicitly from the role."""

    def __init__(self, handlers: list[Handler], kind: str):
        self.kind = kind
        self.by_name: dict[str, Handler] = {}
        self.aliases: dict[str, str] = {}
        errors: list[str] = []
        for handler in handlers:
            key = handler.name.lower()
            if key in self.by_name:
                errors.append(f"duplicate {kind}: {key}")
                continue
            self.by_name[key] = handler
            for alias in handler.aliases:
                alias = alias.lower()
                if alias in self.aliases or alias in self.by_name:
                    errors.append(f"duplicate {kind} alias: {alias}")
                    continue
                self.aliases[alias] = key
        if errors:
            raise RegistryError("invalid handler:\n- " + "\n- ".join(errors))

    def get(self, name: str) -> Handler | None:
        key = name.lower()
        return self.by_name.get(key) or self.by_name.get(self.aliases.get(key, ""))

    def names(self) -> list[str]:
        return sorted(self.by_name)

    def reserved(self) -> tuple[str, ...]:
        """Every name a new handler may not claim: canonical names plus aliases (ADR-0028)."""
        return tuple(sorted({*self.by_name, *self.aliases}))

    def patterns(self, explicit: tuple[str, ...] | None) -> tuple[str, ...]:
        """Only the explicit list applies; the registry has no defaults."""
        return explicit or ()

    def entries(self, patterns: tuple[str, ...]) -> list[Handler]:
        return [h for h in self.by_name.values() if not h.hidden and allows(patterns, h.name)]


def discover(
    package: str,
    folder: Path,
    attribute: str,
    kind: str,
    families: frozenset[str] = frozenset(),
) -> list[Handler]:
    """Import every module in the folder (sorted by name), validate, and merge the errors."""
    found: list[Handler] = []
    errors: list[str] = []
    for info in sorted(pkgutil.iter_modules([str(folder)]), key=lambda item: item.name):
        if info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{package}.{info.name}")
        except Exception as exc:  # a broken plugin is reported alongside the other invalid plugins
            errors.append(f"{info.name}.py: failed to import: {type(exc).__name__}: {exc}")
            continue
        handler = getattr(module, attribute, None)
        if handler is None:
            errors.append(f"{info.name}.py: does not define {attribute}")
            continue
        if not isinstance(handler, Handler):
            errors.append(f"{info.name}.py: {attribute} is not a Handler")
            continue
        handler_errors = validate(handler, info.name, kind, families)
        errors.extend(handler_errors)
        if not handler_errors:
            found.append(handler)
    if errors:
        raise RegistryError("invalid handler:\n- " + "\n- ".join(errors))
    return found


def discover_many(
    sources: Sequence[tuple[str, Path]],
    attribute: str,
    kind: str,
    families: frozenset[str] = frozenset(),
) -> list[Handler]:
    """Merge discovery across several packages (core + gateway) with aggregated errors."""
    found: list[Handler] = []
    errors: list[str] = []
    for package, folder in sources:
        try:
            found.extend(discover(package, Path(folder), attribute, kind, families))
        except RegistryError as exc:
            errors.append(str(exc).split(":\n- ", 1)[-1])
    if errors:
        raise RegistryError("invalid handler:\n- " + "\n- ".join(errors))
    return found
