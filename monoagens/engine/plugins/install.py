"""Install, enable, disable, and remove plugin trees (ADR-0005)."""


import io
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

import yaml

from .loader import (
    PluginLoader,
    _core_names,
    _enabled,
    _inspect,
    _pin,
    _register,
    _verify,
    _with_enabled,
)
from .manifest import (
    ARTIFACT_LIMIT,
    DEPS_TIMEOUT,
    FETCH_TIMEOUT,
    MANIFEST,
    NAME,
    REMOVE_NAME,
    Manifest,
    Plugin,
    PluginError,
    code_digest,
    read_manifest,
)

log = logging.getLogger(__name__)


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
    with tempfile.TemporaryDirectory(prefix=f"monoagens-{name}-") as temp:
        staged = _extract(data, Path(temp) / "unpacked")
        manifest = read_manifest(staged / MANIFEST)
        if manifest.name != name:
            raise PluginError(f"artifact is for '{manifest.name}', not '{name}'")
        if manifest.local:
            raise PluginError(
                f"plugin '{name}' declares local: true; place the tree at {base} "
                "instead of installing it from a source"
            )
        actual = code_digest(staged)
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
        raise PluginError(f"config not found: {path} (run `monoagens init`)")
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
        from ..memory.ledger import ActionLedger
        from ..schema import config_changes

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


def _remove_target(settings: PluginLoader, name: str) -> Path:
    """The plugin folder to delete, proven inside the root and never a symlink."""
    if not REMOVE_NAME.fullmatch(str(name or "")):
        raise PluginError(f"invalid plugin name: {name!r}")
    root = Path(settings.root).resolve()
    base = root / str(name)
    target = base.resolve()
    if not target.is_relative_to(root):
        raise PluginError(f"refusing to remove outside the plugin root: {base}")
    if base.is_symlink():
        raise PluginError(f"refusing to remove a symbolic link: {base}")
    return base


def remove(settings: PluginLoader, name: str, *, actor: str = "", db=None) -> Plugin:
    """Delete code and config entry; an enabled plugin must be disabled first."""
    base = _remove_target(settings, name)
    if _enabled(settings, name):
        raise PluginError(f"plugin '{name}' is enabled; disable it before removing")
    _ = _config_body(settings)
    if base.exists():
        shutil.rmtree(base)
    _drop_plugin(settings, name)
    _audit(db, actor, "remove", name, f"deleted {base}")
    return Plugin(name, "absent", "code and state were removed", base)
