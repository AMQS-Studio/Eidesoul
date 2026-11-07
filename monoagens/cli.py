"""The operator entry point: every `monoagens <command>` parser and dispatch.

`monoagens run` composes the engine; every other command is an administration surface over the
same host files. A mutation of `config.yaml`, `roles.yaml`, or `soul.yaml` goes through
`admin.yaml_io` and ends with a reload request, so a running primary sees the change explicitly.
"""


import argparse
import asyncio
import contextlib
import difflib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from .admin import backup as admin_backup
from .admin import doctor as admin_doctor
from .admin import extensions as admin_extensions
from .admin import gateway as admin_gateway
from .admin import service as admin_service
from .admin import setting as admin_setting
from .admin import templates as admin_templates
from .engine.config import (
    Config,
    ConfigError,
    load_config,
    load_env,
    load_host,
    resolve_database_url,
)

log = logging.getLogger(__name__)


def setup_logging() -> logging.Handler:
    """Boot default: text format; `build_runtime` applies `engine.logging` once loaded."""
    from .engine.turn import observability

    return observability.install("text", "INFO")


def _verdict(passed: object) -> str:
    return "PASS" if passed else "FAIL"


def _report(label: str, outcome: str, detail: str = "") -> bool:
    print(f"{outcome} {label}{(' - ' + detail) if detail else ''}", flush=True)
    return outcome != "FAIL"


def data_dir(config: Config) -> Path:
    """Host-local data directory (session, workspace, lock, outbox)."""
    return config.workspace.parent


def _role(config: Config):
    for role in config.roles.by_name.values():
        if "*" in role.tools:
            return role
    return config.roles.default_role()


async def build_runtime(
    config_path: str | None, *, console: bool = False, quiet: bool = False
):
    """Required bootstrap: host -> DB -> documents -> config -> stores -> gateways -> agent."""
    from .engine.access.roles import RoleError
    from .engine.agent import Agent
    from .engine.config import load_host, resolve_database_url
    from .engine.db import Database
    from .engine.jobs.ambient import Ambient
    from .engine.jobs.lease import Lease, lock_data_dir
    from .engine.jobs.mcp import Hub
    from .engine.jobs.supervisor import Supervisor
    from .engine.llm import LLM
    from .engine.memory import build_memory
    from .engine.memory.ledger import ActionLedger
    from .engine.stores.affect import AffectStore
    from .engine.stores.documents import DocumentStore
    from .engine.stores.history import HistoryStore
    from .engine.stores.recipes import RecipeStore
    from .engine.stores.skills import SkillStore
    from .engine.stores.style import StyleStore
    from .engine.stores.vibe import VibeRunner
    from .engine.turn.context import WorkingMemory

    host, paths = load_host(config_path)
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError("database URL is empty; set it in config.yaml or MONOAGENS_DATABASE_URL")
    db = Database(
        url,
        pool_size=host.database.pool_size,
        max_overflow=host.database.max_overflow,
        pool_timeout=host.database.pool_timeout,
    )
    db.ensure()
    admin_gateway.ensure_schemas(db)
    if console:
        # The console is the local operator: link it once before the administrator boot gate.
        from .admin.people import ensure_console_administrator

        ensure_console_administrator(db, paths)
    documents = DocumentStore(db, paths, sync=host.sync_documents)
    documents.seed()
    documents.sync()
    # The console is a core REPL, not a gateway: a host with no gateway still boots it.
    config = load_config(paths.config, require_gateway=not console)
    from .engine.turn import observability

    observability.configure(config.logging)
    override = os.environ.get("MONOAGENS_LOG_LEVEL", "").strip().upper()
    if override:
        observability.install(config.logging.format, override)
    if quiet:
        for handler in logging.getLogger().handlers:
            handler.setLevel(logging.CRITICAL)
        logging.getLogger().setLevel(logging.CRITICAL)
        observability.silence_third_party(debug=False)
    try:
        config.roles.require_administrator()
    except RoleError as exc:
        raise ConfigError(str(exc)) from exc
    history = HistoryStore(db, config.workspace)
    windows = WorkingMemory(store=history)
    ledger = ActionLedger(db)
    skills = SkillStore(db, config.skills)
    recipes = RecipeStore(db)
    memory = await build_memory(config, db=db)
    voice = config.soul.voice
    affect_enabled = bool(voice.affect.enabled)
    style_enabled = bool(voice.style.enabled)
    affect = (
        AffectStore(db, voice.affect, soul=config.soul.persona) if affect_enabled else None
    )
    style = (
        StyleStore(db, voice.style, language=config.soul.language)
        if style_enabled
        else None
    )
    vibe = None
    if affect is not None and voice.affect.vibe.enabled:
        vibe = VibeRunner(db, voice.affect.vibe)
    hub = Hub(config.mcp)
    await hub.start()
    llm = LLM(config, db=db)
    console_body = None
    if console:
        from .admin.console import Console

        console_body = Console()
        gateways = [console_body]
        admin_gateway.ensure_core(console_body)
    else:
        gateways = admin_gateway.runtime_gateways(
            config, db=db, history=history, windows=windows
        )
    ambient = None
    if config.ambient.enabled:
        ambient = Ambient(config, llm, affect, style, memory, history, windows)
    agent = Agent(
        config,
        db,
        llm=llm,
        hub=hub,
        windows=windows,
        history=history,
        memory=memory,
        ledger=ledger,
        skills=skills,
        recipes=recipes,
        affect=affect,
        style=style,
        ambient=ambient,
        vibe=vibe,
        gateways=tuple(gateways),
        documents=documents,
    )
    # ADR-0001 §4.5: every registered gateway passed `ensure_core` in `build_gateways`.
    agent.runtime.gateways = tuple(gateway.name for gateway in gateways)
    agent.runtime.gateway_core_ok = len(gateways)
    lease = None
    supervisor = None
    lock = None
    if console_body is not None:
        # The console runs the Agent directly; there is no supervisor, gateway, or lease loop.
        agent.mode = "primary"
        agent.runtime.mode = "primary"
        agent.runtime.phase = "running"
    else:
        lease = Lease(db)
        try:
            lock = lock_data_dir(data_dir(config) / ".monoagens.lock")
        except RuntimeError as exc:
            raise ConfigError(str(exc)) from exc
        supervisor = Supervisor(agent, lease, gateways, takeover=False)
    return {
        "host": host,
        "paths": paths,
        "config": config,
        "db": db,
        "agent": agent,
        "lease": lease,
        "supervisor": supervisor,
        "gateways": gateways,
        "console": console_body,
        "lock": lock,
    }


async def _apply_reload(agent) -> None:
    """Apply one explicit reload request to the live runtime, never two at once."""
    try:
        summary = await agent.reload()
        print(f"reload: {summary}", flush=True)
    except Exception:  # a rejected file set must not stop the running primary
        log.exception("reload failed; the previous runtime stays in service")


def _install_reload_signal(loop: asyncio.AbstractEventLoop, holder: dict[str, Any]) -> None:
    """SIGUSR1 is the reload IPC; a signal during boot is applied once the runtime exists."""
    number = getattr(signal, "SIGUSR1", None)
    if number is None:
        return

    def on_signal() -> None:
        agent = holder.get("agent")
        if agent is None:
            holder["requested"] = True
            return
        loop.create_task(_apply_reload(agent))

    with contextlib.suppress(NotImplementedError, RuntimeError):
        loop.add_signal_handler(number, on_signal)


def _write_ipc(path: Path, protocol: int = 1) -> None:
    """Announce that this process handles SIGUSR1 before any long boot step runs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"pid": os.getpid(), "protocol": protocol, "at": time.time()})
    path.write_text(body, encoding="utf-8")
    os.chmod(path, 0o600)


async def serve(
    config_path: str | None,
    *,
    console: bool = False,
    takeover: bool = False,
    quiet: bool = False,
) -> int:
    holder: dict[str, Any] = {"agent": None, "requested": False}
    _install_reload_signal(asyncio.get_running_loop(), holder)
    ipc_path: Path | None = None
    pid_file: Path | None = None
    try:
        _host, paths = load_host(config_path)
        if not console:
            ipc_path = paths.workspace.parent / admin_service.IPC_NAME
            _write_ipc(ipc_path)
            pid_file = paths.workspace.parent / "run" / "monoagens.pid"
            admin_service.write_pid(pid_file, os.getpid())
        runtime = await build_runtime(config_path, console=console, quiet=quiet)
        agent = runtime["agent"]
        holder["agent"] = agent
        if holder.get("requested"):
            print("reload: applying a request received during boot", flush=True)
            await _apply_reload(agent)
        console_body = runtime["console"]
        supervisor = runtime["supervisor"]
        lock = runtime["lock"]
        try:
            if console_body is not None:
                await console_body.run(agent)
                return 0
            supervisor.takeover = takeover
            await supervisor.run()
            return 0
        finally:
            await agent.close()
            if lock is not None:
                with contextlib.suppress(Exception):
                    lock.close()
    finally:
        if ipc_path is not None:
            with contextlib.suppress(OSError):
                ipc_path.unlink()
        if pid_file is not None:
            with contextlib.suppress(OSError, ValueError):
                if pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                    pid_file.unlink()


async def backfill(config_path: str | None) -> int:
    """Transport-owned history backfill; the core names no transport."""
    return int(await admin_gateway.backfill(config_path))


# ------------------------------------------------------------------ service


def _service(args: argparse.Namespace) -> int:
    current = admin_service.load(args.config)
    command = args.command
    if command == "start":
        return admin_service.start(current)
    if command == "stop":
        return admin_service.stop(current)
    if command == "restart":
        return admin_service.restart(current)
    data = admin_service.status(current)
    for line in admin_service.format_status(data):
        print(line, flush=True)
    return 0


def _logs(args: argparse.Namespace) -> int:
    current = admin_service.load(args.config)
    minimum = admin_service.level_from_args(args)
    return admin_service.logs(current, follow=bool(args.follow), minimum=minimum)


def _reload(args: argparse.Namespace) -> int:
    return admin_service.reload(args.config, reason="manual")


def _run(args: argparse.Namespace) -> int:
    """Foreground engine; an early exit names the layer that refused to start."""
    level = admin_service.level_from_args(args)
    previous = os.environ.get("MONOAGENS_LOG_LEVEL")
    os.environ["MONOAGENS_LOG_LEVEL"] = level
    try:
        return asyncio.run(serve(args.config, takeover=bool(getattr(args, "takeover", False))))
    except ConfigError as exc:
        print(f"monoagens run: cannot start: {exc}", file=sys.stderr, flush=True)
        print(
            "  check: config.yaml, soul.yaml, roles.yaml, and the database URL in .env",
            file=sys.stderr,
            flush=True,
        )
        return 2
    except Exception as exc:  # the engine stopped before serving its first turn
        log.exception("the engine stopped early")
        print(
            f"monoagens run: engine exited: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        print(f"  logs: monoagens logs --debug; config: {args.config or 'config.yaml'}",
              file=sys.stderr, flush=True)
        return 1
    finally:
        if previous is None:
            os.environ.pop("MONOAGENS_LOG_LEVEL", None)
        else:
            os.environ["MONOAGENS_LOG_LEVEL"] = previous


# ------------------------------------------------------------------ settings


def _setting(args: argparse.Namespace) -> int:
    action = getattr(args, "setting_command", None)
    if action in (None, "menu"):
        return admin_setting.menu(args.config)
    if action == "list":
        return admin_setting.show(args.config)
    if action == "get":
        return admin_setting.get(args.config, args.key)
    if action == "set":
        admin_setting.set_value(args.config, args.key, args.value)
        return 0
    if action in ("reset", "unset", "default"):
        return _setting_reset(args.config, args.key)
    if action == "export":
        return _setting_export(args.config, args.path)
    if action == "import":
        return _setting_import(args.config, args.file)
    if action == "model":
        if not args.value:
            return admin_setting.get(args.config, "engine.llm.model")
        return admin_setting.model(args.config, args.value)
    if action == "provider":
        if not args.value:
            return admin_setting.get(args.config, "engine.llm.provider")
        return admin_setting.provider(args.config, args.value)
    if action == "websearch":
        if not args.value:
            return admin_setting.get(args.config, "web_search.provider")
        return admin_setting.websearch(args.config, args.value)
    raise ValueError(f"unknown setting command '{action}'")


def _setting_reset(config_path: str | None, key: str) -> int:
    resolved = admin_setting.canonical(key)
    default = admin_setting._default(resolved)
    if default is admin_setting.MISSING:
        raise ValueError(f"setting '{resolved}' has no dataclass default")
    admin_setting._checked_write(config_path, {resolved: default})
    print(f"{resolved}: {default}", flush=True)
    return 0


def _setting_export(config_path: str | None, path: str | None) -> int:
    body = admin_setting.read(config_path)
    rendered = yaml.safe_dump(body, sort_keys=False)
    if path:
        Path(path).write_text(rendered, encoding="utf-8")
        print(f"exported {path}", flush=True)
    else:
        print(rendered, flush=True)
    return 0


def _setting_import(config_path: str | None, source: str | None) -> int:
    if not source:
        return _config_import(argparse.Namespace(config=config_path))
    body = yaml.safe_load(Path(source).read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError(f"import file must be a YAML mapping: {source}")
    updates: dict[str, object] = {}

    def walk(node: dict, prefix: str = "") -> None:
        for key, value in node.items():
            dotted = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict) and value:
                walk(value, dotted)
            else:
                updates[dotted] = value

    walk(body)
    admin_setting._checked_write(config_path, updates)
    print(f"imported {len(updates)} settings from {source}", flush=True)
    return 0


def _shortcut(args: argparse.Namespace) -> int:
    command = args.command
    value = getattr(args, "value", None)
    if command == "model":
        if value:
            return admin_setting.model(args.config, value)
        return admin_setting.get(args.config, "engine.llm.model")
    if command == "provider":
        if value:
            return admin_setting.provider(args.config, value)
        return admin_setting.get(args.config, "engine.llm.provider")
    if value:
        return admin_setting.websearch(args.config, value)
    return admin_setting.get(args.config, "web_search.provider")


# --------------------------------------------------------------- extensions


def _gateway(args: argparse.Namespace) -> int:
    command = args.gateway_command
    if command in ("list", "ls"):
        return admin_gateway.list_gateways(args.config)
    if command in ("add", "enable", "install"):
        if getattr(args, "connect", False):
            return admin_gateway.connect(args.config, args.name)
        return admin_gateway.add(args.config, args.name)
    if command == "disable":
        return admin_gateway.set_enabled(args.config, args.name, False)
    if command in ("delete", "remove", "rm", "uninstall"):
        return admin_gateway.delete(args.config, args.name)
    if command in ("pair", "connect", "login"):
        return admin_gateway.pair(args.config, args.name)
    if command in ("unpair", "disconnect", "logout"):
        return admin_gateway.unpair(args.config, args.name)
    if command in ("update", "upgrade"):
        return admin_extensions.upgrade_extras(args.config, args.name)
    if command == "doctor":
        return admin_gateway.doctor(args.config, fix=args.fix)
    raise ValueError(f"unknown gateway command '{command}'")


def _plugins(args: argparse.Namespace) -> int:
    command = args.plugins_command
    if command in ("list", "ls"):
        return admin_extensions.list_plugins(args.config)
    if command in ("install", "add"):
        return admin_extensions.install_plugin(args.config, args.name)
    if command == "enable":
        return admin_extensions.set_plugin(args.config, args.name, True)
    if command == "disable":
        return admin_extensions.set_plugin(args.config, args.name, False)
    if command in ("delete", "remove", "rm", "uninstall"):
        return admin_extensions.delete_plugin(args.config, args.name)
    if command in ("update", "upgrade"):
        return admin_extensions.update_plugins(args.config, getattr(args, "name", None))
    if command == "digest":
        return admin_extensions.digest(args.directory)
    raise ValueError(f"unknown plugin command '{command}'")


def _tools(args: argparse.Namespace) -> int:
    command = args.tools_command
    if command in ("list", "ls"):
        return admin_extensions.list_tools(args.config)
    if command in ("enable", "add"):
        return admin_extensions.set_tool(args.config, args.name, True)
    if command in ("disable", "remove", "rm"):
        return admin_extensions.set_tool(args.config, args.name, False)
    raise ValueError(f"unknown tools command '{command}'")


def _update_core(root: Path, source: str = "") -> int:
    """Refresh the code from the published archive, then sync the lockfile.

    The archive needs no `git` and never touches an operator file; a failed download keeps the
    local tree and reports it, because an update must not leave a half-upgraded deployment.
    """
    uv = admin_service.find_uv(shutil.which("uv"), root)
    if not (root / "pyproject.toml").is_file():
        print(
            "core: no checkout here; upgrade the installed package, then rerun "
            "`monoagens update`"
        )
        return 0
    url = source or admin_service.UPDATE_ARCHIVE
    try:
        written = admin_service.fetch_archive(root, url)
    except Exception as exc:
        print(f"core: archive fetch failed ({exc}); syncing the local tree", file=sys.stderr)
    else:
        print(f"core: refreshed {len(written)} files from {url}", flush=True)
    if uv is None:
        print("core: uv is not on PATH; install it, then rerun `monoagens update`", file=sys.stderr)
        return 2
    print(f"core: {uv} sync --locked --no-dev", flush=True)
    return int(
        subprocess.run([uv, "sync", "--locked", "--no-dev"], cwd=root, check=False).returncode
    )


def _update(args: argparse.Namespace) -> int:
    paths = load_host(args.config)[1]
    code = _update_core(paths.config.parent)
    for changed in admin_templates.refresh_host_files(paths):
        print(f"host files: refreshed {changed}", flush=True)
    if not args.core:
        code = admin_extensions.upgrade_extras(args.config) or code
        code = admin_extensions.update_plugins(args.config) or code
    current = admin_service.load(args.config)
    if current.mode == admin_service.COMPOSE:
        # The stack owns the database host name and the image: build, migrate inside the
        # network, then recreate the service so it runs the code just synced on the host.
        code = admin_service.compose_update(current) or code
        return code
    from .admin import database as admin_db

    try:
        admin_db.upgrade(args.config)
    except ConfigError as exc:
        print(f"db upgrade: {exc}", file=sys.stderr, flush=True)
        code = code or 2
    if admin_service.read_pid(current) is not None:
        code = admin_service.restart(current) or code
    else:
        admin_service.reload(args.config, reason="update")
    return code


def _uninstall(args: argparse.Namespace) -> int:
    current = admin_service.load(args.config)
    code = admin_service.stop(current)
    wrapper = Path.home() / ".local" / "bin" / "monoagens"
    if wrapper.exists():
        wrapper.unlink()
        print(f"uninstall: removed {wrapper}", flush=True)
    if not args.purge:
        print(
            f"uninstall: data kept at {current.state}; "
            "remove it with `monoagens uninstall --purge`",
            flush=True,
        )
        return code
    if not _confirmed(args, current):
        print("uninstall: --purge removes the data directory; pass --yes to confirm", flush=True)
        return 2
    _purge(current)
    return code


def _confirmed(args: argparse.Namespace, current: admin_service.Service) -> bool:
    if getattr(args, "yes", False):
        return True
    if not sys.stdin.isatty():
        return False
    answer = input(f"remove {current.state}? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def _purge(current: admin_service.Service) -> None:
    state = current.state.resolve()
    config = current.paths.config.resolve()
    home = Path.home().resolve()
    if state == config.parent or config.is_relative_to(state):
        raise ValueError(f"refusing to remove the deployment root: {state}")
    if state == home or state == Path("/") or state.parent == state:
        raise ValueError(f"refusing to remove {state}")
    allowed = state.is_relative_to(home) or state.is_relative_to(current.paths.config.parent)
    if not allowed:
        raise ValueError(f"refusing to remove a directory outside the deployment: {state}")
    if current.mode == admin_service.COMPOSE:
        argv = admin_service.compose_argv(current)
        if argv is not None:
            admin_service._run([*argv, "down", "-v"], current.root)
    if state.is_dir():
        shutil.rmtree(state)
        print(f"uninstall: removed {state}", flush=True)


# ------------------------------------------------------------------ backup/doctor


def _backup(args: argparse.Namespace) -> int:
    admin_backup.create(args.config, args.output or args.path)
    return 0


def _restore(args: argparse.Namespace) -> int:
    admin_backup.restore(
        args.file, args.config, yes=bool(args.yes), force=bool(args.force)
    )
    return 0


def _doctor(args: argparse.Namespace) -> int:
    return admin_doctor.run(args.config, fix=bool(args.fix))


# ------------------------------------------------------------------ existing


async def selftest(config: Config) -> int:
    """Exercise stores, tools, and the LLM against the configured Postgres store."""
    from .engine.access.permissions import commands_for, tool_patterns, tools_for
    from .engine.commands import REGISTRY as COMMANDS
    from .engine.db import Database
    from .engine.i18n import t
    from .engine.jobs.mcp import Hub
    from .engine.llm import LLM
    from .engine.memory import MemoryHit, build_memory, memory_status
    from .engine.memory.ledger import ActionLedger
    from .engine.stores.affect import AffectStore
    from .engine.stores.documents import DocumentStore
    from .engine.stores.history import HistoryStore
    from .engine.stores.recipes import RecipeStore
    from .engine.stores.skills import SkillStore
    from .engine.stores.style import StyleStore
    from .engine.tools import REGISTRY, ToolContext, execute
    from .engine.tools.sandbox import resolve_runtime
    from .engine.turn.context import Message
    from .engine.turn.decision import Appraisal, Decision, RelationshipDelta

    hub = Hub(config.mcp)
    await hub.start()
    url = config.database_url()
    if not url:
        raise ConfigError(
            "database URL is empty; `monoagens selftest` needs the configured database"
        )
    db = Database(url)
    db.ensure()
    admin_gateway.ensure_schemas(db)
    docs = DocumentStore(db, config.paths, sync=False)
    docs.seed()
    history = HistoryStore(db, config.workspace)
    ledger = ActionLedger(db)
    affect = AffectStore(db, config.soul.voice.affect, soul=config.soul.persona)
    style = StyleStore(db, config.soul.voice.style, language=config.soul.language)
    skills = SkillStore(db, config.skills)
    recipes = RecipeStore(db)
    memory = await build_memory(config, db=db)
    results: list[bool] = []
    from .engine import plugins
    from .engine.registry import Registry

    plugin_settings = plugins.PluginLoader.from_config(config)
    plugin_report = plugins.states(plugin_settings)
    if not plugin_report:
        print("plugins: none configured", flush=True)
    for item in plugin_report:
        detail = f" - {item.reason}" if item.reason else ""
        print(f"plugin {item.name}: {item.state}{detail}", flush=True)
    enabled = plugins.load(plugin_settings)
    plugin_tools = Registry(list(enabled.tools), "tool")
    try:
        role = _role(config)
        for name, item in config.roles.by_name.items():
            tools = [handler.name for handler in tools_for(REGISTRY, (item,))]
            tools += [handler.name for handler in tools_for(plugin_tools, (item,))]
            tools += [handler.name for handler in hub.specs((item,))]
            commands = [handler.name for handler in commands_for(COMMANDS, (item,))]
            command_label = ", ".join(commands) or "-"
            tool_label = ", ".join(tools) or "-"
            print(f"role {name}: command={command_label} | tool={tool_label}", flush=True)

        from .engine.self_knowledge import check as selfcheck

        problems = selfcheck(config)
        results.append(
            _report(
                "selfcheck",
                "PASS" if not problems else "FAIL",
                "" if not problems else "; ".join(problems[:3]),
            )
        )

        config.workspace.mkdir(parents=True, exist_ok=True)
        ctx = ToolContext(config, (role,), tool_patterns((role,)), memory=memory, skills=skills)
        specs = {
            handler.name: handler
            for handler in REGISTRY.by_name.values()
            if not handler.hidden
        }

        wrote = await execute(
            specs,
            "write",
            {"path": "private/selftest.txt", "content": "monoagens-selftest"},
            ctx,
        )
        read = await execute(specs, "read", {"path": "private/selftest.txt"}, ctx)
        passed = wrote.startswith("ok:") and "monoagens-selftest" in read
        detail = "" if passed else f"{wrote} | {read[:80]}"
        results.append(_report("write/read", _verdict(passed), detail))

        history.add(
            "selftest",
            Message(
                id="selftest-1",
                sender_name="Tester",
                sender_id="p1",
                when=datetime.now(UTC),
                text="monoagens-selftest-archive",
                author="manual_owner",
                visibility=3,
            ),
        )
        found = history.search("selftest", "monoagens-selftest-archive")
        history.mark_deleted("selftest", "selftest-1", "p2", "Admin")
        removed = history.find("selftest", "selftest-1")
        passed = (
            [item.id for item in found] == ["selftest-1"]
            and (found[0].author == "manual_owner" and found[0].visibility == 3)
            and removed is not None
            and removed.deleted is True
            and removed.deleted_admin is True
        )
        detail = "" if passed else "archive not readable"
        results.append(_report("history", _verdict(passed), detail))

        action_id = ledger.log_action("selftest", "selftest", "send", "monoagens-selftest-action")
        passed = action_id > 0 and any(
            row["summary"] == "monoagens-selftest-action" for row in ledger.recent_actions()
        )
        detail = "" if passed else "action not recorded"
        results.append(_report("ledger", _verdict(passed), detail))

        if getattr(memory, "available", False):
            ids = await memory.add(
                "monoagens-selftest-memory",
                scope="global",
                kind="note",
                source="selftest",
                level=0,
                owner_id="selftest",
                subject_id="selftest",
                confirmed=True,
            )
            hits = await memory.search("monoagens-selftest-memory", scopes=("global",))
            removed = bool(ids) and await memory.forget(
                ids[0], requester_id="selftest", allow_global=True
            )
            passed = bool(hits) and removed
            detail = "" if passed else "entry not found"
            results.append(_report("memory", _verdict(passed), detail))
        else:
            _report("memory", "SKIP", memory_status(memory))

        from .engine.config import PrivacyConfig
        from .engine.memory import evidence as privacy_layers
        from .engine.memory.privacy import PrivacyPolicy

        try:
            found, _ = privacy_layers.classify(
                "Nama saya Budi Santoso, tinggal di Jakarta.", config.soul.language
            )
            spans = [
                item for item in found if item.detector == "ner" and item.span is not None
            ]
            detail = "" if spans else "no entity span"
            results.append(_report("privacy_entities", _verdict(bool(spans)), detail))
        except Exception as exc:
            results.append(_report("privacy_entities", "FAIL", str(exc)[:200]))

        policy = PrivacyPolicy(PrivacyConfig())
        probe = MemoryHit(id="x", text="t", level=3, owner_id="a", subject_id="a", confirmed=True)
        vetoed = MemoryHit(id="y", text="t", level=1, vetoed=True)
        checks = (
            policy.show(probe, direct=False) is False,
            policy.show(probe, direct=True) is True,
            policy.disclose(probe, requester="a", direct=False) == "ask",
            policy.disclose(probe, requester="a", direct=True) == "allow",
            policy.disclose(probe, requester="b", direct=True) == "deny",
            policy.disclose(vetoed, requester="a", direct=True) == "deny",
        )
        results.append(
            _report("privacy", _verdict(all(checks)), "" if all(checks) else str(checks))
        )

        skills.save(
            "selftest",
            "Selftest check.",
            "Selftest steps.\n",
            overwrite=True,
            language=config.soul.language,
        )
        passed = "Selftest check." in skills.catalog()
        detail = "" if passed else skills.catalog()[:120]
        results.append(_report("skill", _verdict(passed), detail))

        recipes.save(
            "selftest",
            "recipe",
            "Selftest recipe.",
            steps=[{"tool": "read", "args": {"path": "selftest.txt"}}],
            overwrite=True,
        )
        passed = recipes.get("selftest") is not None
        detail = "" if passed else "recipe missing"
        results.append(_report("recipe", _verdict(passed), detail))

        shell = await execute(specs, "shell", {"command": "echo shell-ok"}, ctx)
        passed = "shell-ok" in shell
        detail = "" if passed else shell[:120]
        results.append(_report("shell", _verdict(passed), detail))

        sandbox_on = admin_extensions.sandbox_enabled(config)
        runtime = resolve_runtime(config.sandbox) if sandbox_on else None
        if not sandbox_on:
            results.append(
                _report(
                    "sandbox",
                    "SKIP",
                    "disabled; enable via 'monoagens tools enable sandbox'",
                )
            )
        elif runtime is None:
            results.append(
                _report(
                    "sandbox",
                    "SKIP",
                    f"{config.sandbox.runtime or 'podman/docker'} not found",
                )
            )
        elif not str(config.sandbox.proxy or "").strip():
            results.append(
                _report("sandbox", "SKIP", "sandbox.proxy is not configured")
            )
        else:
            box = await execute(
                specs,
                "sandbox",
                {"command": "echo sandbox-ok && python3 -V", "timeout": 300},
                ctx,
            )
            passed = "sandbox-ok" in box
            detail = "" if passed else box[:200]
            results.append(_report("sandbox", _verdict(passed), detail))

        args = {"query": "python asyncio", "max_results": 3}
        search = await execute(specs, "web_search", args, ctx)
        passed = bool(search.strip()) and not search.startswith("error:")
        detail = "" if passed else search[:200]
        results.append(_report("web_search", _verdict(passed), detail))

        llm = LLM(config, db=db)
        answer = await llm.run("Reply with: ok", "ping", [], ctx, session="monoagens-selftest")
        passed = bool(answer.strip()) and answer != t("agent.api_error", config.soul.language)
        results.append(_report("llm", "PASS" if passed else "FAIL", "" if passed else answer[:200]))

        selftest_msg_id = f"selftest-{uuid4().hex[:8]}"
        affect.turn_start("selftest", selftest_msg_id)
        affect.turn_end(
            "selftest",
            "p1",
            "Tester",
            "you are an idiot",
            "member",
            "p1",
            Decision(
                addressed="me",
                act="reply",
                mode="daily",
                tone="hurt",
                bubbles=1,
                appraisal=Appraisal("hostility", -0.4, 0.3, 0.1, "insulted"),
                relationship=RelationshipDelta(trust=-0.1, annoyance=0.15),
                reason="hinaan",
            ),
            selftest_msg_id,
        )
        hurt = affect.status("selftest", "p1")
        calmed = affect.calm("selftest", "p1")
        healed = affect.status("selftest", "p1")
        affect.pulse("selftest")
        passed = (
            hurt["pleasure"] < 0
            and calmed
            and healed["pleasure"] > hurt["pleasure"]
            and affect.drives("selftest")["boredom"] >= 0
        )
        detail = "" if passed else "PAD did not move"
        results.append(_report("affect", _verdict(passed), detail))

        style.learn("yeah i already checked, it is fine")
        text = style.profile()
        passed = "How people chat here" in text and "particles:" in text
        results.append(_report("style", "PASS" if passed else "FAIL", "" if passed else text[:120]))

        from .engine import guard as safety_guard

        if safety_guard.has_pack(config.soul.language):
            results.append(_report("safety", "PASS", ""))
        elif config.safety.allow_degraded:
            results.append(_report("safety", "DEGRADED", "the English net only"))
        else:
            results.append(
                _report("safety", "FAIL", f"no pack for '{config.soul.language}'")
            )

        for name in config.mcp:
            prefix = f"mcp__{name}__"
            found = [spec.name for spec in hub.specs((role,)) if spec.name.startswith(prefix)]
            print(f"mcp {name}: {', '.join(found) or '(no tools)'}", flush=True)
    finally:
        await memory.close()
        await hub.stop()
        db.dispose()

    if all(results):
        print("SELFTEST OK", flush=True)
        return 0
    print("SELFTEST FAILED", flush=True)
    return 1


def _database(args: argparse.Namespace):
    from .admin import database as admin_db

    return admin_db


def _db_upgrade(args: argparse.Namespace) -> int:
    data = _database(args).upgrade(args.config)
    print(f"db upgrade: {data['dialect']} {data['database']}", flush=True)
    version = data["schema_version"]
    documents = len(data["documents"])
    print(f"schema version: {version}; documents: {documents}", flush=True)
    return 0


def _db_check(args: argparse.Namespace) -> int:
    admin_db = _database(args)
    data = admin_db.check(args.config)
    if args.json:
        import json

        print(json.dumps(data, indent=2, ensure_ascii=False, default=str), flush=True)
    else:
        print(admin_db.format_check(data), flush=True)
    return 0


def _db_backup(args: argparse.Namespace) -> int:
    path = _database(args).backup(args.config, Path(args.out))
    print(f"backup: {path}", flush=True)
    return 0


def _db_restore(args: argparse.Namespace) -> int:
    _database(args).restore(args.config, Path(args.file), yes=args.yes, force=args.force)
    print(f"restore: {args.file}", flush=True)
    return 0


def _db_downgrade(args: argparse.Namespace) -> int:
    from alembic import command
    from alembic.config import Config as AlembicConfig

    from .engine.db import MIGRATIONS, Database

    host, _ = load_host(args.config)
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError("database URL is empty; set it in config.yaml or MONOAGENS_DATABASE_URL")
    db = Database(url)
    try:
        db.assert_migration_allowed()
        cfg = AlembicConfig()
        cfg.set_main_option("script_location", str(MIGRATIONS))
        cfg.set_main_option("sqlalchemy.url", url)
        command.downgrade(cfg, args.revision)
    finally:
        db.dispose()
    print(f"db downgrade: {args.revision}", flush=True)
    return 0


def _db_dispatch(args: argparse.Namespace) -> int:
    command = args.db_command
    handlers = {
        "upgrade": _db_upgrade,
        "downgrade": _db_downgrade,
        "check": _db_check,
        "status": _db_check,
        "backup": _db_backup,
        "restore": _db_restore,
    }
    if command in handlers:
        return handlers[command](args)
    raise ValueError(f"unknown db command '{command}'")


def _doc_store(args: argparse.Namespace):
    from .engine.config import load_host, resolve_database_url
    from .engine.db import Database
    from .engine.stores.documents import DocumentStore

    host, paths = load_host(args.config)
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError("database URL is empty; set it in config.yaml or MONOAGENS_DATABASE_URL")
    db = Database(
        url,
        pool_size=host.database.pool_size,
        max_overflow=host.database.max_overflow,
        pool_timeout=host.database.pool_timeout,
    )
    return DocumentStore(db, paths, sync=True), db


def _config_import(args: argparse.Namespace) -> int:
    docs, db = _doc_store(args)
    try:
        count = docs.import_from_files()
    finally:
        db.dispose()
    print(f"config import: {count} documents from files", flush=True)
    return 0


def _init(args: argparse.Namespace) -> int:
    from .admin import init as admin_init

    interactive = sys.stdin.isatty() and not args.non_interactive
    if not interactive and not args.provider:
        print(
            "monoagens: no terminal for the interactive setup and no --provider given.\n"
            "  pass: --provider <name>  (required)\n"
            "        --model <id> --database-url <url> --label <text> --voice-layer on|off\n"
            "        --api-key-file <path>   (optional; never pass the key itself)\n"
            "        --language <code> --locale <tag> --timezone <IANA>\n"
            "        --gateway <name> --websearch <backend> --websearch-url <url>\n"
            "        --tools <a,b> --voice-parts <a,b>\n"
            "  or run `monoagens init` on a terminal.",
            file=sys.stderr,
            flush=True,
        )
        return 2
    api_key: str | None = None
    if args.api_key_file:
        try:
            api_key = Path(args.api_key_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            print(f"monoagens: cannot read --api-key-file: {exc}", file=sys.stderr, flush=True)
            return 2
    color = bool(sys.stdout.isatty() and not args.no_color and not os.environ.get("NO_COLOR"))
    gateways = tuple(args.gateway) if args.gateway else None
    tools = (
        tuple(item for item in (args.tools or "").split(",") if item.strip())
        if args.tools is not None
        else None
    )
    voice_parts = (
        tuple(item for item in (args.voice_parts or "").split(",") if item.strip())
        if args.voice_parts is not None
        else None
    )
    try:
        if interactive:
            written, skipped = admin_init.wizard(
                args.dir,
                provider=args.provider,
                api_key=api_key,
                model=args.model,
                database_url=args.database_url,
                label=args.label,
                voice_layer=args.voice_layer,
                language=args.language,
                locale=args.locale,
                timezone=args.timezone,
                gateways=gateways,
                websearch=args.websearch,
                websearch_url=args.websearch_url,
                tools=tools,
                voice_parts=voice_parts,
                ask_capabilities=args.capabilities,
                force=args.force,
                ask_location=not args.no_location,
                color=color,
            )
        else:
            written, skipped = admin_init.init(
                args.dir,
                force=args.force,
                provider=args.provider,
                api_key=api_key or "",
                model=args.model or "",
                database_url=args.database_url or "",
                label=args.label or "",
                voice_layer=args.voice_layer or "off",
                language=args.language or "",
                locale=args.locale or "",
                timezone=args.timezone or "",
                gateways=gateways,
                websearch=args.websearch or "",
                websearch_url=args.websearch_url or "",
                tools=tools,
                voice_parts=voice_parts,
                base_url=args.base_url or "",
                api_key_env=args.api_key_env or "",
            )
    except admin_init.InitCancelled as exc:
        print(f"monoagens: {exc}", file=sys.stderr, flush=True)
        return 130
    except ValueError as exc:
        print(f"monoagens: {exc}", file=sys.stderr, flush=True)
        return 2
    if not interactive:
        print(
            admin_init.report(
                args.dir,
                written,
                skipped,
                provider=args.provider or "",
                model=args.model or "",
            ),
            flush=True,
        )
    return 0


def _models(args: argparse.Namespace) -> int:
    from .admin import models

    config = load_config(args.config, require_key=False, require_model=False)
    ids = models.list_models(config)
    print(f"{config.llm.provider}: {len(ids)} models from {models.models_url(config)}", flush=True)
    for model_id in ids:
        print(f"  {model_id}", flush=True)
    return 0


def _people_db(args: argparse.Namespace):
    db = _doc_store(args)[1]
    db.ensure()  # a deployment upgraded in place may miss the newest tables
    return db


def _people_list(args: argparse.Namespace) -> int:
    from .admin import people as admin_people

    _, paths = load_host(args.config)
    db = _people_db(args)
    try:
        for line in admin_people.summary(db, paths):
            print(line, flush=True)
    finally:
        db.dispose()
    return 0


def _people_link(args: argparse.Namespace) -> int:
    from .admin import people as admin_people

    db = _people_db(args)
    try:
        status = admin_people.link(db, args.person_id, args.gateway, args.address)
    finally:
        db.dispose()
    if status == "ok":
        print(f"linked {args.person_id} {args.gateway} {args.address}", flush=True)
        return 0
    if status == "taken":
        print("address already linked", file=sys.stderr, flush=True)
        return 2
    print("error: person_id is empty", file=sys.stderr, flush=True)
    return 2


def _people_add(args: argparse.Namespace) -> int:
    from .admin import people as admin_people

    _, paths = load_host(args.config)
    db = _people_db(args)
    try:
        person = admin_people.add(
            db,
            paths,
            args.gateway,
            args.address,
            args.role,
            name=args.name or "",
        )
    finally:
        db.dispose()
    print(f"person {person} -> {args.role} ({args.gateway}:{args.address})", flush=True)
    return 0


def _people_unlink(args: argparse.Namespace) -> int:
    from .admin import people as admin_people

    db = _people_db(args)
    try:
        removed = admin_people.unlink(db, args.person_id, args.gateway, args.address)
    finally:
        db.dispose()
    print("unlinked" if removed else "not linked", flush=True)
    return 0


def _people_remove(args: argparse.Namespace) -> int:
    from .admin import people as admin_people

    _, paths = load_host(args.config)
    db = _people_db(args)
    try:
        removed = admin_people.remove(
            db, paths, args.person_or_address, role=args.role, purge=args.purge
        )
    finally:
        db.dispose()
    print(f"removed from: {', '.join(removed) or '-'}", flush=True)
    return 0


def _people_grant(args: argparse.Namespace) -> int:
    from .admin import people as admin_people

    _, paths = load_host(args.config)
    db = _people_db(args)
    try:
        admin_people.grant(db, paths, args.person_id, args.role)
    finally:
        db.dispose()
    print(f"granted {args.role}", flush=True)
    return 0


def _people_revoke(args: argparse.Namespace) -> int:
    from .admin import people as admin_people

    _, paths = load_host(args.config)
    db = _people_db(args)
    try:
        admin_people.revoke(db, paths, args.person_id, args.role)
    finally:
        db.dispose()
    print(f"revoked {args.role}", flush=True)
    return 0


def _people_adopt(args: argparse.Namespace) -> int:
    from .admin import people as admin_people

    _, paths = load_host(args.config)
    db = _people_db(args)
    try:
        moved = admin_people.adopt(db, paths)
    finally:
        db.dispose()
    if not moved:
        print("nothing to adopt", flush=True)
    for old, fresh in moved:
        print(f"adopted {old} -> {fresh}", flush=True)
    return 0


def _people_dispatch(args: argparse.Namespace) -> int:
    handlers = {
        "list": _people_list,
        "add": _people_add,
        "link": _people_link,
        "unlink": _people_unlink,
        "remove": _people_remove,
        "delete": _people_remove,
        "rm": _people_remove,
        "grant": _people_grant,
        "revoke": _people_revoke,
        "adopt": _people_adopt,
    }
    if args.people_command in handlers:
        return handlers[args.people_command](args)
    raise ValueError(f"unknown people command '{args.people_command}'")


def _memory_db(args: argparse.Namespace):
    from .engine.db import Database

    host, _ = load_host(args.config)
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError("database URL is empty; memory commands need the configured database")
    return Database(url)


def _memory_list(args: argparse.Namespace) -> int:
    from .engine.memory.store import MemoryStore

    db = _memory_db(args)
    try:
        hits = asyncio.run(
            MemoryStore(db).list(
                scope=args.scope,
                person_id=args.person,
                thread_id=args.thread,
                limit=args.limit,
            )
        )
    finally:
        db.dispose()
    for hit in hits:
        print(f"{hit.id} [{hit.scope}] {hit.text}", flush=True)
    if not hits:
        print("memory: no rows", flush=True)
    return 0


def _memory_add(args: argparse.Namespace) -> int:
    from .engine.memory.store import MemoryStore

    db = _memory_db(args)
    try:
        ids = asyncio.run(
            MemoryStore(db).add(
                args.text,
                scope=args.scope,
                person_id=args.person,
                thread_id=args.thread,
            )
        )
    finally:
        db.dispose()
    print(f"memory add: {', '.join(ids) or 'stored'}", flush=True)
    return 0


def _memory_remove(args: argparse.Namespace) -> int:
    from .engine.memory.store import MemoryStore

    db = _memory_db(args)
    try:
        removed = asyncio.run(MemoryStore(db).forget(args.memory_id))
    finally:
        db.dispose()
    print("memory remove: removed" if removed else "memory remove: not found", flush=True)
    return 0 if removed else 2


def _memory_purge(args: argparse.Namespace) -> int:
    from .engine.db import Database
    from .engine.memory.store import MemoryStore

    if args.batch <= 0:
        raise ValueError("--batch must be >= 1")
    host, _ = load_host(args.config)
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError("database URL is empty; memory purge needs the configured database")
    db = Database(url)
    try:
        report = asyncio.run(MemoryStore(db).purge(dry_run=args.dry_run, batch=args.batch))
    finally:
        db.dispose()
    verb = "would purge" if report.dry_run else "purged"
    count = report.selected if report.dry_run else report.purged
    print(f"memory purge: {verb} {count} of {report.selected} expired rows", flush=True)
    return 0


def _memory_dispatch(args: argparse.Namespace) -> int:
    handlers = {
        "list": _memory_list,
        "ls": _memory_list,
        "search": _memory_list,
        "add": _memory_add,
        "remember": _memory_add,
        "remove": _memory_remove,
        "delete": _memory_remove,
        "rm": _memory_remove,
        "forget": _memory_remove,
        "purge": _memory_purge,
    }
    if args.memory_command in handlers:
        return handlers[args.memory_command](args)
    raise ValueError(f"unknown memory command '{args.memory_command}'")


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid integer: {text}") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("--batch must be >= 1")
    return value


def _console(args: argparse.Namespace) -> int:
    quiet = not (args.verbose or args.debug or args.level)
    return asyncio.run(serve(args.config, console=True, quiet=quiet))


# -------------------------------------------------------------------- parser


def _add_verbosity(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--verbose", action="store_true", help="INFO level on this run")
    group.add_argument("--debug", action="store_true", help="DEBUG level on this run")
    group.add_argument(
        "--level",
        default="",
        help="log level: DEBUG, INFO, WARNING, ERROR, or CRITICAL",
    )


def _gateway_parser(sub) -> argparse.ArgumentParser:
    gateway = sub.add_parser(
        "gateway", help="list, enable, disable, pair, delete, or update a gateway"
    )
    gateway.set_defaults(_group_parser=True)
    gateway_sub = gateway.add_subparsers(dest="gateway_command", parser_class=CLIParser)
    gateway_sub.add_parser("list", aliases=("ls",), help="show discovered gateways and state")
    add = gateway_sub.add_parser(
        "add", aliases=("enable", "install"), help="enable a gateway and seed its role policy"
    )
    add.add_argument("name", help="gateway name from `gateway list`")
    add.add_argument(
        "--connect",
        action="store_true",
        help="install the extra, enable, seed roles.yaml, and run the gateway's onboarding",
    )
    gateway_sub.add_parser("disable", help="switch the gateway off and keep its code")
    pair = gateway_sub.add_parser(
        "pair", aliases=("connect", "login"), help="pair the gateway in this terminal"
    )
    pair.add_argument("name", help="gateway name from `gateway list`")
    unpair = gateway_sub.add_parser(
        "unpair", aliases=("disconnect", "logout"), help="remove the paired session"
    )
    unpair.add_argument("name", help="gateway name from `gateway list`")
    delete = gateway_sub.add_parser(
        "delete",
        aliases=("remove", "rm", "uninstall"),
        help="remove code, sessions, config block, and role blocks",
    )
    delete.add_argument("name", help="gateway name from `gateway list`")
    update = gateway_sub.add_parser(
        "update", aliases=("upgrade",), help="reinstall the extra of an enabled gateway"
    )
    update.add_argument("name", nargs="?", default=None, help="gateway name; default: all enabled")
    doctor = gateway_sub.add_parser("doctor", help="report missing role gateway policies")
    doctor.add_argument("--fix", action="store_true", help="write the missing seed blocks")
    return gateway


def _plugins_parser(sub) -> argparse.ArgumentParser:
    parser = sub.add_parser("plugins", help="plugin lifecycle")
    parser.set_defaults(_group_parser=True)
    plugin_sub = parser.add_subparsers(dest="plugins_command", parser_class=CLIParser)
    plugin_sub.add_parser(
        "list", aliases=("ls",), help="show absent, installed, enabled, and unavailable plugins"
    )
    for verb, aliases, text in (
        ("install", ("add",), "place verified plugin code under plugin.root"),
        ("enable", (), "install if needed, then register the plugin at boot"),
        ("disable", (), "switch the plugin off and keep its files"),
        (
            "delete",
            ("remove", "rm", "uninstall"),
            "delete a disabled plugin's code and config entry",
        ),
    ):
        item = plugin_sub.add_parser(verb, aliases=aliases, help=text)
        item.add_argument("name", help="plugin name from its manifest")
    update = plugin_sub.add_parser(
        "update", aliases=("upgrade",), help="re-verify and re-enable installed plugins"
    )
    update.add_argument("name", nargs="?", default=None, help="plugin name; default: all")
    digest = plugin_sub.add_parser("digest", help="print the tree and code digests")
    digest.add_argument("directory", help="plugin directory")
    return parser


def _tools_parser(sub) -> argparse.ArgumentParser:
    parser = sub.add_parser("tools", help="toggle heavy tools such as the sandbox")
    parser.set_defaults(_group_parser=True)
    tools_sub = parser.add_subparsers(dest="tools_command", parser_class=CLIParser)
    tools_sub.add_parser("list", aliases=("ls",), help="show every tool and its switch")
    for verb, aliases, text in (
        ("enable", ("add",), "enable a heavy tool"),
        ("disable", ("remove", "rm"), "disable a heavy tool"),
    ):
        item = tools_sub.add_parser(verb, aliases=aliases, help=text)
        item.add_argument("name", help="tool name from `tools list`")
    return parser


def _setting_subcommands(sub, *, full: bool) -> None:
    sub.add_parser("list", help="print the effective settings")
    get = sub.add_parser("get", help="print one setting")
    get.add_argument("key", help="dotted key, e.g. engine.llm.model")
    setter = sub.add_parser("set", help="write one setting")
    setter.add_argument("key", help="dotted key, e.g. engine.llm.model")
    setter.add_argument("value", help="new value")
    for verb in ("reset", "unset", "default"):
        item = sub.add_parser(verb, help="restore one setting to its dataclass default")
        item.add_argument("key", help="dotted key, e.g. engine.llm.model")
    export = sub.add_parser("export", help="print or write config.yaml as YAML")
    export.add_argument("path", nargs="?", default=None, help="target file; default: stdout")
    importer = sub.add_parser("import", help="merge a YAML file into config.yaml")
    importer.add_argument("file", nargs="?", default=None, help="source file; default: refresh DB")
    sub.add_parser("help", help="show this help and exit 0")
    if not full:
        return
    sub.add_parser("menu", help="interactive settings menu")
    for verb, text in (
        ("model", "show or set engine.llm.model"),
        ("provider", "show or set engine.llm.provider"),
        ("websearch", "show or set web_search.provider"),
    ):
        item = sub.add_parser(verb, help=text)
        item.add_argument("value", nargs="?", default="", help=f"new {verb}")


def _setting_parser(sub) -> argparse.ArgumentParser:
    parser = sub.add_parser(
        "setting",
        aliases=("settings",),
        help="interactive settings, or get/set one value in config.yaml",
    )
    parser.set_defaults(_group_parser=True)
    _setting_subcommands(
        parser.add_subparsers(dest="setting_command", parser_class=CLIParser), full=True
    )
    return parser


def _people_parser(sub) -> argparse.ArgumentParser:
    people = sub.add_parser("people", help="person identity operations")
    people.set_defaults(_group_parser=True)
    people_sub = people.add_subparsers(dest="people_command", parser_class=CLIParser)
    people_sub.add_parser(
        "list", aliases=("ls",), help="list identities, roles, and linked addresses"
    )
    add = people_sub.add_parser("add", help="create/link the first identity for a role")
    add.add_argument("gateway", help="gateway name from config.yaml")
    add.add_argument("address", help="the gateway's own address for that person")
    add.add_argument("--role", default="administrator", help="role document to append to")
    add.add_argument("--name", default="", help="display name")
    remove = people_sub.add_parser(
        "remove", aliases=("delete", "rm"), help="remove a person from one or all roles"
    )
    remove.add_argument("person_or_address", help="per_… id, address, or display name")
    remove.add_argument("--role", default=None, help="role to remove from; default: all")
    remove.add_argument("--purge", action="store_true", help="also delete the identity and links")
    link = people_sub.add_parser("link", help="link a gateway address to an identity")
    link.add_argument("person_id", help="per_… identity id")
    link.add_argument("gateway", help="gateway name from config.yaml")
    link.add_argument("address", help="the gateway's own address for that person")
    unlink = people_sub.add_parser("unlink", help="remove one gateway link")
    unlink.add_argument("person_id", help="per_… identity id")
    unlink.add_argument("gateway", help="gateway name")
    unlink.add_argument("address", nargs="?", default="", help="specific address; default: all")
    grant = people_sub.add_parser("grant", help="append a person to a role")
    grant.add_argument("person_id", help="per_… identity id")
    grant.add_argument("role", help="role name from roles.yaml")
    revoke = people_sub.add_parser("revoke", help="remove a person from a role")
    revoke.add_argument("person_id", help="per_… identity id")
    revoke.add_argument("role", help="role name from roles.yaml")
    people_sub.add_parser("adopt", help="move legacy raw addresses onto per_… identities")
    return people


def _init_parser(sub) -> None:
    init = sub.add_parser("init", help="write config.yaml, roles.yaml, soul.yaml, and .env")
    init.add_argument("--dir", default=".", help="target directory")
    init.add_argument(
        "--no-location",
        action="store_true",
        help="use --dir without asking where the soul lives (for an installer-owned directory)",
    )
    init.add_argument("--force", action="store_true", help="replace existing files without asking")
    init.add_argument(
        "--provider", default=None, help="provider name (asked on a terminal; required when piped)"
    )
    init.add_argument(
        "--base-url",
        default=None,
        help="override the provider endpoint written to engine.llm.base_url",
    )
    init.add_argument(
        "--api-key-env",
        default=None,
        help="override the provider key variable written to engine.llm.api_key_env",
    )
    init.add_argument(
        "--model", default=None, help="model id to write; omitted leaves it for the operator"
    )
    init.add_argument(
        "--api-key-file",
        default=None,
        help="read the provider key from a file; never pass a key on the command line",
    )
    init.add_argument(
        "--database-url",
        default=None,
        help="Postgres URL; written to .env, never to YAML",
    )
    init.add_argument(
        "--label", default=None, help="label for the first administrator (roles.yaml)"
    )
    init.add_argument(
        "--voice-layer",
        choices=("on", "off"),
        default=None,
        help="turn the soul voice layer on; off (default) keeps every part inert",
    )
    init.add_argument(
        "--language",
        default=None,
        help="language catalog code written to soul.language (default: the host locale)",
    )
    init.add_argument(
        "--locale",
        default=None,
        help="locale such as en-US written to soul.locale (default: the host locale)",
    )
    init.add_argument(
        "--timezone",
        default=None,
        help="IANA zone written to soul.timezone (default: the host zone)",
    )
    init.add_argument(
        "--capabilities",
        action="store_true",
        help="ask for gateway, websearch, tool, and voice-part capabilities",
    )
    init.add_argument(
        "--gateway",
        action="append",
        default=None,
        help="enable a discovered gateway (repeatable; default: none, console only)",
    )
    init.add_argument("--websearch", default=None, help="search backend or off")
    init.add_argument("--websearch-url", default=None, help="SearXNG base URL")
    init.add_argument(
        "--tools", default=None, help="comma-separated tool names for the member role"
    )
    init.add_argument("--voice-parts", default=None, help="comma-separated voice sub-sections")
    init.add_argument(
        "--non-interactive",
        action="store_true",
        help="never prompt; fail when --provider is missing",
    )
    init.add_argument("--no-color", action="store_true", help="plain output (also via NO_COLOR)")


class CLIParser(argparse.ArgumentParser):
    """A parser that prints help when a group has no subcommand and suggests typos."""

    group_parsers: dict[str, tuple[argparse.ArgumentParser, str]]

    def error(self, message: str) -> None:
        print(f"error: {message}", file=sys.stderr)
        match = re.search(r"invalid choice: '([^']+)'", message)
        if match:
            bad = match.group(1)
            known = [item for item in re.findall(r"'([^']+)'", message) if item != bad]
            matches = difflib.get_close_matches(bad, known, n=3)
            if matches:
                print("did you mean: " + ", ".join(matches), file=sys.stderr)
        self.print_help(sys.stderr)
        self.exit(2)


def build_parser() -> argparse.ArgumentParser:
    parser = CLIParser(
        prog="monoagens", description="MonoAgens persistent mono agent harness"
    )
    parser.add_argument(
        "--config", default=None, help="path to config.yaml (default: ./config.yaml)"
    )
    parser.add_argument("--env", default=".env", help="path to .env (default: ./.env)")
    sub = parser.add_subparsers(dest="command", parser_class=CLIParser)
    run = sub.add_parser("run", help="run the gateways enabled in config.yaml")
    run.add_argument("--takeover", action="store_true", help="take the lease from another instance")
    _add_verbosity(run)
    sub.add_parser("start", help="start the background service (native daemon or compose stack)")
    sub.add_parser("stop", help="stop the background service")
    sub.add_parser("restart", help="restart the background service")
    sub.add_parser("status", help="mode, PID, lease holder, and version")
    logs = sub.add_parser("logs", help="show the service log")
    logs.add_argument("-f", "--follow", action="store_true", help="stream new lines")
    _add_verbosity(logs)
    sub.add_parser("reload", help="validate the host files and ask the primary to reload")
    setting_parser = _setting_parser(sub)
    for shortcut in ("model", "provider", "websearch"):
        item = sub.add_parser(shortcut, help=f"show or switch the {shortcut}")
        item.add_argument("value", nargs="?", default="", help=f"new {shortcut}")
    console = sub.add_parser(
        "console",
        aliases=("cli",),
        help="local REPL over the same soul and database (no gateway)",
    )
    _add_verbosity(console)
    gateway_parser = _gateway_parser(sub)
    plugins_parser = _plugins_parser(sub)
    tools_parser = _tools_parser(sub)
    update = sub.add_parser("update", aliases=("upgrade",), help="update core, extras, and schema")
    update.add_argument("--core", action="store_true", help="update only the core package")
    doctor = sub.add_parser("doctor", help="health check with an optional repair")
    doctor.add_argument("--fix", action="store_true", help="repair policy and cache problems")
    backup = sub.add_parser("backup", help="write a portable archive of database and host files")
    backup.add_argument("path", nargs="?", default=None, help="target .tar.gz")
    backup.add_argument(
        "--output", "-o", default=None, help="target .tar.gz (alias for the positional path)"
    )
    restore = sub.add_parser(
        "restore", help="extract an archive and optionally restore the database"
    )
    restore.add_argument("file", help="archive written by `monoagens backup`")
    restore.add_argument("--yes", action="store_true", help="also restore the database")
    restore.add_argument("--force", action="store_true", help="ignore an active lease")
    uninstall = sub.add_parser("uninstall", help="stop the service and remove the $PATH binary")
    uninstall.add_argument("--purge", action="store_true", help="also remove the data directory")
    uninstall.add_argument("--yes", action="store_true", help="confirm --purge without a prompt")
    sub.add_parser("selftest", help="quick check of stores, tools, and the LLM")
    sub.add_parser("backfill", help="pull old history through a throwaway device")
    _init_parser(sub)
    sub.add_parser("models", help="list the model ids the configured provider serves")
    config = sub.add_parser("config", help="config operations")
    config.set_defaults(_group_parser=True)
    _setting_subcommands(
        config.add_subparsers(dest="config_command", parser_class=CLIParser), full=False
    )
    database = sub.add_parser("db", help="database operations")
    database.set_defaults(_group_parser=True)
    database_sub = database.add_subparsers(dest="db_command", parser_class=CLIParser)
    database_sub.add_parser("upgrade", help="create and seed the database schema")
    downgrade = database_sub.add_parser("downgrade", help="roll the schema back one revision")
    downgrade.add_argument(
        "revision", nargs="?", default="-1", help="target revision; default: -1"
    )
    db_check = database_sub.add_parser(
        "check", aliases=("status",), help="schema and database content summary"
    )
    db_check.add_argument("--json", action="store_true", help="emit JSON")
    db_backup = database_sub.add_parser("backup", help="pg_dump a custom-format archive")
    db_backup.add_argument("--out", required=True, help="target file")
    db_restore = database_sub.add_parser("restore", help="restore from a backup")
    db_restore.add_argument("file", help="backup file")
    db_restore.add_argument("--yes", action="store_true", help="confirm the restore")
    db_restore.add_argument("--force", action="store_true", help="ignore an active lease")
    people_parser = _people_parser(sub)
    memory = sub.add_parser("memory", help="canonical memory maintenance")
    memory.set_defaults(_group_parser=True)
    memory_sub = memory.add_subparsers(dest="memory_command", parser_class=CLIParser)
    listing = memory_sub.add_parser(
        "list", aliases=("ls", "search"), help="list canonical memories"
    )
    listing.add_argument("--scope", default="global", help="global, person, or thread")
    listing.add_argument("--person", default="", help="person id for person scope")
    listing.add_argument("--thread", default="", help="thread id for thread scope")
    listing.add_argument("--limit", type=int, default=20, help="rows to print")
    remember = memory_sub.add_parser("add", aliases=("remember",), help="store one memory")
    remember.add_argument("text", help="memory text")
    remember.add_argument("--scope", default="global", help="global, person, or thread")
    remember.add_argument("--person", default="", help="person id for person scope")
    remember.add_argument("--thread", default="", help="thread id for thread scope")
    forget = memory_sub.add_parser(
        "remove", aliases=("delete", "rm", "forget"), help="delete one memory by id"
    )
    forget.add_argument("memory_id", help="mem_… id")
    memory_purge = memory_sub.add_parser("purge", help="delete expired, unpinned memories")
    memory_purge.add_argument(
        "--dry-run", action="store_true", help="report what would be purged, change nothing"
    )
    memory_purge.add_argument(
        "--batch", type=_positive_int, default=500, help="rows per batch (>= 1)"
    )
    parser.group_parsers = {
        "gateway": (gateway_parser, "gateway_command"),
        "plugins": (plugins_parser, "plugins_command"),
        "tools": (tools_parser, "tools_command"),
        "setting": (setting_parser, "setting_command"),
        "settings": (setting_parser, "setting_command"),
        "config": (config, "config_command"),
        "db": (database, "db_command"),
        "people": (people_parser, "people_command"),
        "memory": (memory, "memory_command"),
    }
    return parser


def _dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    command = args.command
    if command is None:
        parser.print_help()
        return 0
    group = getattr(parser, "group_parsers", {}).get(command)
    if group is not None:
        group_parser, dest = group
        value = getattr(args, dest, None)
        if value is None or value == "help":
            group_parser.print_help()
            return 0
    if command in {"console", "cli"}:
        return _console(args)
    if command == "run":
        return _run(args)
    if command in {"start", "stop", "restart", "status"}:
        return _service(args)
    if command == "logs":
        return _logs(args)
    if command == "reload":
        return _reload(args)
    if command in {"setting", "settings"}:
        return _setting(args)
    if command == "config":
        args.setting_command = args.config_command
        return _setting(args)
    if command in {"model", "provider", "websearch"}:
        return _shortcut(args)
    if command == "gateway":
        return _gateway(args)
    if command == "plugins":
        return _plugins(args)
    if command == "tools":
        return _tools(args)
    if command in {"update", "upgrade"}:
        return _update(args)
    if command == "doctor":
        return _doctor(args)
    if command == "backup":
        return _backup(args)
    if command == "restore":
        return _restore(args)
    if command == "uninstall":
        return _uninstall(args)
    if command == "db":
        return _db_dispatch(args)
    if command == "people":
        return _people_dispatch(args)
    if command == "memory":
        return _memory_dispatch(args)
    if command == "init":
        return _init(args)
    if command == "models":
        return _models(args)
    if command == "selftest":
        return asyncio.run(selftest(load_config(args.config, require_key=False)))
    if command == "backfill":
        return asyncio.run(backfill(args.config))
    raise ValueError(f"unknown command '{command}'")


def main(argv: list[str] | None = None) -> int:
    handler = setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {"console", "cli"} and not (
        getattr(args, "verbose", False) or getattr(args, "debug", False)
    ):
        handler.setLevel(logging.CRITICAL)
        from .engine.turn import observability

        observability.silence_third_party(debug=False)
    config_path = Path(args.config).resolve().parent if args.config else Path.cwd()
    load_env(args.env, root=config_path)
    try:
        return _dispatch(args, parser)
    except ConfigError as exc:
        print(f"monoagens: {exc}", file=sys.stderr, flush=True)
        return 2
    except (ValueError, OSError) as exc:
        print(f"monoagens: {exc}", file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        return 130
if __name__ == "__main__":
    sys.exit(main())
