"""The plugin loader: verify, import once, and hand validated handlers to the registries."""


import importlib.abc
import importlib.machinery
import importlib.util
import logging
import os
import sys
import types
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..config import Config, HostConfig, Paths, PluginSettings
from ..registry import Handler, validate
from ..turn import plugin_registration
from ..turn.prompts import PromptPart
from .manifest import (
    MANIFEST,
    NAME,
    Manifest,
    Plugin,
    PluginError,
    code_digest,
    compatible,
    engine_version,
    module_name,
    normalize_digest,
    read_manifest,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PluginLoader:
    """The loader's whole input, built from the engine `Config` or from host files (CLI)."""
    root: Path
    fetch: bool = True
    source: str = ""
    local: bool = False
    plugins: Mapping[str, PluginSettings] = field(default_factory=dict)
    config_path: Path | None = None

    @classmethod
    def from_config(cls, config: Config) -> PluginLoader:
        root = config.plugin.root or config.workspace / "plugins"
        return cls(
            root=Path(root),
            fetch=config.plugin.fetch,
            source=config.plugin.source,
            local=bool(getattr(config.plugin, "local", False)),
            plugins=dict(config.plugins),
            config_path=config.paths.config,
        )

    @classmethod
    def from_host(cls, host: HostConfig, paths: Paths) -> PluginLoader:
        root = host.plugin.root or host.workspace / "plugins"
        return cls(
            root=Path(root),
            fetch=host.plugin.fetch,
            source=host.plugin.source,
            local=bool(getattr(host.plugin, "local", False)),
            plugins=dict(host.plugins),
            config_path=paths.config,
        )


@dataclass(frozen=True)
class Loaded:
    """What boot registration produced; the caller feeds these into the existing registries."""
    tools: tuple[Handler, ...] = ()
    commands: tuple[Handler, ...] = ()
    prompts: tuple[PromptPart, ...] = ()
    plugins: tuple[Plugin, ...] = ()

    def state(self, name: str) -> str:
        for item in self.plugins:
            if item.name == name:
                return item.state
        return "absent"


# ------------------------------------------------------------------ inspection


def _enabled(settings: PluginLoader, name: str) -> bool:
    found = settings.plugins.get(name)
    return bool(found and found.enabled)


def _with_enabled(
    settings: PluginLoader, name: str, enabled: bool
) -> PluginLoader:
    """A settings copy that matches the config document just written."""
    current = settings.plugins.get(name) or PluginSettings()
    plugins = {
        **settings.plugins,
        name: PluginSettings(enabled=enabled, digest=current.digest),
    }
    return replace(settings, plugins=plugins)


def _pin(settings: PluginLoader, name: str) -> str:
    found = settings.plugins.get(name)
    return normalize_digest(found.digest) if found and found.digest else ""


def names(settings: PluginLoader) -> list[str]:
    """Configured names plus installed manifests; a fresh install reports nothing."""
    found = {name for name in settings.plugins if NAME.fullmatch(str(name))}
    root = Path(settings.root)
    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and NAME.fullmatch(child.name) and (child / MANIFEST).is_file():
                found.add(child.name)
    return sorted(found)


def _layout(base: Path, manifest: Manifest) -> None:
    entry = base / manifest.entry.split(":", 1)[0]
    if not entry.is_file():
        raise PluginError(f"entry file is missing: {entry}")
    kinds = (("tools", manifest.provides.tools), ("commands", manifest.provides.commands))
    for kind, declared in kinds:
        for name in declared:
            if not (base / kind / f"{name}.py").is_file():
                raise PluginError(
                    f"provided {kind[:-1]} '{name}' has no module at {kind}/{name}.py"
                )
    if manifest.provides.gateways:
        raise PluginError(
            "plugins cannot register gateways; install a gateway instead (ADR-0018)"
        )


def _verify(
    settings: PluginLoader, name: str, base: Path, *, secrets: bool = True
) -> Manifest:
    """Manifest + digest + pin + engine range + secrets + layout, before any import."""
    manifest = read_manifest(base / MANIFEST)
    if manifest.name != name:
        raise PluginError(f"manifest name '{manifest.name}' does not match folder '{name}'")
    if not (manifest.local or settings.local):
        actual = code_digest(base)
        if actual != manifest.digest:
            raise PluginError(
                f"digest mismatch: manifest {manifest.digest}, installed tree {actual}"
            )
        pin = _pin(settings, name)
        if pin and pin != actual:
            raise PluginError(f"digest mismatch: pinned {pin}, installed tree {actual}")
    if not compatible(manifest.engine, engine_version()):
        raise PluginError(
            f"engine {engine_version()} is outside the manifest range '{manifest.engine}'"
        )
    if secrets:
        for secret in manifest.secrets:
            if not os.environ.get(secret):
                raise PluginError(f"env {secret} is not set")
    _layout(base, manifest)
    return manifest


def _inspect(settings: PluginLoader, name: str) -> Plugin:
    base = Path(settings.root) / name
    enabled = _enabled(settings, name)
    if not (base / MANIFEST).is_file():
        if not enabled:
            return Plugin(name, "absent", "code is not installed", base)
        return Plugin(name, "unavailable", f"code is not installed under {base}", base)
    try:
        manifest = _verify(settings, name, base, secrets=enabled)
    except (PluginError, OSError) as exc:
        return Plugin(name, "unavailable", str(exc), base)
    return Plugin(name, "enabled" if enabled else "installed", path=base, manifest=manifest)


def states(settings: PluginLoader) -> tuple[Plugin, ...]:
    return tuple(_inspect(settings, name) for name in names(settings))


# --------------------------------------------------------------------- import


def _handlers(result: Mapping[str, Any], key: str) -> list[Handler]:
    value = result.get(key) or []
    if not isinstance(value, (list, tuple)):
        raise PluginError(f"register() {key} must be a list of Handler")
    found: list[Handler] = []
    for handler in value:
        if not isinstance(handler, Handler):
            raise PluginError(f"register() {key} contains a non-Handler value")
        found.append(handler)
    return found


def _purge(key: str) -> None:
    for item in [name for name in sys.modules if name == key or name.startswith(key + ".")]:
        del sys.modules[item]


class _DepsFinder(importlib.abc.MetaPathFinder):
    """Resolve a plugin's `deps/` names only when the normal path finder had no answer.

    Appended after `PathFinder`, so a dependency can never shadow a core or stdlib package.
    """

    def __init__(self, path: str) -> None:
        self.path = path

    def find_spec(self, fullname, path=None, target=None):
        if path is not None or "." in fullname:
            return None
        return importlib.machinery.PathFinder.find_spec(fullname, [self.path])


def _use_deps(deps: Path) -> None:
    """Make `deps/` importable without putting it on `sys.path` ahead of the core."""
    target = str(deps.resolve())
    for finder in sys.meta_path:
        if isinstance(finder, _DepsFinder) and finder.path == target:
            return
    sys.meta_path.append(_DepsFinder(target))


def _entry_module(base: Path, manifest: Manifest) -> types.ModuleType:
    key = module_name(manifest.name)
    package = sys.modules.get(key)
    if package is None:
        package = types.ModuleType(key)
        package.__package__ = key
        sys.modules[key] = package
    package.__path__ = [str(base)]  # type: ignore[attr-defined]
    deps = base / "deps"
    if deps.is_dir():
        _use_deps(deps)
    full = f"{key}.{manifest.module}"
    spec = importlib.util.spec_from_file_location(full, base / manifest.entry.split(":", 1)[0])
    if spec is None or spec.loader is None:
        raise PluginError(f"cannot import the entry module {manifest.entry}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


def _register(
    base: Path,
    manifest: Manifest,
    reserved_tools: set[str],
    reserved_commands: set[str],
) -> tuple[list[Handler], list[Handler], list[PromptPart]]:
    key = module_name(manifest.name)
    try:
        module = _entry_module(base, manifest)
        function = getattr(module, manifest.attribute, None)
        if not callable(function):
            raise PluginError(f"entry function '{manifest.attribute}' is missing")
        try:
            result = function()
        except Exception as exc:
            raise PluginError(
                f"registration failed: {type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(result, Mapping):
            raise PluginError("register() must return a mapping of tools/commands")
        tools = _handlers(result, "tools")
        commands = _handlers(result, "commands")
        try:
            prompts = plugin_registration.prompt_parts(
                result, manifest.name, manifest.provides.prompts
            )
            plugin_registration.declared("tools", manifest.provides.tools, tools)
            plugin_registration.declared("commands", manifest.provides.commands, commands)
        except plugin_registration.RegistrationError as exc:
            raise PluginError(str(exc)) from exc
        errors: list[str] = []

        for handler in tools:
            errors.extend(validate(handler, handler.name, "tool"))
        for handler in commands:
            errors.extend(validate(handler, handler.name, "command"))
        if errors:
            raise PluginError("; ".join(errors))
        try:
            plugin_registration.collisions("tool", tools, reserved_tools)
            plugin_registration.collisions("command", commands, reserved_commands)
        except plugin_registration.RegistrationError as exc:
            raise PluginError(str(exc)) from exc
        return tools, commands, prompts
    except PluginError:
        _purge(key)
        raise
    except Exception as exc:  # no plugin import may take the engine down
        _purge(key)
        raise PluginError(f"failed to load: {type(exc).__name__}: {exc}") from exc


# ------------------------------------------------------------------- lifecycle


def _core_names() -> tuple[set[str], set[str]]:
    from ..commands import REGISTRY as commands
    from ..tools import REGISTRY as tools

    return set(tools.reserved()), set(commands.reserved())


def load(
    settings: PluginLoader,
    *,
    tools: Iterable[str] | None = None,
    commands: Iterable[str] | None = None,
) -> Loaded:
    """Import every enabled plugin once and return its handlers; never touches the network."""
    reserved_tools, reserved_commands = _core_names()
    reserved_tools.update(tools or ())
    reserved_commands.update(commands or ())
    found_tools: list[Handler] = []
    found_commands: list[Handler] = []
    found_prompts: list[PromptPart] = []
    found: list[Plugin] = []
    for name in names(settings):
        plugin = _inspect(settings, name)
        if plugin.state != "enabled":
            found.append(plugin)
            continue
        base = Path(settings.root) / name
        try:
            manifest = _verify(settings, name, base)
            plugin_tools, plugin_commands, plugin_prompts = _register(
                base, manifest, reserved_tools, reserved_commands
            )
        except (PluginError, OSError) as exc:
            log.warning("plugin '%s' unavailable: %s", name, exc)
            found.append(Plugin(name, "unavailable", str(exc), base))
            continue
        found_tools.extend(plugin_tools)
        found_commands.extend(plugin_commands)
        found_prompts.extend(plugin_prompts)
        found.append(Plugin(name, "enabled", path=base, manifest=manifest))
    prompts = tuple(
        replace(part, order=1000 + index) for index, part in enumerate(found_prompts)
    )
    return Loaded(
        tools=tuple(found_tools),
        commands=tuple(found_commands),
        prompts=prompts,
        plugins=tuple(found),
    )


