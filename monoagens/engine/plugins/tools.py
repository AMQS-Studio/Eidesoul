"""The `tool` registration DX: a typed function becomes a `Handler` with a JSON Schema."""


import collections.abc
import inspect
import re
import types
from collections.abc import Callable, Mapping
from typing import Annotated, Any, Literal, Union, get_args, get_origin, get_type_hints

from .. import io as io_pool
from ..registry import Handler
from .manifest import PluginError

# --------------------------------------------------------------- tool decorator

SCALAR_TYPES: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    type(None): "null",
}
ARRAY_ORIGINS = (
    list,
    set,
    frozenset,
    tuple,
    collections.abc.Sequence,
    collections.abc.Set,
    collections.abc.Iterable,
)
MAPPING_ORIGINS = (dict, collections.abc.Mapping, collections.abc.MutableMapping)
DOC_HEADING = re.compile(r"^(args|arguments|parameters)\s*:\s*$", re.IGNORECASE)
DOC_ARGUMENT = re.compile(r"^\s{2,}([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?\s*:\s*(.*)$")


def _annotation_schema(
    annotation: object, tool_name: str, parameter: str
) -> tuple[dict[str, Any], bool]:
    """JSON Schema for one hint, and whether it accepts `None` too."""
    origin = get_origin(annotation)
    if origin is Annotated:
        arguments = get_args(annotation)
        annotation = arguments[0] if arguments else annotation
        origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        members = get_args(annotation)
        optional = type(None) in members
        rest = [member for member in members if member is not type(None)]
        if len(rest) != 1:
            raise PluginError(
                f"tool '{tool_name}' parameter '{parameter}' must use a single type or "
                "Optional[...]; unions of several types have no JSON Schema here"
            )
        schema, _ = _annotation_schema(rest[0], tool_name, parameter)
        return schema, optional
    if origin is Literal:
        values = list(get_args(annotation))
        if values and all(isinstance(value, str) for value in values):
            return {"type": "string", "enum": values}, False
        if values and all(
            isinstance(value, int) and not isinstance(value, bool) for value in values
        ):
            return {"type": "integer", "enum": values}, False
        raise PluginError(
            f"tool '{tool_name}' parameter '{parameter}' Literal values must be all strings "
            "or all integers"
        )
    if origin in ARRAY_ORIGINS or annotation in (list, set, frozenset, tuple):
        members = get_args(annotation)
        item = members[0] if members and members[0] is not Ellipsis else None
        items, _ = (
            _annotation_schema(item, tool_name, parameter) if item is not None else ({}, False)
        )
        return {"type": "array", "items": items}, False
    if origin in MAPPING_ORIGINS or annotation in (dict,):
        return {"type": "object"}, False
    kind = SCALAR_TYPES.get(annotation) if isinstance(annotation, type) else None
    if kind is not None:
        return {"type": kind}, False
    raise PluginError(
        f"tool '{tool_name}' parameter '{parameter}' annotation {annotation!r} is not "
        "supported; use str, int, float, bool, list[...], dict, Literal[...], or Optional[...]"
    )


def _docstring(fn: Any, tool_name: str) -> tuple[str, dict[str, str]]:
    """The description and per-argument prose from a function's docstring."""
    lines = (inspect.getdoc(fn) or "").splitlines()
    summary: list[str] = []
    for line in lines:
        if not line.strip():
            break
        summary.append(line.strip())
    description = " ".join(summary).strip()
    if not description:
        raise PluginError(
            f"tool '{tool_name}' needs a docstring: its first line is the description"
        )
    descriptions: dict[str, str] = {}
    current = ""
    in_arguments = False
    for line in lines:
        if DOC_HEADING.match(line.strip()):
            in_arguments = True
            continue
        if not in_arguments:
            continue
        match = DOC_ARGUMENT.match(line)
        if match:
            current = match.group(1)
            descriptions[current] = match.group(2).strip()
        elif current and line.startswith("    ") and line.strip():
            descriptions[current] = f"{descriptions[current]} {line.strip()}".strip()
        elif line.strip():
            in_arguments = False
    return description, descriptions


def _parameters(fn: Any, tool_name: str, descriptions: Mapping[str, str]) -> tuple[dict, set[str]]:
    try:
        hints = get_type_hints(fn)
    except Exception as exc:
        raise PluginError(
            f"tool '{tool_name}' has an unresolvable type annotation: {type(exc).__name__}: {exc}"
        ) from exc
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError) as exc:
        raise PluginError(f"tool '{tool_name}' has no inspectable signature: {exc}") from exc
    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter_name, parameter in signature.parameters.items():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            raise PluginError(
                f"tool '{tool_name}' cannot describe *args/**kwargs; name each argument"
            )
        if parameter.kind is parameter.POSITIONAL_ONLY:
            raise PluginError(
                f"tool '{tool_name}' parameter '{parameter_name}' must be keyword-callable"
            )
        annotation = hints.get(parameter_name, parameter.annotation)
        if annotation is inspect.Parameter.empty:
            raise PluginError(
                f"tool '{tool_name}' parameter '{parameter_name}' needs a type annotation; "
                "@plugin.tool builds the JSON Schema from it"
            )
        schema, optional = _annotation_schema(annotation, tool_name, parameter_name)
        description = descriptions.get(parameter_name, "")
        if description:
            schema["description"] = description
        properties[parameter_name] = schema
        if parameter.default is inspect.Parameter.empty and not optional:
            required.append(parameter_name)
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        parameters["required"] = required
    return parameters, set(signature.parameters)


def _build_tool(fn: Any, options: Mapping[str, Any]) -> Handler:
    """One `Handler` whose schema comes from the function's own signature and docstring."""
    name = str(options.get("name") or getattr(fn, "__name__", "") or "").strip()
    if not name:
        raise PluginError("a @plugin.tool function needs a name")
    description, descriptions = _docstring(fn, name)
    parameters, accepted = _parameters(fn, name, descriptions)
    overridden = str(options.get("description") or "").strip()
    coroutine = inspect.iscoroutinefunction(fn)

    async def invoke(args: Mapping[str, Any] | None = None, ctx: Any = None) -> str:
        values = {
            key: value
            for key, value in dict(args or {}).items()
            if key in accepted
        }
        result = await fn(**values) if coroutine else await io_pool.run(fn, **values)
        return str(result)

    return Handler(
        name=name,
        description=overridden or description,
        fn=invoke,
        parameters=parameters,
        family=str(options.get("family") or ""),
        target=str(options.get("target") or ""),
        approval_class=str(options.get("approval_class") or "authorization"),
        aliases=tuple(options.get("aliases") or ()),
        usage=str(options.get("usage") or ""),
        hidden=bool(options.get("hidden", False)),
        destructive=tuple(options.get("destructive") or ()),
        path_arg=str(options.get("path_arg") or ""),
        scan=tuple(options.get("scan") or ()),
        max_output_chars=int(options.get("max_output_chars") or 20_000),
    )


def tool(
    fn: Any = None,
    *,
    name: str = "",
    description: str = "",
    family: str = "",
    target: str = "",
    approval_class: str = "authorization",
    aliases: tuple[str, ...] = (),
    usage: str = "",
    hidden: bool = False,
    destructive: tuple[str, ...] = (),
    path_arg: str = "",
    scan: tuple[str, ...] = (),
    max_output_chars: int = 20_000,
) -> Handler | Callable[[Any], Handler]:
    """Turn a typed, documented function into a `Handler` inside `register()`.

    The JSON Schema `parameters` object is derived from the annotations: scalars map to their
    JSON types, `list[...]` to an array, `dict` to an object, `Literal[...]` to an enum, and
    `Optional[...]`/`X | None` to the inner type with the argument not required. The docstring's
    first paragraph is the description; an `Args:` section documents each property.
    """
    options = {
        "name": name,
        "description": description,
        "family": family,
        "target": target,
        "approval_class": approval_class,
        "aliases": aliases,
        "usage": usage,
        "hidden": hidden,
        "destructive": destructive,
        "path_arg": path_arg,
        "scan": scan,
        "max_output_chars": max_output_chars,
    }

    def decorate(function: Any) -> Handler:
        return _build_tool(function, options)

    return decorate if fn is None else decorate(fn)




__all__ = ["tool"]
