"""Gateways are appendages: transport in and out of the core, never leaking into the engine."""


import ast
import importlib
import importlib.util
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from ..engine.config import ConfigError
from ..engine.inbound import capability_report, ensure_core
from .base import (
    BaseGateway,
    GatewayRegistration,
    register_gateway,
    registered_gateways,
)
from .descriptor import GatewayDescriptor

log = logging.getLogger(__name__)

__all__ = [
    "BaseGateway",
    "GatewayInfo",
    "GatewayRegistration",
    "build_gateways",
    "gateway_index",
    "load_gateway",
    "register_gateway",
    "registered_gateways",
]


@dataclass(frozen=True)
class GatewayInfo:
    """One discoverable gateway: builtin package or entry point."""

    name: str
    module: str
    attribute: str
    source: str = "builtin"
    descriptor: GatewayDescriptor = field(default_factory=GatewayDescriptor)
    extra: str = ""

    @property
    def can_pair(self) -> bool:
        return bool(self.descriptor.onboarding)

    @property
    def installed(self) -> bool:
        try:
            return importlib.util.find_spec(self.module) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            return False

    @property
    def gateway_class(self) -> type | None:
        """The body class, imported lazily; a missing transport extra answers None."""
        try:
            module = importlib.import_module(self.module)
        except ImportError:
            return None
        found = getattr(module, self.attribute, None)
        return found if isinstance(found, type) else None

    def default_settings(self) -> dict[str, object]:
        """The body's own `gateways.<name>` defaults; empty when it cannot be imported."""
        hook = getattr(self.gateway_class, "default_settings", None)
        found = hook() if callable(hook) else None
        return dict(found) if isinstance(found, Mapping) else {}

    def default_config(self) -> dict[str, object]:
        """The whole `gateways.<name>` block the body renders, when it declares one."""
        hook = getattr(self.gateway_class, "default_config", None)
        found = hook() if callable(hook) else None
        return dict(found) if isinstance(found, Mapping) else self.default_settings()

    def default_role_policy(self, role_name: str) -> dict[str, object]:
        """One role's default policy block, owned by the body's own vocabulary."""
        hook = getattr(self.gateway_class, "default_role_policy", None)
        found = hook(str(role_name)) if callable(hook) else None
        return dict(found) if isinstance(found, Mapping) else {}


def _class_name(path: Path, gateway_name: str) -> str | None:
    """Find the class in a builtin gateway module whose `name` is the directory name."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            if not any(isinstance(target, ast.Name) and target.id == "name" for target in targets):
                continue
            value = statement.value
            if isinstance(value, ast.Constant) and value.value == gateway_name:
                return node.name
    return None


def _has_pair(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "pair"
        for node in ast.walk(tree)
    )


def _capabilities(folder: Path) -> tuple[str, ...]:
    """Tool names declared by `TOOL = Handler(name=...)` in the gateway's own package."""
    found: list[str] = []
    tool_dir = folder / "tools"
    if not tool_dir.is_dir():
        return ()
    for path in sorted(tool_dir.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if not any(isinstance(t, ast.Name) and t.id == "TOOL" for t in node.targets):
                continue
            name = next(
                (
                    kw.value.value
                    for kw in node.value.keywords
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant)
                ),
                None,
            )
            if isinstance(name, str) and name not in found:
                found.append(name)
    return tuple(found)


def _descriptor(folder: Path) -> GatewayDescriptor:
    """The package's own `descriptor.py`, with derived fallbacks for older gateways."""
    try:
        module = importlib.import_module(f"{__name__}.{folder.name}.descriptor")
    except ImportError:
        module = None
    found = getattr(module, "GATEWAY", None) if module is not None else None
    if isinstance(found, GatewayDescriptor):
        return found
    onboarding = "pair" if _has_pair(folder / "cli.py") else ""
    schema = (
        f"{__name__}.{folder.name}.schema"
        if (folder / "schema.py").is_file()
        else ""
    )
    return GatewayDescriptor(
        name=folder.name,
        onboarding=onboarding,
        schema=schema,
        capabilities=_capabilities(folder),
    )


def _builtin_index() -> dict[str, GatewayInfo]:
    found: dict[str, GatewayInfo] = {}
    root = Path(__file__).parent
    for folder in sorted(root.iterdir()):
        module_path = folder / "gateway.py"
        if not folder.is_dir() or not module_path.is_file():
            continue
        attribute = _class_name(module_path, folder.name)
        if attribute is None:
            continue
        found[folder.name] = GatewayInfo(
            folder.name,
            f"{__name__}.{folder.name}.gateway",
            attribute,
            descriptor=_descriptor(folder),
        )
    return found


def _entrypoint_index() -> dict[str, GatewayInfo]:
    found: dict[str, GatewayInfo] = {}
    try:
        entries = entry_points(group="monoagens.gateways")
    except Exception:
        return found
    for entry in entries:
        module, _, attribute = str(entry.value).partition(":")
        if not module or not attribute or entry.name in found:
            continue
        found[str(entry.name)] = GatewayInfo(str(entry.name), module, attribute, "entry point")
    return found


def _registered_index() -> dict[str, GatewayInfo]:
    """Bodies that called `@register_gateway`, keyed by their registered name."""
    found: dict[str, GatewayInfo] = {}
    for name, entry in registered_gateways().items():
        found[name] = GatewayInfo(
            name,
            entry.gateway.__module__,
            entry.gateway.__qualname__,
            "registered",
            extra=entry.extra,
        )
    return found


def gateway_index() -> dict[str, GatewayInfo]:
    """Every gateway visible to this install: packages, entry points, registered bodies."""
    found = _builtin_index()
    for name, info in _entrypoint_index().items():
        found.setdefault(name, info)
    for name, info in _registered_index().items():
        current = found.get(name)
        if current is None:
            found[name] = info
        elif not current.extra and info.extra:
            found[name] = replace(current, extra=info.extra)
    return found


def ensure_schemas(db) -> list[str]:
    """Create gateway-specific tables from each discoverable `<gateway>.schema` module."""
    done: list[str] = []
    for info in gateway_index().values():
        module_name = f"{info.module.rsplit('.', 1)[0]}.schema"
        try:
            module = importlib.import_module(module_name)
        except ImportError:  # gateway extra is not installed
            continue
        ensure = getattr(module, "ensure_schema", None)
        if not callable(ensure):
            continue
        ensure(db)
        done.append(module_name)
    return done


def cli_entry(name: str, *args) -> Any:
    """A transport CLI entry point, looked up by capability so core names no transport."""
    for info in gateway_index().values():
        module_name = f"{info.module.rsplit('.', 1)[0]}.cli"
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        function = getattr(module, name, None)
        if callable(function):
            return function(*args)
    raise ConfigError(f"no gateway provides the '{name}' command; install its extra")


def gateway_cli(name: str, command: str, *args) -> Any:
    """Run one command from a named gateway's CLI module; the index supplies the module."""
    info = gateway_index().get(str(name))
    if info is None:
        raise ConfigError(f"unknown gateway '{name}'")
    module_name = f"{info.module.rsplit('.', 1)[0]}.cli"
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ConfigError(f"gateway '{name}' needs its transport extra: {exc}") from exc
    function = getattr(module, command, None)
    if not callable(function):
        raise ConfigError(f"gateway '{name}' does not provide '{command}'")
    return function(*args)


def load_gateway(name: str):
    """Gateway class from the auto-indexed builtins and entry points."""
    info = gateway_index().get(str(name))
    if info is None:
        raise ConfigError(f"unknown gateway '{name}'")
    try:
        module = importlib.import_module(info.module)
    except ImportError as exc:
        raise ConfigError(
            f"gateway '{name}' needs its transport extra: {exc}"
        ) from exc
    return getattr(module, info.attribute)


def build_gateways(
    config,
    *,
    db,
    history,
    windows,
    only: tuple[str, ...] = (),
    force: tuple[str, ...] = (),
) -> list:
    """Build the enabled gateways from the `gateways` section of config.yaml; `only`/`force` are
    used by the console."""
    found: list = []
    names = list(config.gateways)
    for name in only:
        if name not in names:
            names.append(name)
    for name in names:
        settings = config.gateways.get(name)
        enabled = bool(settings and settings.enabled) or name in force
        if not enabled:
            continue
        if only and name not in only:
            continue
        cls = load_gateway(name)
        found.append(cls(config, db, history, windows, settings=settings))
    if not found:
        raise ConfigError("no gateway is enabled; fill in the `gateways` section of config.yaml")
    for gateway in found:
        ensure_core(gateway)
    log.info("gateway_capabilities %s", capability_report(found))
    return found
