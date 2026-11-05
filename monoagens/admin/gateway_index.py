"""Auto-index gateway packages for the admin surface without importing a gateway module."""

import ast
import importlib.util
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path


@dataclass(frozen=True)
class GatewayInfo:
    name: str
    module: str
    attribute: str
    source: str = "builtin"
    can_pair: bool = False


def _has_pair(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "pair"
        for node in ast.walk(tree)
    )


def _class_name(path: Path, gateway_name: str) -> str | None:
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


def _builtin_index() -> dict[str, GatewayInfo]:
    found: dict[str, GatewayInfo] = {}
    root = Path(__file__).resolve().parents[1] / "gateways"
    for folder in sorted(root.iterdir()):
        module_path = folder / "gateway.py"
        if not folder.is_dir() or not module_path.is_file():
            continue
        attribute = _class_name(module_path, folder.name)
        if attribute is None:
            continue
        found[folder.name] = GatewayInfo(
            folder.name,
            f"monoagens.gateways.{folder.name}.gateway",
            attribute,
            can_pair=_has_pair(folder / "cli.py"),
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
        found[str(entry.name)] = GatewayInfo(
            str(entry.name), module, attribute, "entry point"
        )
    return found


def gateway_index() -> dict[str, GatewayInfo]:
    """Every gateway visible to this install, indexed from packages and entry points."""
    found = _builtin_index()
    for name, info in _entrypoint_index().items():
        found.setdefault(name, info)
    return found


def installed(name: str) -> bool:
    info = gateway_index().get(name)
    if info is None:
        return False
    try:
        return importlib.util.find_spec(info.module) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False
