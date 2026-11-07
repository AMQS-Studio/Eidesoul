"""Auto-indexed capability catalog for setup screens.

The installer and wizard never carry their own tool, gateway, or voice lists. Tools come from
the same registry the engine executes, gateways from their packages/entry points, voice parts
from `VoiceConfig`, and search backends from the search tool's backend record.
"""

import argparse
import ast
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from pathlib import Path


@dataclass(frozen=True)
class CatalogItem:
    name: str
    label: str
    detail: str = ""
    parameters: dict = field(default_factory=dict)


def _literal(call: ast.Call, key: str):
    for keyword in call.keywords:
        if keyword.arg != key:
            continue
        try:
            return ast.literal_eval(keyword.value)
        except (ValueError, SyntaxError):
            return None
    return None


def _gateway_tools() -> list[CatalogItem]:
    """Gateway-owned tools discovered from `TOOL = Handler(...)`, without importing them."""
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
                name = _literal(node.value, "name")
                if not isinstance(name, str):
                    continue
                description = _literal(node.value, "description")
                parameters = _literal(node.value, "parameters")
                found.append(
                    CatalogItem(
                        name,
                        name,
                        description
                        if isinstance(description, str) and description.strip()
                        else f"gateway:{folder.name}",
                        parameters if isinstance(parameters, dict) else {},
                    )
                )
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
    for item in fields(VoiceConfig):
        factory = item.default_factory
        if factory is MISSING or not callable(factory):
            continue
        value = factory()
        if is_dataclass(value) and "enabled" in getattr(value, "__dataclass_fields__", {}):
            found.append(CatalogItem(item.name, item.name, ""))
    return found


def default_voice_parts() -> tuple[str, ...]:
    """The seed soul's enabled parts, so the checkbox starts from the shipped template."""
    import yaml

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


def websearch_choices() -> list[CatalogItem]:
    """Every `web_search.provider` the loader accepts, with the env names it reads.

    The list is the loader's own `WEB_SEARCH_PROVIDERS`, so the wizard and the installer can
    never offer a name the config would reject; `off` closes the menu.
    """
    from ..engine.config import WEB_SEARCH_PROVIDERS
    from .templates import WEB_SEARCH_API_KEYS, WEB_SEARCH_ENGINE_KEYS

    found: list[CatalogItem] = []
    for name in sorted(WEB_SEARCH_PROVIDERS):
        if name == "off":
            continue
        key = WEB_SEARCH_API_KEYS.get(name, "")
        engine = WEB_SEARCH_ENGINE_KEYS.get(name, "")
        if name == "searxng":
            detail = "operator-owned JSON endpoint; no key"
        elif key:
            detail = f"key in {key}" + (f"; engine id in {engine}" if engine else "")
        else:
            detail = "public result pages; no key; can rate-limit"
        found.append(CatalogItem(name, name, detail))
    found.append(CatalogItem("off", "off", "disable web search and research"))
    return found


def default_gateways() -> tuple[str, ...]:
    """The gateways the template enables by default."""
    from .templates import GATEWAY

    return tuple(name for name, value in GATEWAY["gateways"].items() if value.get("enabled"))


# --- install-time choices -----------------------------------------------------------------
#
# `install.sh` asks these before `uv sync` runs, so they read the canonical definitions with
# `ast` when the runtime dependencies are not importable yet. The parsed files are the same
# ones the engine imports, so the shell never carries a second, drifting list.


def _source(*parts: str) -> Path:
    return Path(__file__).resolve().parents[1].joinpath(*parts)


def _assigned(path: Path, name: str) -> ast.expr | None:
    """The value node of a module-level `NAME = ...` assignment."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return value
    return None


def _constant(path: Path, name: str):
    """A literal constant, or the literal first argument of a `frozenset({...})`/`set(...)`."""
    value = _assigned(path, name)
    if value is None:
        return None
    for node in (value, value.args[0] if isinstance(value, ast.Call) and value.args else None):
        if node is None:
            continue
        try:
            return ast.literal_eval(node)
        except (ValueError, SyntaxError):
            continue
    return None


def _source_provider_names() -> list[str]:
    value = _assigned(_source("providers", "__init__.py"), "PROVIDERS")
    if not isinstance(value, ast.Dict):
        return []
    return sorted(
        str(ast.literal_eval(key)) for key in value.keys if isinstance(key, ast.Constant)
    )


def _source_websearch_names() -> list[str]:
    value = _constant(_source("engine", "config.py"), "WEB_SEARCH_PROVIDERS")
    if isinstance(value, (set, frozenset, list, tuple)):
        return sorted(str(name) for name in value)
    return []


def _source_gateway_names() -> list[str]:
    root = _source("gateways")
    if not root.is_dir():
        return []
    return sorted(
        folder.name for folder in root.iterdir() if (folder / "gateway.py").is_file()
    )


def provider_names() -> list[str]:
    """Every LLM provider id, from `providers.PROVIDERS`."""
    try:
        from ..providers import PROVIDERS
    except Exception:
        return _source_provider_names()
    return sorted(PROVIDERS)


def websearch_names() -> list[str]:
    """Every `web_search.provider` value, from the loader's own choices."""
    try:
        from ..engine.config import WEB_SEARCH_PROVIDERS
    except Exception:
        return _source_websearch_names()
    return sorted(WEB_SEARCH_PROVIDERS)


def gateway_names() -> list[str]:
    """Every gateway this checkout carries, from `gateways.gateway_index()`."""
    try:
        from .gateway_index import gateway_index
    except Exception:
        return _source_gateway_names()
    try:
        return sorted(gateway_index())
    except Exception:
        return _source_gateway_names()


def installer_choices() -> dict[str, list[str]]:
    """The three lists `install.sh` validates and offers, one payload."""
    return {
        "providers": provider_names(),
        "websearch": websearch_names(),
        "gateways": gateway_names(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m monoagens.admin.catalog")
    parser.add_argument(
        "kind",
        nargs="?",
        default="choices",
        choices=("choices", "providers", "websearch", "gateways"),
    )
    args = parser.parse_args(argv)
    loaders = {
        "providers": provider_names,
        "websearch": websearch_names,
        "gateways": gateway_names,
    }
    if args.kind == "choices":
        for name, loader in loaders.items():
            print(f"{name}={','.join(loader())}")
        return 0
    print("\n".join(loaders[args.kind]()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
