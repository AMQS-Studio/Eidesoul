"""Plugin loader: one manifest, one digest, one import (ADR-0028).

A plugin is an installable unit fetched at enable time, never a core tool and never a
build-time extra. The manifest is data: it is read and verified before anything is imported.
The loader never keeps a second registry — it hands validated `Handler`s back to the caller,
which passes them to the existing registries (tools, commands).

Digest semantics: `digest` is the SHA-256 of the code tree (sorted relative paths and their
bytes), excluding `plugin.yaml` itself, `deps/`, and bytecode. The same value is pinned in
`plugins.<name>.digest`; an install is refused unless both match.
"""


import hashlib
import importlib.metadata
import importlib.util
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import types
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .config import Config, HostConfig, Paths, PluginSettings
from .registry import Handler, validate

log = logging.getLogger(__name__)

MANIFEST = "plugin.yaml"
MODULE = "eidesoul_plugin"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
HANDLER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ENTRY = re.compile(r"^([a-z_][a-z0-9_]*)\.py:([a-z_][a-z0-9_]*)$")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
CONSTRAINT = re.compile(r"(>=|<=|>|<|==|=)\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?")
DIGEST = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")
SECRET = re.compile(r"^[A-Z][A-Z0-9_]*$")
SKIP_DIRS = frozenset({"__pycache__", "deps"})
MANIFEST_KEYS = frozenset(
    {"name", "version", "engine", "entry", "digest", "provides", "requires", "secrets", "credits"}
)
CREDIT_KEYS = frozenset({"license", "author", "source", "use"})
ARTIFACT_LIMIT = 64 * 1024 * 1024
FETCH_TIMEOUT = 30
DEPS_TIMEOUT = 900


class PluginError(RuntimeError):
    """A manifest, digest, fetch, or registration failure; the message carries no secrets."""


@dataclass(frozen=True)
class Credits:
    """The README credits row a plugin owes (ADR-0028 §4.2)."""

    license: str
    author: str
    source: str
    use: str = ""


@dataclass(frozen=True)
class Provides:
    tools: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    gateways: tuple[str, ...] = ()


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    engine: str
    entry: str
    digest: str
    provides: Provides
    requires: tuple[str, ...] = ()
    secrets: tuple[str, ...] = ()
    credits: tuple[Credits, ...] = ()

    @property
    def module(self) -> str:
        return self.entry.split(":", 1)[0].removesuffix(".py")

    @property
    def attribute(self) -> str:
        return self.entry.split(":", 1)[1]


@dataclass(frozen=True)
class Plugin:
    """One plugin's state; `unavailable` is the uniform answer whenever it cannot be used."""

    name: str
    state: str  # absent | installed | enabled | unavailable
    reason: str = ""
    path: Path | None = None
    manifest: Manifest | None = None


@dataclass(frozen=True)
class Loaded:
    """What boot registration produced; the caller feeds these into the existing registries."""

    tools: tuple[Handler, ...] = ()
    commands: tuple[Handler, ...] = ()
    plugins: tuple[Plugin, ...] = ()

    def state(self, name: str) -> str:
        for item in self.plugins:
            if item.name == name:
                return item.state
        return "absent"


@dataclass(frozen=True)
class PluginLoader:
    """The loader's whole input, built from the engine `Config` or from host files (CLI)."""

    root: Path
    fetch: bool = True
    source: str = ""
    plugins: Mapping[str, PluginSettings] = field(default_factory=dict)
    config_path: Path | None = None

    @classmethod
    def from_config(cls, config: Config) -> PluginLoader:
        root = config.plugin.root or config.workspace / "plugins"
        return cls(
            root=Path(root),
            fetch=config.plugin.fetch,
            source=config.plugin.source,
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
            plugins=dict(host.plugins),
            config_path=paths.config,
        )


# ------------------------------------------------------------------ digest


def tree_digest(root: Path) -> str:
    """SHA-256 over the plugin's code tree (manifest, deps, and bytecode excluded)."""
    base = Path(root)
    digest = hashlib.sha256()
    for path in sorted(base.rglob("*"), key=lambda item: item.relative_to(base).as_posix()):
        relative = path.relative_to(base)
        if path.is_dir() or any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix == ".pyc" or relative.as_posix() == MANIFEST:
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def normalize_digest(value: object) -> str:
    match = DIGEST.fullmatch(str(value or "").strip().lower())
    if match is None:
        raise PluginError(f"invalid digest {value!r}; expected sha256:<64 hex>")
    return "sha256:" + match.group(1)


def module_name(name: str) -> str:
    """The synthetic package a plugin is imported under; never a top-level plugin name."""
    return f"{MODULE}_{name.replace('-', '_')}"


# ----------------------------------------------------------------- manifest


def _strings(value: object, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PluginError(f"{label} must be a list of strings")
    return tuple(str(item).strip() for item in value if str(item).strip())


def _names(value: object, label: str) -> tuple[str, ...]:
    found = _strings(value, label)
    for name in found:
        if not HANDLER.fullmatch(name):
            raise PluginError(f"{label} contains an invalid name: {name!r}")
    return found


def _credit_row(value: object) -> Credits:
    if not isinstance(value, dict):
        raise PluginError("credits rows must be mappings")
    unknown = sorted(set(value) - CREDIT_KEYS)
    if unknown:
        raise PluginError(f"credits has unsupported key(s): {', '.join(unknown)}")
    body = {key: str(value.get(key) or "").strip() for key in CREDIT_KEYS}
    if not body["license"] or not body["author"] or not body["source"]:
        raise PluginError("credits requires license, author, and source")
    return Credits(
        license=body["license"],
        author=body["author"],
        source=body["source"],
        use=body["use"],
    )


def _credits(value: object) -> tuple[Credits, ...]:
    rows = value if isinstance(value, list) else [value]
    if not rows or any(row is None for row in rows):
        raise PluginError("credits is required: one row per dependency or source")
    return tuple(_credit_row(row) for row in rows)


def read_manifest(path: Path) -> Manifest:
    """Read and fully validate the manifest without importing any plugin code."""
    try:
        body = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PluginError(f"manifest is unreadable: {type(exc).__name__}: {exc}") from exc
    if not isinstance(body, dict):
        raise PluginError("manifest must be a mapping")
    unknown = sorted(set(body) - MANIFEST_KEYS)
    if unknown:
        raise PluginError(f"manifest has unsupported key(s): {', '.join(unknown)}")
    name = str(body.get("name") or "").strip()
    if not NAME.fullmatch(name):
        raise PluginError(f"manifest name is invalid: {name!r}")
    version = str(body.get("version") or "").strip()
    if not VERSION.fullmatch(version):
        raise PluginError("manifest version must be semver (e.g. 1.0.0)")
    engine = str(body.get("engine") or "").strip()
    if not engine:
        raise PluginError("manifest engine range is required")
    entry = str(body.get("entry") or "").strip()
    if not ENTRY.fullmatch(entry):
        raise PluginError("manifest entry must look like 'plugin.py:register'")
    digest = normalize_digest(body.get("digest"))
    provides_raw = body.get("provides") or {}
    if not isinstance(provides_raw, dict):
        raise PluginError("provides must be a mapping")
    for key, value in provides_raw.items():
        if not isinstance(key, str) or not isinstance(value, list):
            raise PluginError("provides must map names to lists")
        if not all(isinstance(item, str) and item for item in value):
            raise PluginError(f"provides.{key} must be a list of names")
    secrets = _strings(body.get("secrets"), "secrets")
    for secret in secrets:
        if not SECRET.fullmatch(secret):
            raise PluginError(f"secrets must be env names, not values: {secret!r}")
    return Manifest(
        name=name,
        version=version,
        engine=engine,
        entry=entry,
        digest=digest,
        provides=Provides(
            tools=_names(provides_raw.get("tools"), "provides.tools"),
            commands=_names(provides_raw.get("commands"), "provides.commands"),
            gateways=_names(provides_raw.get("gateways"), "provides.gateways"),
        ),
        requires=_strings(body.get("requires"), "requires"),
        secrets=secrets,
        credits=_credits(body.get("credits")),
    )


# -------------------------------------------------------------- compatibility


def engine_version() -> str:
    try:
        return importlib.metadata.version("eidesoul")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


def _release(text: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", str(text).strip())
    if match is None:
        raise PluginError(f"invalid version: {text!r}")
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def _compare(op: str, current: tuple[int, int, int], wanted: tuple[int, int, int]) -> bool:
    if op == ">=":
        return current >= wanted
    if op == "<=":
        return current <= wanted
    if op == ">":
        return current > wanted
    if op == "<":
        return current < wanted
    return current == wanted


def compatible(spec: str, version: str) -> bool:
    """A manifest's `engine` range: comma-separated `>=x.y.z`-style constraints."""
    current = _release(version)
    for part in str(spec or "").split(","):
        part = part.strip()
        if not part:
            raise PluginError(f"invalid engine range: {spec!r}")
        match = CONSTRAINT.fullmatch(part)
        if match is None:
            raise PluginError(f"invalid engine range: {spec!r}")
        op = match.group(1)
        wanted = tuple(int(value or 0) for value in match.groups()[1:])
        if not _compare(op, current, wanted):  # type: ignore[arg-type]
            return False
    return True


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
    actual = tree_digest(base)
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


def _declared(manifest: Manifest, kind: str, found: list[Handler]) -> None:
    expected = set(getattr(manifest.provides, kind))
    actual = {handler.name for handler in found}
    if expected != actual:
        missing = ", ".join(sorted(expected - actual)) or "-"
        extra = ", ".join(sorted(actual - expected)) or "-"
        raise PluginError(f"{kind} do not match provides: missing {missing}; undeclared {extra}")


def _purge(key: str) -> None:
    for item in [name for name in sys.modules if name == key or name.startswith(key + ".")]:
        del sys.modules[item]


def _entry_module(base: Path, manifest: Manifest) -> types.ModuleType:
    key = module_name(manifest.name)
    package = sys.modules.get(key)
    if package is None:
        package = types.ModuleType(key)
        package.__package__ = key
        sys.modules[key] = package
    package.__path__ = [str(base)]  # type: ignore[attr-defined]
    deps = base / "deps"
    if deps.is_dir() and str(deps) not in sys.path:
        sys.path.insert(0, str(deps))
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
) -> tuple[list[Handler], list[Handler]]:
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
        _declared(manifest, "tools", tools)
        _declared(manifest, "commands", commands)
        errors: list[str] = []

        for handler in tools:
            errors.extend(validate(handler, handler.name, "tool"))
        for handler in commands:
            errors.extend(validate(handler, handler.name, "command"))
        if errors:
            raise PluginError("; ".join(errors))
        _collisions("tool", tools, reserved_tools)
        _collisions("command", commands, reserved_commands)
        return tools, commands
    except PluginError:
        _purge(key)
        raise
    except Exception as exc:  # no plugin import may take the engine down
        _purge(key)
        raise PluginError(f"failed to load: {type(exc).__name__}: {exc}") from exc


def _collisions(kind: str, handlers: Sequence[Handler], reserved: set[str]) -> None:
    for handler in handlers:
        for value in (handler.name, *handler.aliases):
            if value in reserved:
                raise PluginError(f"{kind} '{value}' collides with an existing {kind}")
        reserved.update((handler.name, *handler.aliases))


# ------------------------------------------------------------------- lifecycle


def _core_names() -> tuple[set[str], set[str]]:
    from .commands import REGISTRY as commands
    from .tools import REGISTRY as tools

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
    found: list[Plugin] = []
    for name in names(settings):
        plugin = _inspect(settings, name)
        if plugin.state != "enabled":
            found.append(plugin)
            continue
        base = Path(settings.root) / name
        try:
            manifest = _verify(settings, name, base)
            plugin_tools, plugin_commands = _register(
                base, manifest, reserved_tools, reserved_commands
            )
        except (PluginError, OSError) as exc:
            log.warning("plugin '%s' unavailable: %s", name, exc)
            found.append(Plugin(name, "unavailable", str(exc), base))
            continue
        found_tools.extend(plugin_tools)
        found_commands.extend(plugin_commands)
        found.append(Plugin(name, "enabled", path=base, manifest=manifest))
    return Loaded(tuple(found_tools), tuple(found_commands), tuple(found))


def _fetch_artifact(settings: PluginLoader, name: str) -> bytes:
    source = str(settings.source or "").strip().rstrip("/")
    if not source:
        raise PluginError(
            "plugin.source is not set; name the operator source to fetch plugins from"
        )
    if "://" in source:
        url = f"{source}/{name}.tar.gz"
        try:
            with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as response:
                data = response.read(ARTIFACT_LIMIT + 1)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            raise PluginError(f"cannot fetch '{name}' from {url}: {reason}") from exc
    else:
        artifact = Path(source).expanduser() / f"{name}.tar.gz"
        if not artifact.is_file():
            raise PluginError(f"cannot fetch '{name}': {artifact} is missing")
        data = artifact.read_bytes()
    if len(data) > ARTIFACT_LIMIT:
        raise PluginError(f"cannot fetch '{name}': artifact exceeds {ARTIFACT_LIMIT} bytes")
    return data


def _extract(data: bytes, target: Path) -> Path:
    """Unpack a tar.gz into a temp folder; traversal, links, and devices are refused."""
    target.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for member in archive.getmembers():
                relative = PurePosixPath(member.name)
                if member.issym() or member.islnk() or member.isdev():
                    raise PluginError(f"artifact contains a link or device: {member.name}")
                if relative.is_absolute() or ".." in relative.parts:
                    raise PluginError(f"artifact contains an unsafe path: {member.name}")
            archive.extractall(target, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise PluginError(f"artifact is not a readable tar.gz: {exc}") from exc
    if (target / MANIFEST).is_file():
        return target
    children = [item for item in target.iterdir() if item.is_dir()]
    if len(children) == 1 and (children[0] / MANIFEST).is_file():
        return children[0]
    raise PluginError(f"artifact has no {MANIFEST} at its root")


def _install_requires(base: Path, manifest: Manifest) -> None:
    if not manifest.requires:
        return
    deps = base / "deps"
    deps.mkdir(parents=True, exist_ok=True)
    uv = shutil.which("uv")
    command = (
        [uv, "pip", "install", "--target", str(deps), *manifest.requires]
        if uv
        else [sys.executable, "-m", "pip", "install", "--target", str(deps), *manifest.requires]
    )
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=DEPS_TIMEOUT, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PluginError(f"dependencies for '{manifest.name}' failed: {exc}") from exc
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-1:]
        raise PluginError(
            f"dependencies for '{manifest.name}' failed: {' '.join(tail) or 'unknown error'}"
        )


def _fetch(settings: PluginLoader, name: str, base: Path) -> Plugin:
    pin = _pin(settings, name)
    if not pin:
        raise PluginError(f"plugins.{name}.digest is required to install from a source")
    data = _fetch_artifact(settings, name)
    with tempfile.TemporaryDirectory(prefix=f"eidesoul-{name}-") as temp:
        staged = _extract(data, Path(temp) / "unpacked")
        manifest = read_manifest(staged / MANIFEST)
        if manifest.name != name:
            raise PluginError(f"artifact is for '{manifest.name}', not '{name}'")
        actual = tree_digest(staged)
        if actual != pin or manifest.digest != pin:
            raise PluginError(
                f"digest mismatch: pinned {pin}, artifact tree {actual}, "
                f"manifest {manifest.digest}"
            )
        _install_requires(staged, manifest)
        base.parent.mkdir(parents=True, exist_ok=True)
        if base.exists():
            raise PluginError(f"target already exists: {base}")
        shutil.move(str(staged), str(base))
    return Plugin(name, "installed", path=base, manifest=manifest)


def install(
    settings: PluginLoader, name: str, *, actor: str = "", db=None
) -> Plugin:
    """Place verified code under the plugin root; `absent` code is fetched by digest."""
    if not NAME.fullmatch(str(name or "")):
        raise PluginError(f"invalid plugin name: {name!r}")
    base = Path(settings.root) / name
    if (base / MANIFEST).is_file():
        plugin = _inspect(settings, name)
        if plugin.state == "unavailable":
            raise PluginError(plugin.reason)
        return Plugin(name, "installed", path=base, manifest=plugin.manifest)
    if not settings.fetch:
        raise PluginError(
            f"plugin '{name}' is not installed under {base} and plugin.fetch is false; "
            "place verified code there or set plugin.fetch: true"
        )
    installed = _fetch(settings, name, base)
    _audit(db, actor, "install", name, installed.manifest.digest if installed.manifest else "")
    return installed


def _config_body(settings: PluginLoader) -> tuple[Path, dict]:
    path = settings.config_path
    if path is None or not Path(path).is_file():
        raise PluginError(f"config not found: {path} (run `eidesoul init`)")
    try:
        body = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PluginError(f"config YAML is invalid: {exc}") from exc
    if not isinstance(body, dict):
        raise PluginError("config is empty or not a mapping")
    return Path(path), body


def _write_config(path: Path, body: dict) -> None:
    mode = path.stat().st_mode & 0o777
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(body, sort_keys=False, allow_unicode=True), encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _set_enabled(settings: PluginLoader, name: str, enabled: bool) -> None:
    path, body = _config_body(settings)
    plugins = body.get("plugins")
    if not isinstance(plugins, dict):
        plugins = {}
    entry = plugins.get(name)
    entry = dict(entry) if isinstance(entry, dict) else {}
    entry["enabled"] = bool(enabled)
    plugins[name] = entry
    body["plugins"] = plugins
    _write_config(path, body)


def _drop_plugin(settings: PluginLoader, name: str) -> None:
    path, body = _config_body(settings)
    plugins = body.get("plugins")
    if isinstance(plugins, dict):
        plugins.pop(name, None)
        body["plugins"] = plugins
    _write_config(path, body)


def _audit(db, actor: str, action: str, name: str, detail: str) -> None:
    if db is None:
        return
    try:
        from .memory.ledger import ActionLedger
        from .schema import config_changes

        row = {
            "ts": time.time(),
            "actor": str(actor or ""),
            "file": "plugin",
            "path": str(name),
            "old": "",
            "new": f"{action}: {detail}"[:4000],
        }
        with db.connect() as conn:
            conn.execute(config_changes.insert().values(row))
        ActionLedger(db).log_action("plugin", str(name), "config", f"{action}: {detail}")
    except Exception:  # auditing never turns a completed action into a failure
        log.warning("plugin action was not recorded: %s %s", action, name)


def enable(settings: PluginLoader, name: str, *, actor: str = "", db=None) -> Plugin:
    """Install when needed, verify, import once, and only then write the config document."""
    if not NAME.fullmatch(str(name or "")):
        raise PluginError(f"invalid plugin name: {name!r}")
    base = Path(settings.root) / name
    if not (base / MANIFEST).is_file():
        install(settings, name, actor=actor, db=db)
    manifest = _verify(settings, name, base)
    reserved_tools, reserved_commands = _core_names()
    _register(base, manifest, reserved_tools, reserved_commands)
    _set_enabled(settings, name, True)
    _audit(db, actor, "enable", name, manifest.digest)
    return Plugin(name, "enabled", path=base, manifest=manifest)


def disable(settings: PluginLoader, name: str, *, actor: str = "", db=None) -> Plugin:
    """Switch the capability off; files and state stay for a fast reversal."""
    if not NAME.fullmatch(str(name or "")):
        raise PluginError(f"invalid plugin name: {name!r}")
    _set_enabled(settings, name, False)
    _audit(db, actor, "disable", name, "files kept")
    return _inspect(_with_enabled(settings, name, False), name)


def remove(settings: PluginLoader, name: str, *, actor: str = "", db=None) -> Plugin:
    """Delete code and config entry; an enabled plugin must be disabled first."""
    if not NAME.fullmatch(str(name or "")):
        raise PluginError(f"invalid plugin name: {name!r}")
    if _enabled(settings, name):
        raise PluginError(f"plugin '{name}' is enabled; disable it before removing")
    _ = _config_body(settings)
    base = Path(settings.root) / name
    if base.exists():
        shutil.rmtree(base)
    _drop_plugin(settings, name)
    _audit(db, actor, "remove", name, f"deleted {base}")
    return Plugin(name, "absent", "code and state were removed", base)
