"""Extension lifecycle: plugins, and the heavy tools that stay off until enabled.

A plugin is code plus a manifest; the engine owns verification, registration, and the config
document, so this module only sequences those calls and asks for a reload. A heavy tool is a
switch in `config.yaml`: `sandbox.runtime: off` disables the sandbox, and the compose profile
follows the same decision so the proxy container never runs for a disabled tool.
"""


import os
import shutil
import sys
from pathlib import Path

from ..engine import plugins
from ..engine.config import Config, ConfigError, HostConfig, load_host
from . import service
from .gateway import note, update_document

SANDBOX_OFF = "off"
SANDBOX_PROFILE = "sandbox"
PROXY_SERVICE = "sandbox-proxy"
DEFAULT_SEARCH = "ddgs"
ALWAYS_ON = (
    ("read", "read files under the workspace roots"),
    ("write", "write files under the workspace roots"),
    ("grep", "search file contents"),
    ("fetch", "fetch a public page"),
    ("shell", "bounded shell command"),
    ("mcp", "tools from the configured MCP servers"),
)


def sandbox_enabled(host: HostConfig | Config) -> bool:
    """The sandbox is enabled unless the operator parked its runtime at `off`."""
    return str(host.sandbox.runtime or "").strip().lower() != SANDBOX_OFF


def compose_profiles(enabled: bool) -> tuple[str, ...]:
    """The compose profile that starts the proxy; empty when the tool is off."""
    return (SANDBOX_PROFILE,) if enabled else ()


def _detected_runtime() -> str:
    from ..engine.config import SandboxConfig
    from ..engine.tools.sandbox import resolve_runtime

    return resolve_runtime(SandboxConfig()) or ""


def list_tools(config_path: str | None = None) -> int:
    host, _paths = load_host(config_path)
    sandbox = sandbox_enabled(host)
    print(
        f"sandbox: {'enabled' if sandbox else 'disabled'}"
        f" (runtime {host.sandbox.runtime or 'autodetect'})",
        flush=True,
    )
    print(f"web_search: {host.web_search.provider}", flush=True)
    for name, detail in ALWAYS_ON:
        print(f"{name}: enabled (always available; {detail})", flush=True)
    return 0


def _write_switch(config_path: str | None, updates: dict, comments: dict) -> None:
    _host, paths = load_host(config_path)
    update_document(paths.config, updates, comments=comments)


def sync_proxy(config_path: str | None, enabled: bool) -> None:
    """Start or stop the proxy container when this deployment is a compose stack."""
    try:
        current = service.load(config_path)
    except ConfigError:
        return
    argv = service.compose_argv(current)
    if argv is None:
        return
    # The profile must be named on both directions: a service behind an inactive profile is
    # not addressable, and the disable path has already rewritten the config.
    profile = ["--profile", SANDBOX_PROFILE]
    action = (
        [*profile, "up", "-d", PROXY_SERVICE]
        if enabled
        else [*profile, "stop", PROXY_SERVICE]
    )
    code = service._run([*argv, *action], current.root)
    if code:
        print(f"compose: the {PROXY_SERVICE} command exited {code}", file=sys.stderr)


def _set_sandbox(config_path: str | None, enabled: bool) -> int:
    runtime = "" if enabled else SANDBOX_OFF
    _write_switch(
        config_path,
        {"sandbox": {"runtime": runtime}},
        {"sandbox.runtime": note("sandbox.runtime", runtime)},
    )
    if enabled:
        found = _detected_runtime()
        if found:
            print(f"sandbox: enabled (runtime {found})", flush=True)
        else:
            print(
                "sandbox: enabled, but no podman/docker binary was found on this host",
                flush=True,
            )
    else:
        print("sandbox: disabled", flush=True)
    sync_proxy(config_path, enabled)
    service.notify(config_path, reason=f"sandbox {'enabled' if enabled else 'disabled'}")
    return 0


def _set_websearch(config_path: str | None, enabled: bool) -> int:
    provider = DEFAULT_SEARCH if enabled else "off"
    research = "auto" if enabled else "off"
    _write_switch(
        config_path,
        {"web_search": {"provider": provider}, "engine": {"research": {"mode": research}}},
        {
            "web_search.provider": note("web_search.provider", provider),
            "engine.research.mode": note("engine.research.mode", research),
        },
    )
    print(f"web_search: {provider}", flush=True)
    if enabled:
        choices = "|".join(("ddgs", "searxng", "tavily", "brave", "exa", "serper", "google"))
        print(f"  switch provider: monoagens websearch <{choices}>", flush=True)
    service.notify(config_path, reason=f"web_search {'enabled' if enabled else 'disabled'}")
    return 0


def set_tool(config_path: str | None, name: str, enabled: bool) -> int:
    """Toggle one heavy tool; an unknown or always-on tool is a refusal, never a silent no-op."""
    tool = str(name or "").strip()
    if tool == "sandbox":
        return _set_sandbox(config_path, enabled)
    if tool == "web_search":
        return _set_websearch(config_path, enabled)
    known = {item for item, _ in ALWAYS_ON}
    if tool in known:
        raise ValueError(
            f"tool '{tool}' is always available and has no switch; "
            "only heavy tools (sandbox, web_search) can be toggled"
        )
    raise ValueError(f"unknown tool '{tool}'; see `monoagens tools list`")


# ------------------------------------------------------------------ plugins


def _loader(config_path: str | None):
    host, paths = load_host(config_path)
    return plugins.PluginLoader.from_host(host, paths)


def _actor() -> str:
    return os.environ.get("USER", "") or os.environ.get("USERNAME", "")


def list_plugins(config_path: str | None = None) -> int:
    found = plugins.states(_loader(config_path))
    if not found:
        print("plugins: none configured", flush=True)
    for item in found:
        detail = f" - {item.reason}" if item.reason else ""
        print(f"plugin {item.name}: {item.state}{detail}", flush=True)
    return 0


def install_plugin(config_path: str | None, name: str) -> int:
    settings = _loader(config_path)
    try:
        item = plugins.install(settings, name, actor=_actor())
    except (plugins.PluginError, OSError) as exc:
        print(f"monoagens: {exc}", file=sys.stderr, flush=True)
        return 2
    detail = f" - {item.reason}" if item.reason else ""
    print(f"plugin {item.name}: {item.state}{detail}", flush=True)
    return 0


def set_plugin(config_path: str | None, name: str, enabled: bool) -> int:
    settings = _loader(config_path)
    action = plugins.enable if enabled else plugins.disable
    try:
        item = action(settings, name, actor=_actor())
    except (plugins.PluginError, OSError) as exc:
        print(f"monoagens: {exc}", file=sys.stderr, flush=True)
        return 2
    detail = f" - {item.reason}" if item.reason else ""
    print(f"plugin {item.name}: {item.state}{detail}", flush=True)
    service.notify(config_path, reason=f"plugin {item.name} {'enabled' if enabled else 'disabled'}")
    return 0


def delete_plugin(config_path: str | None, name: str) -> int:
    settings = _loader(config_path)
    try:
        current = plugins.states(settings)
        state = next((item.state for item in current if item.name == name), "absent")
        if state == "enabled":
            plugins.disable(settings, name, actor=_actor())
        item = plugins.remove(settings, name, actor=_actor())
    except (plugins.PluginError, OSError) as exc:
        print(f"monoagens: {exc}", file=sys.stderr, flush=True)
        return 2
    print(f"plugin {item.name}: {item.state}", flush=True)
    service.notify(config_path, reason=f"plugin {item.name} deleted")
    return 0


def update_plugins(config_path: str | None, name: str | None = None) -> int:
    settings = _loader(config_path)
    known = plugins.names(settings)
    if name and str(name) not in known:
        print(f"monoagens: plugin '{name}' is not configured", file=sys.stderr, flush=True)
        return 2
    wanted = [str(name)] if name else known
    if not wanted:
        print("plugins: none configured", flush=True)
        return 0
    states = {item.name: item.state for item in plugins.states(settings)}
    code = 0
    for item in wanted:
        try:
            if states.get(item) == "enabled":
                plugin = plugins.enable(settings, item, actor=_actor())
            else:
                plugin = plugins.install(settings, item, actor=_actor())
        except (plugins.PluginError, OSError) as exc:
            print(f"plugin {item}: {exc}", flush=True)
            code = 2
            continue
        detail = f" - {plugin.reason}" if plugin.reason else ""
        print(f"plugin {item}: {plugin.state}{detail}", flush=True)
    if code == 0:
        service.notify(config_path, reason="plugins updated")
    return code


def digest(directory: str | Path) -> int:
    root = Path(directory).expanduser()
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    print(f"tree: {plugins.tree_digest(root)}", flush=True)
    print(f"code: {plugins.code_digest(root)}", flush=True)
    return 0


def upgrade_extras(config_path: str | None, name: str | None = None) -> int:
    """Reinstall the declared extra of enabled gateways (`gateway update`)."""
    from . import gateway as admin_gateway

    _host, paths = load_host(config_path)
    wanted = [name] if name else admin_gateway.enabled_names(config_path)
    if not wanted:
        print("gateway: none enabled", flush=True)
        return 0
    pyproject = paths.config.parent / "pyproject.toml"
    code = 0
    for gateway in wanted:
        info = admin_gateway.gateway_info(gateway)
        if info is None:
            print(f"monoagens: unknown gateway '{gateway}'", file=sys.stderr, flush=True)
            return 2
        extra = getattr(getattr(info, "descriptor", None), "extra", "") or gateway
        if shutil.which("uv") is None or not pyproject.is_file():
            print(f"{gateway}: install its dependency with uv sync --extra {extra}", flush=True)
            continue
        result = admin_gateway.install_extra(extra, paths.config.parent)
        if result:
            code = result
            continue
        print(f"{gateway}: extra '{extra}' is installed", flush=True)
    if code == 0:
        service.notify(config_path, reason="gateway extras updated")
    return code
