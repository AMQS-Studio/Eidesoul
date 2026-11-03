"""ADR-0000 §4.7: the engine holds no transport vocabulary and imports no gateway package."""


import ast
import re
from pathlib import Path

import pytest

from eidesoul.engine.inbound import (
    CORE_MEMBERS,
    Action,
    CoreDefaults,
    Directory,
    GatewayCore,
    Percept,
    Rich,
)

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "eidesoul" / "engine"

# The process entry point and the admin CLI build the gateway list; the engine never imports
# them, so the "no gateway import" rule applies to every other module outside gateways/.
COMPOSITION = {"eidesoul/__main__.py", "eidesoul/admin/database.py"}

# The only body words the engine may know: the documented `Percept.hints` keys.
HINT_KEYS = frozenset({"dm", "mention", "reply", "group", "presence", "self", "roles"})

# One token, one forbidden transport concept (ADR-0000 §4.4).
FORBIDDEN = (
    "neonize",
    "whatsapp",
    "@g.us",
    "@s.whatsapp.net",
    "chatmessage",
    "is_dm",
    "is_mention",
    "is_reply",
    "chat_id",
    "chat_eras",
    "chat_people",
)
JID = re.compile(r"(?<![a-z0-9])jid(?![a-z0-9])")
WA = re.compile(r"(?<![a-z0-9])wa_")


def watched_texts(node: ast.AST):
    """Every identifier and string literal, except the documented hint keys."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            yield child.lineno, child.id
        elif isinstance(child, ast.Attribute):
            yield child.lineno, child.attr
        elif isinstance(child, ast.arg) or (isinstance(child, ast.keyword) and child.arg):
            yield child.lineno, child.arg
        elif isinstance(child, ast.alias):
            yield child.lineno, child.name.rsplit(".", 1)[-1]
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            yield child.lineno, child.value
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield child.lineno, child.name


def vocabulary(text: str) -> list[str]:
    if text in HINT_KEYS:
        return []
    body = text.casefold()
    found = [token for token in FORBIDDEN if token in body]
    if JID.search(body):
        found.append("jid")
    if WA.search(body):
        found.append("wa_")
    return found


def test_no_transport_vocabulary_in_the_engine() -> None:
    hits: list[str] = []
    for path in sorted(ENGINE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, text in watched_texts(tree):
            for token in vocabulary(text):
                hits.append(f"{path.relative_to(ROOT)}:{line}: {token} in {text!r}")
    assert not hits, "transport vocabulary in the engine:\n" + "\n".join(hits)


def test_nothing_outside_gateways_imports_a_transport_or_a_gateway() -> None:
    hits: list[str] = []
    for path in sorted((ROOT / "eidesoul").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if "gateways" in path.relative_to(ROOT / "eidesoul").parts:
            continue
        if relative in COMPOSITION:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = ["." * node.level + module] if node.level else [module]
            for name in names:
                parts = name.lstrip(".").split(".")
                if parts[0] == "neonize":
                    hits.append(f"{path.relative_to(ROOT)}:{node.lineno}: imports {name}")
                if parts[0] == "gateways" or name.startswith("eidesoul.gateways"):
                    hits.append(f"{path.relative_to(ROOT)}:{node.lineno}: imports {name}")
                if (
                    name.startswith("eidesoul.")
                    and path.is_relative_to(ENGINE)
                    and len(parts) > 1
                    and parts[1] != "providers"
                ):
                    hits.append(f"{path.relative_to(ROOT)}:{node.lineno}: engine imports {name}")
    assert not hits, "gateway or transport import outside gateways/:\n" + "\n".join(hits)


def test_the_engine_tool_catalog_imports_no_gateway() -> None:
    path = ENGINE / "tools" / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.append((node.level, node.module or ""))
        elif isinstance(node, ast.Import):
            imported.extend((0, alias.name) for alias in node.names)
    assert all("gateway" not in module for _level, module in imported), imported


FORBIDDEN_FUTURE = "from __future__ " + "import annotations"


def test_no_module_relies_on_future_annotations() -> None:
    """ADR-0031 §4.7: on 3.14 annotations are lazy, so the import is dead weight and banned."""
    roots = [ROOT / name for name in ("eidesoul", "tests", "scripts", "plugins", "migrations")]
    offenders = sorted(
        path.relative_to(ROOT).as_posix()
        for base in roots
        for path in base.rglob("*.py")
        if path.name != "test_architecture.py"
        and FORBIDDEN_FUTURE in path.read_text(encoding="utf-8")
    )
    assert offenders == []


def test_percept_is_exactly_the_seam() -> None:
    fields = [item.name for item in Percept.__dataclass_fields__.values()]
    assert fields == ["source", "thread", "actor", "kind", "payload", "hints", "raw"]
    action = [item.name for item in Action.__dataclass_fields__.values()]
    assert action == ["capability", "target", "payload", "approval_class"]
    with pytest.raises(ValueError, match="approval_class must be one of"):
        Action(capability="send_text", target="t", payload="p", approval_class="magic")


def test_the_three_protocols_accept_the_right_fakes() -> None:
    from support import DirectoryFakeGateway, FakeGateway, RichFakeGateway

    core = FakeGateway()
    assert isinstance(core, GatewayCore)
    assert not isinstance(core, Rich)
    assert not isinstance(core, Directory)
    assert core.chat_name("thread") == "Chat"

    rich = RichFakeGateway()
    assert isinstance(rich, Rich)
    assert not isinstance(rich, Directory)

    directory = DirectoryFakeGateway(members=None)
    assert isinstance(directory, Directory)
    assert not isinstance(directory, Rich)
    assert directory.participants("thread") is None


def test_the_old_seam_names_are_gone() -> None:
    from eidesoul.engine import context, inbound

    assert not hasattr(inbound, "Inbound")
    assert not hasattr(context, "ChatMessage")
    assert not hasattr(inbound, "Gateway")


def test_core_defaults_labels_a_thread_by_its_id() -> None:
    class Bare(CoreDefaults):
        pass

    assert Bare().chat_name("thread-1") == "thread-1"
    assert set(CORE_MEMBERS) >= {"name", "send_text", "tools", "commands", "run", "stop"}


def test_no_blocking_db_block_inside_an_async_def() -> None:
    """ADR-0017 §4.1: `with db.connect()` belongs in an offloaded helper, never in `async def`."""
    offenders: list[str] = []
    for path in ENGINE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.With):
                    continue
                for item in inner.items:
                    call = item.context_expr
                    if (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "connect"
                    ):
                        offenders.append(f"{path.relative_to(ROOT)}:{inner.lineno}")
    assert offenders == []
