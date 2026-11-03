"""ADR-0028 §7: plugin manifests, digest verification, one import, and the lifecycle."""


import asyncio
import importlib
import sys
from pathlib import Path

import pytest
import yaml
from support import build_config

from eidesoul.engine import plugins
from eidesoul.engine.config import PluginSettings
from eidesoul.engine.registry import Registry
from eidesoul.engine.roles import Role
from eidesoul.engine.tools import REGISTRY as CORE_TOOLS
from eidesoul.engine.tools import ToolContext, execute

ROOT = Path(__file__).resolve().parents[1]
BAD_DIGEST = "sha256:" + "0" * 64
CREDITS = [
    {
        "license": "Apache-2.0",
        "author": "Fixture Authors",
        "source": "https://example.test/fixture",
        "use": "fixture dependency",
    }
]

TOOL = '''"""Fixture plugin tool."""

from eidesoul.engine.registry import Handler


async def _run(args, ctx):
    return "plugin-ok:" + str(args.get("text", ""))


TOOL = Handler(
    name="{name}",
    family="read",
    description="Fixture plugin tool.",
    fn=_run,
    parameters={{
        "type": "object",
        "properties": {{"text": {{"type": "string"}}}},
        "additionalProperties": False,
    }},
)
'''


@pytest.fixture(autouse=True)
def fresh_plugin_modules():
    """Each test starts with no imported plugin module, so "lazy" is observable."""
    for name in [item for item in sys.modules if item.startswith(plugins.MODULE + "_")]:
        del sys.modules[name]


def write_plugin(
    root: Path,
    name: str = "demo",
    tools: tuple[str, ...] = ("echo",),
    *,
    digest: str | None = None,
    secrets: tuple[str, ...] = (),
    raises: bool = False,
    broken_entry: bool = False,
) -> Path:
    """Write a complete local plugin tree and stamp its manifest with the real digest."""
    folder = root / name
    (folder / "tools").mkdir(parents=True)
    (folder / "tools" / "__init__.py").write_text("", encoding="utf-8")
    for tool in tools:
        (folder / "tools" / f"{tool}.py").write_text(TOOL.format(name=tool), encoding="utf-8")
    imports = "\n".join(f"from .tools.{tool} import TOOL as {tool.upper()}" for tool in tools)
    returned = ", ".join(tool.upper() for tool in tools)
    action = 'raise RuntimeError("boom")' if raises else (
        f'return {{"tools": [{returned}], "commands": [], "gateways": []}}'
    )
    entry = (
        '"""Fixture plugin entry."""\n'
        "from eidesoul.engine.registry import Handler\n\n"
        f"{imports}\n\n\n"
        "def register() -> dict[str, list[Handler]]:\n"
        f"    {action}\n"
    )
    (folder / "plugin.py").write_text(entry, encoding="utf-8")
    if broken_entry:
        (folder / "plugin.py").write_text(
            '"""Fixture plugin entry."""\nraise RuntimeError("import-boom")\n',
            encoding="utf-8",
        )
    actual = plugins.tree_digest(folder)
    manifest = {
        "name": name,
        "version": "1.0.0",
        "engine": ">=0.0.0,<1.0",
        "entry": "plugin.py:register",
        "digest": digest or actual,
        "provides": {"tools": list(tools), "commands": [], "gateways": []},
        "requires": [],
        "secrets": list(secrets),
        "credits": CREDITS,
    }
    (folder / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return folder


def settings(
    root: Path, config_path: Path | None = None, **entries
) -> plugins.PluginLoader:
    return plugins.PluginLoader(
        root=Path(root),
        fetch=False,
        plugins={name: PluginSettings(**body) for name, body in entries.items()},
        config_path=config_path,
    )


def write_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"plugins": {"demo": {"enabled": False}}}), encoding="utf-8")
    return path


def test_a_bad_digest_is_refused_before_import(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    write_plugin(root, digest=BAD_DIGEST)

    loaded = plugins.load(settings(root, demo={"enabled": True}))

    assert loaded.state("demo") == "unavailable"
    assert loaded.tools == ()
    reason = next(item.reason for item in loaded.plugins if item.name == "demo")
    assert "digest mismatch" in reason
    assert plugins.module_name("demo") not in sys.modules


def test_a_digest_pin_that_does_not_match_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    write_plugin(root)

    loaded = plugins.load(settings(root, demo={"enabled": True, "digest": BAD_DIGEST}))

    assert loaded.state("demo") == "unavailable"
    assert "pinned" in next(item.reason for item in loaded.plugins if item.name == "demo")


def test_a_missing_plugin_reports_unavailable(tmp_path: Path) -> None:
    root = tmp_path / "plugins"

    loaded = plugins.load(settings(root, demo={"enabled": True}))

    assert loaded.state("demo") == "unavailable"
    assert loaded.tools == ()
    assert "not installed" in next(item.reason for item in loaded.plugins if item.name == "demo")
    assert plugins.states(settings(root, demo={"enabled": False}))[0].state == "absent"


def test_a_valid_local_plugin_registers_and_is_callable(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    write_plugin(root)

    loaded = plugins.load(settings(root, demo={"enabled": True}))

    assert loaded.state("demo") == "enabled"
    assert [handler.name for handler in loaded.tools] == ["echo"]
    registry = Registry([*CORE_TOOLS.by_name.values(), *loaded.tools], "tool")
    config = build_config(tmp_path, roles={"member": Role("member", tools=("echo",))})
    ctx = ToolContext(
        config,
        config.roles.default_role(),
        ("echo",),
        thread_id="thread",
        sender="p1",
    )
    specs = {handler.name: handler for handler in registry.by_name.values()}
    assert asyncio.run(execute(specs, "echo", {"text": "hi"}, ctx)) == "plugin-ok:hi"


def test_the_loader_does_not_import_code_at_inspection_time(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    write_plugin(root)

    assert plugins.module_name("demo") not in sys.modules
    assert plugins.states(settings(root, demo={"enabled": True}))[0].state == "enabled"
    assert plugins.module_name("demo") not in sys.modules

    plugins.load(settings(root, demo={"enabled": True}))

    assert plugins.module_name("demo") in sys.modules


def test_enable_with_fetch_false_and_no_code_fails_and_changes_nothing(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    before = config_path.read_text(encoding="utf-8")
    root = tmp_path / "plugins"

    with pytest.raises(plugins.PluginError, match="plugin.fetch is false"):
        plugins.enable(settings(root, config_path, demo={"enabled": False}), "demo")

    assert config_path.read_text(encoding="utf-8") == before
    assert not (root / "demo").exists()


def test_a_shadowed_core_tool_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    write_plugin(root, tools=("read",))

    loaded = plugins.load(settings(root, demo={"enabled": True}))

    assert loaded.state("demo") == "unavailable"
    assert loaded.tools == ()
    assert "collides" in next(item.reason for item in loaded.plugins if item.name == "demo")


def test_a_missing_secret_reports_the_env_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EIDESOUL_FIXTURE_TOKEN", raising=False)
    root = tmp_path / "plugins"
    write_plugin(root, secrets=("EIDESOUL_FIXTURE_TOKEN",))

    loaded = plugins.load(settings(root, demo={"enabled": True}))

    assert loaded.state("demo") == "unavailable"
    reason = next(item.reason for item in loaded.plugins if item.name == "demo")
    assert "EIDESOUL_FIXTURE_TOKEN" in reason


def test_a_plugin_that_raises_during_registration_is_unavailable(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    write_plugin(root, raises=True)

    loaded = plugins.load(settings(root, demo={"enabled": True}))

    assert loaded.state("demo") == "unavailable"
    assert "boom" in next(item.reason for item in loaded.plugins if item.name == "demo")


def test_a_broken_entry_import_is_unavailable(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    write_plugin(root, broken_entry=True)

    loaded = plugins.load(settings(root, demo={"enabled": True}))

    assert loaded.state("demo") == "unavailable"
    assert "import-boom" in next(
        item.reason for item in loaded.plugins if item.name == "demo"
    )


def test_enable_disable_remove(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    folder = write_plugin(root)
    config_path = write_config(tmp_path)
    settings_obj = settings(root, config_path, demo={"enabled": False})

    assert plugins.enable(settings_obj, "demo").state == "enabled"
    stored = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert stored["plugins"]["demo"]["enabled"] is True

    enabled = settings(root, config_path, demo={"enabled": True})
    assert plugins.load(enabled).state("demo") == "enabled"
    with pytest.raises(plugins.PluginError, match="disable it before removing"):
        plugins.remove(enabled, "demo")

    assert plugins.disable(enabled, "demo").state == "installed"
    assert folder.is_dir()
    stored = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert stored["plugins"]["demo"]["enabled"] is False

    assert plugins.remove(settings_obj, "demo").state == "absent"
    assert not folder.exists()
    stored = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "demo" not in (stored.get("plugins") or {})


def test_google_is_a_plugin_and_not_a_core_tool() -> None:
    assert "google" not in CORE_TOOLS.names()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("eidesoul.engine.tools.google")

    manifest = plugins.read_manifest(ROOT / "plugins" / "google" / "plugin.yaml")
    assert manifest.provides.tools == ("google",)
    assert [row.source for row in manifest.credits] == [
        "https://github.com/googleapis/google-api-python-client",
        "https://github.com/googleapis/google-auth-library-python",
    ]
    assert {row.license for row in manifest.credits} == {"Apache-2.0"}
    assert plugins.tree_digest(ROOT / "plugins" / "google") == manifest.digest


def test_manifest_requires_credits(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    folder = write_plugin(root)
    body = yaml.safe_load((folder / "plugin.yaml").read_text(encoding="utf-8"))
    body.pop("credits")
    (folder / "plugin.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")

    with pytest.raises(plugins.PluginError, match="credits"):
        plugins.read_manifest(folder / "plugin.yaml")
