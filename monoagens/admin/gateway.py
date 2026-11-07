"""Gateway lifecycle: config/roles edits, onboarding, and the runtime composition seam.

Every host-file write goes through `admin.yaml_io`, so comments survive and a bind-mounted
file falls back to an in-place write. The runtime gateway list is composed here too: this
module is already a sanctioned composition root, which keeps `monoagens/cli.py` free of a
static gateway import (ADR-0000 §4.7).
"""


import asyncio
import copy
import inspect
import re
import shutil
import subprocess
from pathlib import Path

import yaml

from . import service
from .templates import CLOSED_GATEWAY_POLICY, GATEWAY_POLICY, comment_for
from .yaml_io import render_update, write_text

_FLOW_EMPTY = re.compile(r"^(?P<pad>\s*)(?P<key>[A-Za-z0-9_-]+):\s*\{\}\s*$")
SESSIONS = Path.home() / ".monoagens" / "sessions"


def _path(value: str | None) -> Path:
    return Path(value or "config.yaml").expanduser()


def _roles_path(path: Path) -> Path:
    return path.parent / "roles.yaml"


def _names() -> dict[str, str]:
    from .gateway_index import gateway_index

    return {name: info.source for name, info in gateway_index().items()}


def _info(name: str):
    from .gateway_index import gateway_index

    return gateway_index().get(str(name))


def gateway_info(name: str):
    """The discovery record for one gateway, or `None`."""
    return _info(name)


def _enabled(path: Path) -> dict[str, bool]:
    body = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = body.get("gateways") or {}
    return {
        str(name): bool(value.get("enabled")) if isinstance(value, dict) else False
        for name, value in raw.items()
    }


def enabled_names(config_path: str | None) -> list[str]:
    path = _path(config_path)
    if not path.is_file():
        return []
    return sorted(name for name, on in _enabled(path).items() if on)


def _read_roles(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    roles = raw.get("roles") if isinstance(raw, dict) else None
    return roles if isinstance(roles, dict) else None


_KEY_LINE = re.compile(r"^(?P<pad>\s*)(?P<key>[^#\s][^:]*?):\s*(?:#.*)?$")


def indent_lists(text: str) -> str:
    """Re-indent `key:` + same-indent `- item` sequences the writer can replace.

    PyYAML emits indentless sequences, while the generated files indent them. Both are valid
    YAML, but only the indented form lets the line-range writer replace a whole list without
    leaving the old items behind. Values, ordering, and comments are unchanged.
    """
    lines = text.split("\n")
    out = list(lines)
    index = 0
    while index < len(lines):
        found = _KEY_LINE.match(lines[index])
        if found is None:
            index += 1
            continue
        pad = len(found.group("pad"))
        cursor = index + 1
        if cursor >= len(lines):
            index += 1
            continue
        item = re.match(rf"^{' ' * pad}-(?:\s|$)", lines[cursor])
        if item is None:
            index += 1
            continue
        end = cursor
        while end < len(lines):
            line = lines[end]
            stripped = line.strip()
            if not stripped:
                end += 1
                continue
            own = len(line) - len(line.lstrip(" "))
            if own < pad:
                break
            if own == pad and not stripped.startswith(("-", "#")):
                break  # the next mapping key at the sequence's own indent
            end += 1
        for position in range(cursor, end):
            if out[position].strip():
                out[position] = "  " + out[position]
        index = end
    return "\n".join(out)


def _leaves(updates: dict, prefix: str = "") -> list[list[str]]:
    found: list[list[str]] = []
    for key, value in updates.items():
        head = prefix.split(".") if prefix else []
        path = [*head, str(key)]
        if isinstance(value, dict) and value:
            found.extend(_leaves(value, ".".join(path)))
        else:
            found.append(path)
    return found


def open_empty(text: str, updates: dict) -> str:
    """A flow-empty `key: {}` ancestor cannot take children; open it before inserting.

    `templates.dump_config` writes an empty `gateways: {}`, and `yaml_io` inserts children
    below a block. Opening the empty mapping first keeps the edit valid without re-rendering
    the file, so every comment stays where it was.
    """
    ancestors = {leaf[depth] for leaf in _leaves(updates) for depth in range(len(leaf) - 1)}
    lines = text.split("\n")
    for index, line in enumerate(lines):
        found = _FLOW_EMPTY.match(line)
        if found is not None and found.group("key") in ancestors:
            lines[index] = f"{found.group('pad')}{found.group('key')}:"
    return "\n".join(lines)


def note(path: str, value: object) -> str:
    """A YAML comment line (`# ...`) for one key, built from the generated documentation."""
    return "\n".join(f"# {line}" for line in comment_for(path, value).splitlines())


def render_document(text: str, updates: dict, *, comments: dict | None = None) -> str:
    """The updated text for one operator file; the caller proves and writes it."""
    prepared = indent_lists(open_empty(text, updates))
    return render_update(prepared, updates, comments=comments or {})


def update_document(path: Path, updates: dict, *, comments: dict | None = None) -> str:
    """Apply dotted updates through the comment-preserving writer, then validate.

    `comments` holds ready comment lines (`# ...`) keyed by dotted path; an absent key keeps
    whatever trailing comment the line already had.
    """
    text = path.read_text(encoding="utf-8")
    changed = render_document(text, updates, comments=comments)
    write_text(path, changed)
    return changed


def _seed(gateway: str, role: str) -> dict:
    """The body's own role vocabulary, with the shipped matrix as the fallback."""
    try:
        from ..gateways import load_gateway

        hook = getattr(load_gateway(gateway), "default_role_policy", None)
        if callable(hook):
            found = hook(str(role))
            if isinstance(found, dict):
                return dict(found)
    except Exception:  # a bare core install has no transport extra to import
        pass
    return copy.deepcopy(GATEWAY_POLICY.get(str(role), CLOSED_GATEWAY_POLICY))


def _missing(roles: dict, gateway: str) -> list[str]:
    found: list[str] = []
    for role, body in roles.items():
        if not isinstance(body, dict):
            continue
        blocks = body.get("gateways")
        if not isinstance(blocks, dict) or not isinstance(blocks.get(gateway), dict):
            found.append(str(role))
    return found


def missing_policies(config_path: str | None) -> list[tuple[str, str]]:
    """Every (gateway, role) pair an enabled gateway still lacks in roles.yaml."""
    path = _path(config_path)
    roles = _read_roles(_roles_path(path))
    if roles is None or not path.is_file():
        return []
    enabled = [name for name, on in _enabled(path).items() if on]
    return [(name, role) for name in enabled for role in _missing(roles, name)]


def materialize(config_path: str | None, gateway: str) -> int:
    """Write the seed block for every role that lacks one (ADR-0002 §4.4)."""
    path = _path(config_path)
    roles_path = _roles_path(path)
    roles = _read_roles(roles_path)
    if roles is None:
        return 0
    missing = _missing(roles, gateway)
    if not missing:
        return 0
    updates: dict = {"roles": {}}
    comments: dict = {}
    for role in missing:
        body = roles.get(role)
        if not isinstance(body, dict):
            continue
        seeded = _seed(gateway, role)
        updates["roles"][role] = {"gateways": {gateway: seeded}}
        comments[f"roles.{role}.gateways.{gateway}.dm"] = note(
            f"gateways.{gateway}", seeded
        )
    if not updates["roles"]:
        return 0
    update_document(roles_path, updates, comments=comments)
    return len(missing)


def normalize_address(gateway: str, address: str) -> str:
    """The gateway's own canonical address shape; an unavailable gateway changes nothing."""
    raw = str(address or "").strip()
    try:
        from ..gateways import load_gateway

        klass = load_gateway(gateway)
    except Exception:  # an unknown gateway or an absent transport extra
        return raw
    descriptor = inspect.getattr_static(klass, "normalize_address", None)
    if descriptor is None:
        return raw
    try:
        if isinstance(descriptor, (staticmethod, classmethod)):
            return str(klass.normalize_address(raw) or raw)
        return str(klass().normalize_address(raw) or raw)
    except Exception:  # the hook needs a configured instance this edit cannot build
        return raw


def list_gateways(config_path: str | None = None) -> int:
    path = _path(config_path)
    enabled = _enabled(path) if path.is_file() else {}
    for name, source in _names().items():
        state = "enabled" if enabled.get(name) else "disabled"
        print(f"{name}: {state} ({source})")
    return 0


def install_extra(extra: str, root: Path) -> int:
    """`uv sync --extra` in the checkout; a host without uv gets the exact command instead."""
    if not (root / "pyproject.toml").is_file():
        print(f"install its dependency: uv sync --no-dev --extra {extra}", flush=True)
        return 0
    uv = shutil.which("uv")
    if uv is None:
        print(f"install its dependency: uv sync --locked --no-dev --extra {extra}", flush=True)
        return 0
    completed = subprocess.run(
        [uv, "sync", "--locked", "--no-dev", "--extra", extra],
        cwd=root,
        check=False,
    )
    return int(completed.returncode)


def _extras(path: Path) -> set[str]:
    try:
        import tomllib

        body = tomllib.loads((path.parent / "pyproject.toml").read_text(encoding="utf-8"))
        return set((body.get("project") or {}).get("optional-dependencies") or {})
    except (OSError, ValueError):
        return set()


def _extra_for(path: Path, name: str) -> str:
    info = _info(name)
    declared = str(getattr(getattr(info, "descriptor", None), "extra", "") or "")
    if declared:
        return declared
    return name if name in _extras(path) else ""


def add(config_path: str | None, name: str) -> int:
    """Enable a gateway and install its declared extra when this checkout has one."""
    path = _path(config_path)
    if name not in _names():
        raise ValueError(f"unknown gateway '{name}'")
    extra = _extra_for(path, name)
    if extra:
        code = install_extra(extra, path.parent)
        if code:
            return code
    return set_enabled(config_path, name, True)


def _onboarding(config_path: str | None, name: str, command: str) -> int:
    from ..gateways import gateway_cli

    result = gateway_cli(name, command, config_path)
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    return int(result or 0)


def connect(config_path: str | None, name: str) -> int:
    """ADR-0002 §4.5: install, enable, seed policy, then run the gateway's own onboarding."""
    code = add(config_path, name)
    if code:
        return code
    info = _info(name)
    onboarding = info.descriptor.onboarding if info is not None else ""
    if not onboarding:
        print(f"{name}: no onboarding command; nothing to pair", flush=True)
        return 0
    print(f"{name}: onboarding ({onboarding}); the code stays on this terminal", flush=True)
    result = _onboarding(config_path, name, onboarding)
    if not result:
        print(
            f"{name}: link your own address explicitly before it can enter a turn: "
            f"monoagens people add {name} <your address> --role administrator",
            flush=True,
        )
    return result


def pair(config_path: str | None, name: str) -> int:
    if _info(name) is None:
        raise ValueError(f"unknown gateway '{name}'")
    return _onboarding(config_path, name, "pair")


def unpair(config_path: str | None, name: str) -> int:
    """Remove the paired session; a gateway without an `unpair` hook says so plainly."""
    from ..engine.config import ConfigError

    if _info(name) is None:
        raise ValueError(f"unknown gateway '{name}'")
    try:
        return _onboarding(config_path, name, "unpair")
    except ConfigError as exc:
        print(f"{name}: {exc}", flush=True)
        return 2


def delete(config_path: str | None, name: str) -> int:
    """Remove a gateway completely: code switch, config block, role blocks, and session."""
    if _info(name) is None:
        raise ValueError(f"unknown gateway '{name}'")
    path = _path(config_path)
    if path.is_file():
        body = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        gateways = body.get("gateways")
        if isinstance(gateways, dict):
            gateways.pop(name, None)
            update_document(path, {"gateways": gateways})
        roles_path = _roles_path(path)
        roles = _read_roles(roles_path)
        if roles is not None:
            updates: dict = {}
            for role, block in roles.items():
                if not isinstance(block, dict):
                    continue
                blocks = block.get("gateways")
                if isinstance(blocks, dict) and name in blocks:
                    blocks.pop(name, None)
                    updates[f"roles.{role}.gateways"] = blocks
            if updates:
                update_document(roles_path, updates)
    removed = _remove_session(name)
    if removed:
        print(f"{name}: removed session {removed}", flush=True)
    extra = _extra_for(path, name)
    if extra and shutil.which("uv") and (path.parent / "pyproject.toml").is_file():
        subprocess.run(
            [shutil.which("uv") or "uv", "sync", "--locked", "--no-dev"],
            cwd=path.parent,
            check=False,
        )
        print(f"{name}: extra '{extra}' removed from the environment", flush=True)
    print(f"{name}: deleted", flush=True)
    service.notify(config_path, reason=f"gateway {name} deleted")
    return 0


def _remove_session(name: str) -> Path | None:
    """Delete `~/.monoagens/sessions/<name>` only when it proves inside the session root."""
    root = SESSIONS.resolve()
    target = (SESSIONS / name)
    try:
        resolved = target.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError(f"refusing to remove outside the session root: {target}")
    if target.is_symlink():
        raise ValueError(f"refusing to remove a symbolic link: {target}")
    if not target.exists():
        return None
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return target


def doctor(config_path: str | None = None, *, fix: bool = False) -> int:
    """Report missing gateway policy blocks; `--fix` writes the seed (ADR-0002 §4.4)."""
    path = _path(config_path)
    if not path.is_file():
        print(f"config.yaml not found: {path}", flush=True)
        return 2
    roles_path = _roles_path(path)
    roles = _read_roles(roles_path)
    if roles is None:
        print(f"no roles mapping: {roles_path}", flush=True)
        return 2
    enabled = [name for name, on in _enabled(path).items() if on]
    missing: list[tuple[str, str]] = [
        (gateway, role) for gateway in enabled for role in _missing(roles, gateway)
    ]
    if not missing:
        print(f"policy: every enabled gateway has a block for all {len(roles)} roles", flush=True)
        return 0
    for gateway, role in missing:
        print(f"missing: roles.{role}.gateways.{gateway}", flush=True)
    if not fix:
        print("repair with: monoagens gateway doctor --fix", flush=True)
        return 1
    for gateway in {item[0] for item in missing}:
        materialize(config_path, gateway)
    service.notify(config_path, reason="gateway policy seeded")
    print(f"policy: wrote {len(missing)} missing block(s) to {roles_path}", flush=True)
    return 0


def set_enabled(config_path: str | None, name: str, enabled: bool) -> int:
    known = _names()
    if name not in known:
        raise ValueError(f"unknown gateway '{name}'")
    path = _path(config_path)
    update_document(
        path,
        {"gateways": {name: {"enabled": bool(enabled)}}},
        comments={f"gateways.{name}.enabled": note(f"gateways.{name}.enabled", bool(enabled))},
    )
    print(f"{name}: {'enabled' if enabled else 'disabled'}")
    if enabled:
        extra = _extra_for(path, name)
        if extra and not (path.parent / "pyproject.toml").is_file():
            print(f"install its dependency: uv sync --locked --no-dev --extra {extra}")
        seeded = materialize(config_path, name)
        if seeded:
            print(f"{name}: wrote {seeded} role policy block(s) to roles.yaml")
        info = _info(name)
        onboarding = str(getattr(getattr(info, "descriptor", None), "onboarding", "") or "")
        if onboarding:
            print(f"{name}: pair it with `monoagens gateway {onboarding} {name}`")
    service.notify(
        config_path, reason=f"gateway {name} {'enabled' if enabled else 'disabled'}"
    )
    return 0


# ------------------------------------------------------------- composition seam


def runtime_gateways(config, *, db, history, windows):
    """Build the runtime gateway bodies; the entry module never imports the gateway package."""
    from ..gateways import build_gateways

    return build_gateways(config, db=db, history=history, windows=windows)


def ensure_core(body) -> None:
    from ..gateways import ensure_core as _ensure_core

    _ensure_core(body)


def ensure_schemas(db) -> list[str]:
    from ..gateways import ensure_schemas as _ensure_schemas

    return _ensure_schemas(db)


def backfill(config_path: str | None):
    from ..gateways import cli_entry

    return cli_entry("backfill", config_path)
