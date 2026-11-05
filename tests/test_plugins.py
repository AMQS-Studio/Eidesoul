"""ADR-0028 §7: plugin manifests, digest verification, one import, and the lifecycle."""


import asyncio
import importlib
import inspect
import sys
from pathlib import Path

import pytest
import yaml
from support import build_config

from monoagens.engine import plugins
from monoagens.engine.access.roles import Role
from monoagens.engine.config import PluginSettings
from monoagens.engine.registry import Handler, Registry
from monoagens.engine.tools import REGISTRY as CORE_TOOLS
from monoagens.engine.tools import ToolContext, execute

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

from monoagens.engine.registry import Handler


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
    prompts: tuple[str, ...] = (),
) -> Path:
    """Write a complete local plugin tree and stamp its manifest with the real digest."""
    folder = root / name
    (folder / "tools").mkdir(parents=True)
    (folder / "tools" / "__init__.py").write_text("", encoding="utf-8")
    for tool in tools:
        (folder / "tools" / f"{tool}.py").write_text(TOOL.format(name=tool), encoding="utf-8")
    imports = "\n".join(f"from .tools.{tool} import TOOL as {tool.upper()}" for tool in tools)
    returned = ", ".join(tool.upper() for tool in tools)
    prompt_rows = ", ".join(
        f'PromptPart("plugin:{name}:{slug}", True, 0, text="Plugin instruction {index}.")'
        for index, slug in enumerate(prompts)
    )
    action = 'raise RuntimeError("boom")' if raises else (
        f'return {{"tools": [{returned}], "commands": [], "gateways": [], '
        f'"prompts": [{prompt_rows}]}}'
    )
    entry = (
        '"""Fixture plugin entry."""\n'
        "from monoagens.engine.registry import Handler\n"
        "from monoagens.engine.turn.prompts import PromptPart\n\n"
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
        "provides": {
            "tools": list(tools),
            "commands": [],
            "gateways": [],
            "prompt": list(prompts),
        },
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

    with pytest.raises(plugins.PluginError, match=r"plugin\.fetch is false"):
        plugins.enable(settings(root, config_path, demo={"enabled": False}), "demo")

    assert config_path.read_text(encoding="utf-8") == before
    assert not (root / "demo").exists()


def test_a_plugin_prompt_is_declared_static_and_namespaced(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    write_plugin(root, prompts=("guide",))

    loaded = plugins.load(settings(root, demo={"enabled": True}))

    assert loaded.state("demo") == "enabled"
    assert len(loaded.prompts) == 1
    part = loaded.prompts[0]
    assert part.name == "plugin:demo:guide"
    assert part.text == "Plugin instruction 0."
    assert part.stable is True
    assert part.order >= 1000


def test_a_plugin_prompt_must_be_declared(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    write_plugin(root, prompts=("guide",))
    manifest = yaml.safe_load((root / "demo" / "plugin.yaml").read_text(encoding="utf-8"))
    manifest["provides"]["prompt"] = []
    (root / "demo" / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    manifest["digest"] = plugins.tree_digest(root / "demo")
    (root / "demo" / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    loaded = plugins.load(settings(root, demo={"enabled": True}))

    assert loaded.state("demo") == "unavailable"
    assert "declared" in next(
        item.reason for item in loaded.plugins if item.name == "demo"
    ) or "missing" in next(item.reason for item in loaded.plugins if item.name == "demo")


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
    monkeypatch.delenv("MONOAGENS_FIXTURE_TOKEN", raising=False)
    root = tmp_path / "plugins"
    write_plugin(root, secrets=("MONOAGENS_FIXTURE_TOKEN",))

    loaded = plugins.load(settings(root, demo={"enabled": True}))

    assert loaded.state("demo") == "unavailable"
    reason = next(item.reason for item in loaded.plugins if item.name == "demo")
    assert "MONOAGENS_FIXTURE_TOKEN" in reason


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


def test_a_plugin_tool_declares_its_own_family_and_target() -> None:
    from monoagens.engine.registry import Handler, validate

    handler = Handler(
        name="robot_arm",
        description="Move one joint on the robot's arm.",
        family="robot",
        target="process",
        approval_class="authorization",
        scan=("command",),
        fn=_async_noop,
        parameters={"type": "object", "properties": {}},
    )
    assert validate(handler, "robot_arm", "tool") == []


async def _async_noop(args: dict, ctx) -> str:
    return ""


def test_google_is_a_plugin_and_not_a_core_tool() -> None:
    assert "google" not in CORE_TOOLS.names()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("monoagens.engine.tools.google")

    manifest = plugins.read_manifest(ROOT / "plugins" / "google" / "plugin.yaml")
    assert manifest.provides.tools == ("google",)
    assert [row.source for row in manifest.credits] == [
        "https://github.com/googleapis/google-api-python-client",
        "https://github.com/googleapis/google-auth-library-python",
    ]
    assert {row.license for row in manifest.credits} == {"Apache-2.0"}
    assert plugins.code_digest(ROOT / "plugins" / "google") == manifest.digest


def test_manifest_requires_credits(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    folder = write_plugin(root)
    body = yaml.safe_load((folder / "plugin.yaml").read_text(encoding="utf-8"))
    body.pop("credits")
    (folder / "plugin.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")

    with pytest.raises(plugins.PluginError, match="credits"):
        plugins.read_manifest(folder / "plugin.yaml")


def test_a_plugin_prompt_reaches_the_assembled_prompt(tmp_path: Path) -> None:
    from monoagens.engine.turn.prompts import CORE_PARTS, PromptContext, system_prompt

    root = tmp_path / "plugins"
    write_plugin(root, prompts=("guide",))
    loaded = plugins.load(settings(root, demo={"enabled": True}))
    config = build_config(tmp_path)
    roles = (Role("member"),)
    context = PromptContext(config=config, roles=roles)

    prompt = system_prompt(
        config, roles, context=context, parts=(*CORE_PARTS, *loaded.prompts)
    )

    assert "Plugin instruction 0." in prompt


def test_tree_digest_covers_installed_dependencies(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    folder = write_plugin(root)
    before = plugins.tree_digest(folder)
    code_before = plugins.code_digest(folder)
    deps = folder / "deps"
    deps.mkdir()
    (deps / "dependency.py").write_text("VALUE = 1\n", encoding="utf-8")

    assert plugins.tree_digest(folder) != before
    assert plugins.code_digest(folder) == code_before
    assert plugins.load(settings(root, demo={"enabled": True})).state("demo") == "enabled"


def test_remove_refuses_parent_paths_without_deleting(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "keep.txt"
    victim.write_text("keep", encoding="utf-8")
    settings_obj = settings(root, write_config(tmp_path))

    for name in ("../outside", "a/b"):
        with pytest.raises(plugins.PluginError, match="invalid plugin name"):
            plugins.remove(settings_obj, name)
    assert victim.is_file()


def test_remove_refuses_an_escaping_symlink(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "keep.txt"
    victim.write_text("keep", encoding="utf-8")
    (root / "evil").symlink_to(outside, target_is_directory=True)
    settings_obj = settings(root, write_config(tmp_path))

    with pytest.raises(plugins.PluginError, match="outside the plugin root"):
        plugins.remove(settings_obj, "evil")
    assert victim.is_file()
    assert (root / "evil").is_symlink()



def test_a_plugin_tool_derives_its_schema_and_runs() -> None:
    @plugins.tool
    async def search(query: str, limit: int = 5) -> str:
        """Search the archive.

        Args:
            query: Words to look for.
            limit: Maximum hits.
        """
        return f"{query}:{limit}"

    assert isinstance(search, Handler)
    assert search.name == "search"
    assert search.description == "Search the archive."
    assert search.fn is not None
    assert inspect.iscoroutinefunction(search.fn)
    assert search.parameters == {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Words to look for."},
            "limit": {"type": "integer", "description": "Maximum hits."},
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    assert asyncio.run(search.fn({"query": "memo"}, None)) == "memo:5"
    assert asyncio.run(search.fn({"query": "memo", "limit": 2}, None)) == "memo:2"


def test_a_plugin_tool_with_a_malformed_annotation_is_a_clear_error() -> None:
    with pytest.raises(plugins.PluginError, match="annotation"):
        @plugins.tool
        async def broken(value: object) -> str:
            """Broken tool."""

    with pytest.raises(plugins.PluginError, match="type annotation"):
        @plugins.tool
        async def untyped(value) -> str:
            """Untyped tool."""


DECORATED_ENTRY = '''"""Plugin entry that builds its tool with the decorator."""

from monoagens.engine.plugins import tool


def register():
    @tool(family="read")
    async def echo(text: str = "hi") -> str:
        """Echo the text back.

        Args:
            text: Words to return.
        """
        return text

    return {"tools": [echo], "commands": []}
'''


def write_decorated_plugin(root: Path, name: str = "decorated") -> Path:
    folder = root / name
    (folder / "tools").mkdir(parents=True)
    (folder / "tools" / "__init__.py").write_text("", encoding="utf-8")
    (folder / "tools" / "echo.py").write_text(
        '"""The entry owns the handler; the module keeps the declared layout."""\n',
        encoding="utf-8",
    )
    (folder / "plugin.py").write_text(DECORATED_ENTRY, encoding="utf-8")
    actual = plugins.tree_digest(folder)
    manifest = {
        "name": name,
        "version": "1.0.0",
        "engine": ">=0.0.0,<1.0",
        "entry": "plugin.py:register",
        "digest": actual,
        "provides": {"tools": ["echo"], "commands": [], "gateways": [], "prompt": []},
        "credits": CREDITS,
    }
    (folder / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return folder


def test_a_decorated_tool_registers_through_the_loader(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    write_decorated_plugin(root)

    loaded = plugins.load(settings(root, decorated={"enabled": True}))

    assert loaded.state("decorated") == "enabled"
    handler = loaded.tools[0]
    assert handler.name == "echo"
    assert handler.family == "read"
    assert handler.parameters["properties"]["text"] == {
        "type": "string",
        "description": "Words to return.",
    }
    assert asyncio.run(handler.fn({"text": "hello"}, None)) == "hello"


def test_a_local_plugin_loads_without_a_digest_or_pin(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    folder = write_plugin(root, digest=BAD_DIGEST)
    body = yaml.safe_load((folder / "plugin.yaml").read_text(encoding="utf-8"))
    body["local"] = True
    body.pop("digest")
    (folder / "plugin.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")

    manifest = plugins.read_manifest(folder / "plugin.yaml")

    assert manifest.local is True
    assert manifest.digest == ""
    loaded = plugins.load(settings(root, demo={"enabled": True, "digest": BAD_DIGEST}))
    assert loaded.state("demo") == "enabled"
    assert [handler.name for handler in loaded.tools] == ["echo"]


def test_a_published_plugin_still_requires_a_digest(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    folder = write_plugin(root)
    body = yaml.safe_load((folder / "plugin.yaml").read_text(encoding="utf-8"))
    body.pop("digest")
    (folder / "plugin.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")

    with pytest.raises(plugins.PluginError, match="digest"):
        plugins.read_manifest(folder / "plugin.yaml")
