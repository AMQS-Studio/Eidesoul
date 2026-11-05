"""Gateway configuration commands: edit the host file without touching engine internals."""

import re
import shutil
import subprocess
from pathlib import Path

import yaml


def _path(value: str | None) -> Path:
    return Path(value or "config.yaml").expanduser()


def _names() -> dict[str, str]:
    from .gateway_index import gateway_index

    return {name: info.source for name, info in gateway_index().items()}


def _enabled(path: Path) -> dict[str, bool]:
    body = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = body.get("gateways") or {}
    return {
        str(name): bool(value.get("enabled")) if isinstance(value, dict) else False
        for name, value in raw.items()
    }


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
    if name in extras:
        uv = shutil.which("uv")
        if uv is None:
            print(f"install its dependency: uv sync --locked --no-dev --extra {name}")
        else:
            completed = subprocess.run(
                [uv, "sync", "--locked", "--no-dev", "--extra", name],
                cwd=path.parent,
                check=False,
            )
            if completed.returncode:
                return completed.returncode
    return set_enabled(config_path, name, True)


def set_enabled(config_path: str | None, name: str, enabled: bool) -> int:
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
    return 0
