"""Auto-indexed capability catalog for setup screens.

The installer and wizard never carry their own tool, gateway, or voice lists. Tools come from
the same registry the engine executes, gateways from their packages/entry points, voice parts
from `VoiceConfig`, and search backends from the search tool's backend record.
"""

import ast
import importlib.util
from dataclasses import MISSING, dataclass, fields, is_dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class CatalogItem:
    name: str
    label: str
    detail: str = ""


def _gateway_tools() -> list[CatalogItem]:
    """Gateway-owned tools discovered from `TOOL = Handler(name=...)`, without importing them."""
    root = Path(__file__).resolve().parents[1] / "gateways"
    found: list[CatalogItem] = []
    for folder in sorted(root.iterdir()):
        tool_dir = folder / "tools"
        if not tool_dir.is_dir():
            continue
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
                if isinstance(name, str):
                    found.append(CatalogItem(name, name, f"gateway:{folder.name}"))
    return found


def tools() -> list[CatalogItem]:
    """Every non-hidden core tool plus gateway-owned tools, indexed without a hand list."""
    from ..engine.tools import REGISTRY

    found = [
        CatalogItem(handler.name, handler.name, handler.description)
        for handler in REGISTRY.by_name.values()
        if not handler.hidden
    ]
    known = {item.name for item in found}
    for item in _gateway_tools():
        if item.name not in known:
            found.append(item)
            known.add(item.name)
    return found


def tool_names() -> set[str]:
    return {item.name for item in tools()}


def default_tools() -> tuple[str, ...]:
    """The member role's shipped defaults, used only as a starting checkbox state."""
    from .templates import ROLES

    return tuple(ROLES["member"]["tools"])


def gateways() -> list[CatalogItem]:
    """Discover builtin gateway packages and `monoagens.gateways` entry points."""
    from .gateway_index import gateway_index

    return [
        CatalogItem(
            info.name,
            info.name,
            f"{info.source}; module {info.module}",
        )
        for info in gateway_index().values()
    ]


def voice_parts() -> list[CatalogItem]:
    """Voice sub-sections that expose their own `enabled` switch, indexed from `VoiceConfig`."""
    from ..engine.soul import VoiceConfig

    found: list[CatalogItem] = []
    for field in fields(VoiceConfig):
        factory = field.default_factory
        if factory is MISSING or not callable(factory):
            continue
        value = factory()
        if is_dataclass(value) and "enabled" in getattr(value, "__dataclass_fields__", {}):
            found.append(CatalogItem(field.name, field.name, ""))
    return found


def default_voice_parts() -> tuple[str, ...]:
    """The seed soul's enabled parts, so the checkbox starts from the shipped template."""
    from .templates import soul_yaml

    body = yaml.safe_load(soul_yaml("en", "en", "UTC")) or {}
    voice = body.get("voice") or {}
    return tuple(
        name
        for name, value in voice.items()
        if isinstance(value, dict) and value.get("enabled") is True
    )


def websearch_backends() -> list[CatalogItem]:
    """Search backends the current install can actually run."""
    from ..engine.tools.web_search import backends

    return [
        CatalogItem(
            item.name,
            item.label,
            f"{item.detail}; {'URL required' if item.needs_url else 'no key'}",
        )
        for item in backends()
    ]


def default_gateways() -> tuple[str, ...]:
    """The gateways the template enables by default."""
    from .templates import GATEWAY

    return tuple(name for name, value in GATEWAY["gateways"].items() if value.get("enabled"))


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False
