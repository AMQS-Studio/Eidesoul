"""Gateway configuration commands: edit the host file without touching engine internals."""

import asyncio
import copy
import inspect
import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml


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


def _enabled(path: Path) -> dict[str, bool]:
    body = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = body.get("gateways") or {}
    return {
        str(name): bool(value.get("enabled")) if isinstance(value, dict) else False
        for name, value in raw.items()
    }


def _read_roles(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    roles = raw.get("roles") if isinstance(raw, dict) else None
    return roles if isinstance(roles, dict) else None


def _write_roles(path: Path, roles: dict) -> None:
    """ADR-0046 §4.1: the operator file is written by the command that changes it."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump({"roles": roles}, sort_keys=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _seed(role: str) -> dict:
    from .templates import CLOSED_GATEWAY_POLICY, GATEWAY_POLICY

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


def materialize(config_path: str | None, gateway: str) -> int:
    """Write the seed block for every role that lacks one (ADR-0046 §4.4)."""
    path = _path(config_path)
    roles_path = _roles_path(path)
    roles = _read_roles(roles_path)
    if roles is None:
        return 0
    missing = _missing(roles, gateway)
    if not missing:
        return 0
    for role in missing:
        body = roles.get(role)
        if not isinstance(body, dict):
            continue
        blocks = body.get("gateways")
        if not isinstance(blocks, dict):
            blocks = {}
            body["gateways"] = blocks
        blocks[gateway] = _seed(role)
    _write_roles(roles_path, roles)
    return len(missing)


def _refresh_cache(config_path: str | None) -> None:
    """Import the projection after a file write; a missing database is not an error here."""
    from ..engine.config import ConfigError, load_host, resolve_database_url
    from ..engine.db import Database
    from ..engine.stores.documents import DocumentStore

    try:
        host, paths = load_host(config_path)
    except ConfigError:
        return
    url = resolve_database_url(host.database)
    if not url:
        return
    db = Database(
        url, pool_size=host.database.pool_size, pool_timeout=host.database.pool_timeout
    )
    try:
        DocumentStore(db, paths, sync=host.sync_documents).import_from_files()
    except Exception:
        return
    finally:
        db.dispose()


def list_gateways(config_path: str | None = None) -> int:
    path = _path(config_path)
    enabled = _enabled(path) if path.is_file() else {}
    for name, source in _names().items():
        print(f"{name}: {'enabled' if enabled.get(name) else 'disabled'} ({source})")
    return 0


def _block(lines: list[str]) -> tuple[int, int] | None:
    for index, line in enumerate(lines):
        if re.match(r"^gateways:\s*(\{\}\s*)?(#.*)?$", line):
            end = len(lines)
            for cursor in range(index + 1, len(lines)):
                if lines[cursor].strip() and not lines[cursor].startswith((" ", "\t")):
                    end = cursor
                    break
            return index, end
    return None


def _extras(path: Path) -> set[str]:
    try:
        import tomllib

        body = tomllib.loads((path.parent / "pyproject.toml").read_text(encoding="utf-8"))
        return set((body.get("project") or {}).get("optional-dependencies") or {})
    except (OSError, ValueError):
        return set()


def add(config_path: str | None, name: str) -> int:
    """Enable a gateway and install its declared extra when this checkout has one."""
    path = _path(config_path)
    extras = _extras(path)
    info = _info(name)
    extra = ""
    if info is not None:
        extra = info.descriptor.extra
    if not extra and name in extras:
        extra = name
    if extra:
        if not (path.parent / "pyproject.toml").is_file():
            print(f"install its dependency: uv sync --no-dev --extra {extra}")
            return set_enabled(config_path, name, True)
        uv = shutil.which("uv")
        if uv is None:
            print(f"install its dependency: uv sync --locked --no-dev --extra {extra}")
        else:
            completed = subprocess.run(
                [uv, "sync", "--locked", "--no-dev", "--extra", extra],
                cwd=path.parent,
                check=False,
            )
            if completed.returncode:
                return completed.returncode
    return set_enabled(config_path, name, True)


def connect(config_path: str | None, name: str) -> int:
    """ADR-0046 §4.5: install, enable, seed policy, then run the gateway's own onboarding."""
    from ..gateways import gateway_cli

    code = add(config_path, name)
    if code:
        return code
    info = _info(name)
    onboarding = info.descriptor.onboarding if info is not None else ""
    if not onboarding:
        print(f"{name}: no onboarding command; nothing to pair", flush=True)
        return 0
    print(f"{name}: onboarding ({onboarding}); the code stays on this terminal", flush=True)
    result = gateway_cli(name, onboarding, config_path)
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    if not result:
        print(
            f"{name}: link your own address explicitly before it can enter a turn: "
            f"monoagens people add {name} <your address> --role administrator",
            flush=True,
        )
    return int(result or 0)


def doctor(config_path: str | None = None, *, fix: bool = False) -> int:
    """Report missing gateway policy blocks; `--fix` writes the seed (ADR-0046 §4.4)."""
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
        (gateway, role)
        for gateway in enabled
        for role in _missing(roles, gateway)
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
    _refresh_cache(config_path)
    print(f"policy: wrote {len(missing)} missing block(s) to {roles_path}", flush=True)
    return 0


def set_enabled(config_path: str | None, name: str, enabled: bool) -> int:  # noqa: PLR0912
    known = _names()
    if name not in known:
        raise ValueError(f"unknown gateway '{name}'")
    path = _path(config_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    block = _block(lines)
    entry = f"  {name}:"
    flag = f"    enabled: {'true' if enabled else 'false'}"
    if block is None:
        lines.extend(["gateways:", entry, flag])
    else:
        start, end = block
        if re.search(r":\s*\{\s*\}", lines[start]):
            lines[start] = "gateways:"
            lines[start + 1 : start + 1] = [entry, flag]
            end += 2
        else:
            entry_at = next(
                (
                    index
                    for index in range(start + 1, end)
                    if re.match(rf"^  {re.escape(name)}:\s*$", lines[index])
                ),
                None,
            )
            if entry_at is None:
                lines[end:end] = [entry, flag]
            else:
                section_end = end
                for cursor in range(entry_at + 1, end):
                    if re.match(r"^  \S", lines[cursor]):
                        section_end = cursor
                        break
                flag_at = next(
                    (
                        index
                        for index in range(entry_at + 1, section_end)
                        if re.match(r"^    enabled:\s*", lines[index])
                    ),
                    None,
                )
                if flag_at is None:
                    lines.insert(entry_at + 1, flag)
                else:
                    lines[flag_at] = flag
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{name}: {'enabled' if enabled else 'disabled'}")
    if enabled and name in _extras(path):
        print(f"install its dependency: uv sync --locked --no-dev --extra {name}")
    if enabled:
        seeded = materialize(config_path, name)
        if seeded:
            print(f"{name}: wrote {seeded} role policy block(s) to roles.yaml")
        _refresh_cache(config_path)
    return 0
