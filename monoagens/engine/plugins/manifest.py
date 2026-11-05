"""Plugin manifest, digest, and engine-range rules (ADR-0028).

Everything here is data: the manifest is read and verified before any plugin code is imported.
The digest covers the published tree (`code_digest`) or the whole installation (`tree_digest`).
"""


import hashlib
import importlib.metadata
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

MANIFEST = "plugin.yaml"
MODULE = "monoagens_plugin"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
REMOVE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
HANDLER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ENTRY = re.compile(r"^([a-z_][a-z0-9_]*)\.py:([a-z_][a-z0-9_]*)$")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
CONSTRAINT = re.compile(r"(>=|<=|>|<|==|=)\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?")
DIGEST = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")
SECRET = re.compile(r"^[A-Z][A-Z0-9_]*$")
SKIP_DIRS = frozenset({"__pycache__"})
CODE_SKIP_DIRS = frozenset({"__pycache__", "deps"})
MANIFEST_KEYS = frozenset(
    {
        "name",
        "version",
        "engine",
        "entry",
        "digest",
        "local",
        "provides",
        "requires",
        "secrets",
        "credits",
    }
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
    prompts: tuple[str, ...] = ()


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
    local: bool = False

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


# ------------------------------------------------------------------ digest


def _digest(root: Path, skip: frozenset[str]) -> str:
    base = Path(root)
    digest = hashlib.sha256()
    for path in sorted(base.rglob("*"), key=lambda item: item.relative_to(base).as_posix()):
        relative = path.relative_to(base)
        if path.is_dir() or any(part in skip for part in relative.parts):
            continue
        if path.suffix == ".pyc" or relative.as_posix() == MANIFEST:
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def tree_digest(root: Path) -> str:
    """SHA-256 over every file in the plugin tree, including installed `deps/`.

    A changed dependency is a changed tree: this digest is the integrity view an operator or a
    test recomputes. Manifest pinning uses `code_digest`, because `deps/` is installed locally
    from `requires` and is not part of the published artifact.
    """
    return _digest(Path(root), SKIP_DIRS)


def code_digest(root: Path) -> str:
    """SHA-256 over the published plugin code: manifest, sources, and no `deps/`."""
    return _digest(Path(root), CODE_SKIP_DIRS)


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
    local = body.get("local", False)
    if not isinstance(local, bool):
        raise PluginError("manifest local must be true or false")
    digest = "" if local and not body.get("digest") else normalize_digest(body.get("digest"))
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
            prompts=_names(provides_raw.get("prompt"), "provides.prompt"),
        ),
        requires=_strings(body.get("requires"), "requires"),
        secrets=secrets,
        credits=_credits(body.get("credits")),
        local=local,
    )


# -------------------------------------------------------------- compatibility


def engine_version() -> str:
    try:
        return importlib.metadata.version("monoagens")
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


