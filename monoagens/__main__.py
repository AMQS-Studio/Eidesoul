"""Entrypoint: `monoagens run|console|init|config|db|memory|plugins|selftest|backfill`."""


import argparse
import asyncio
import contextlib
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from .engine.config import (
    Config,
    ConfigError,
    load_config,
    load_env,
    load_host,
    resolve_database_url,
)

log = logging.getLogger(__name__)


def setup_logging() -> None:
    """Boot default: text format; `build_runtime` applies `engine.logging` once loaded."""
    from .engine.turn import observability

    observability.install("text", "INFO")


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


async def build_runtime(config_path: str | None, *, console: bool = False):
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
    from .gateways import build_gateways, ensure_schemas

    host, paths = load_host(config_path)
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError("database URL is empty; set it in config.yaml or MONOAGENS_DATABASE_URL")
    db = Database(url, pool_size=host.database.pool_size, pool_timeout=host.database.pool_timeout)
    db.ensure()
    ensure_schemas(db)
    documents = DocumentStore(db, paths, sync=host.sync_documents)
    documents.seed()
    documents.sync()
    config = load_config(paths.config, documents=documents, require_gateway=True)
    from .engine.turn import observability

    observability.configure(config.logging)
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
    affect_enabled = bool(voice.enabled and voice.affect.enabled)
    style_enabled = bool(voice.enabled and voice.style.enabled)
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
    force = ("cli",) if console else ()
    only = ("cli",) if console else ()
    gateways = build_gateways(
        config, db=db, history=history, windows=windows, only=only, force=force
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
    # ADR-0014 §4.5: every registered gateway passed `ensure_core` in `build_gateways`.
    agent.runtime.gateways = tuple(gateway.name for gateway in gateways)
    agent.runtime.gateway_core_ok = len(gateways)
    lease = Lease(db)
    if console:
        lock = None
    else:
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
        "lock": lock,
    }


async def serve(config_path: str | None, *, console: bool = False, takeover: bool = False) -> int:
    from .engine.jobs.lease import Lease

    runtime = await build_runtime(config_path, console=console)
    agent = runtime["agent"]
    lease: Lease = runtime["lease"]
    supervisor = runtime["supervisor"]
    lock = runtime["lock"]
    try:
        holder = lease.holder()
        if console and not takeover:
            if holder is not None:
                print(
                    f"Lease held by {holder.host} pid {holder.pid}; use --takeover to take it.",
                    flush=True,
                )
                return 2
            takeover = True
        if console and takeover and holder is not None and holder.instance_id != lease.instance_id:
            print(
                f"Taking lease from {holder.host} pid {holder.pid}; that instance will freeze.",
                flush=True,
            )
            await asyncio.sleep(1)
        supervisor.takeover = takeover
        await supervisor.run()
        return 0
    finally:
        await agent.close()
        if lock is not None:
            with contextlib.suppress(Exception):
                lock.close()


async def backfill(config_path: str | None) -> int:
    """Transport-owned history backfill; the core names no transport."""
    from .gateways import cli_entry

    return int(await cli_entry("backfill", config_path))


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
    from .gateways import ensure_schemas

    hub = Hub(config.mcp)
    await hub.start()
    url = config.database_url()
    if not url:
        raise ConfigError(
            "database URL is empty; `monoagens selftest` needs the configured database"
        )
    db = Database(url)
    db.ensure()
    ensure_schemas(db)
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
            specs, "write", {"path": "private/selftest.txt", "content": "monoagens-selftest"}, ctx
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
                when=datetime.now(),
                text="monoagens-selftest-archive",
                author="manual_owner",
                visibility=3,
            ),
        )
        found = history.search("selftest", "monoagens-selftest-archive")
        history.mark_deleted("selftest", "selftest-1", "p2", "Admin")
        removed = history.find("selftest", "selftest-1")
        passed = [item.id for item in found] == ["selftest-1"] and (
            found[0].author == "manual_owner" and found[0].visibility == 3
        ) and removed is not None and removed.deleted is True and removed.deleted_admin is True
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
            _report(
                "privacy", _verdict(all(checks)), "" if all(checks) else str(checks)
            )
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

        runtime = resolve_runtime(config.sandbox)
        if runtime is None:
            _report("sandbox", "SKIP", f"{config.sandbox.runtime or 'podman/docker'} not found")
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

        affect.turn_start("selftest", "selftest-1")
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
            "selftest-1",
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


def _doc_store(args: argparse.Namespace):
    from .engine.config import load_host, resolve_database_url
    from .engine.db import Database
    from .engine.stores.documents import DocumentStore

    host, paths = load_host(args.config)
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError("database URL is empty; set it in config.yaml or MONOAGENS_DATABASE_URL")
    db = Database(url, pool_size=host.database.pool_size, pool_timeout=host.database.pool_timeout)
    return DocumentStore(db, paths, sync=True), db


def _config_export(args: argparse.Namespace) -> int:
    docs, db = _doc_store(args)
    try:
        count = docs.export_to_files()
    finally:
        db.dispose()
    print(f"config export: {count} documents to files", flush=True)
    return 0


def _config_import(args: argparse.Namespace) -> int:
    from .engine.access.roles import RoleError

    docs, db = _doc_store(args)
    try:
        count = docs.import_from_files()
    except RoleError as exc:
        raise ConfigError(str(exc)) from exc
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
        "        --language <code> --locale <tag> --timezone <IANA>   (default: the host)\n"
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
                force=args.force,
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

    db = _people_db(args)
    try:
        for line in admin_people.summary(db):
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
    from .engine.config import load_host

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
        removed = admin_people.unlink(db, args.person_id, args.gateway)
    finally:
        db.dispose()
    print("unlinked" if removed else "not linked", flush=True)
    return 0


def _people_dispatch(args: argparse.Namespace) -> int:
    handlers = {
        "list": _people_list,
        "add": _people_add,
        "link": _people_link,
        "unlink": _people_unlink,
    }
    if args.people_command in handlers:
        return handlers[args.people_command](args)
    build_parser().print_help()
    return 2


def _memory_dispatch(args: argparse.Namespace) -> int:
    if args.memory_command == "purge":
        return _memory_purge(args)
    build_parser().print_help()
    return 2


def _memory_purge(args: argparse.Namespace) -> int:
    from .engine.db import Database
    from .engine.memory.store import MemoryStore

    host, _ = load_host(args.config)
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError("database URL is empty; memory purge needs the configured database")
    db = Database(url)
    try:
        report = asyncio.run(
            MemoryStore(db).purge(dry_run=args.dry_run, batch=args.batch)
        )
    finally:
        db.dispose()
    verb = "would purge" if report.dry_run else "purged"
    count = report.selected if report.dry_run else report.purged
    print(f"memory purge: {verb} {count} of {report.selected} expired rows", flush=True)
    return 0


def _plugin_db(host):
    """Best-effort audit store; a missing or unreachable DB never blocks the host-file action."""
    url = resolve_database_url(host.database)
    if not url:
        return None
    from .engine.db import Database

    try:
        return Database(url, pool_size=1, pool_timeout=2)
    except Exception:  # a broken URL never blocks a host-file action
        return None


def _plugins_dispatch(args: argparse.Namespace) -> int:
    """ADR-0028 lifecycle: list/install/enable/disable/remove, host files only."""
    from .engine import plugins

    host, paths = load_host(args.config)
    settings = plugins.PluginLoader.from_host(host, paths)
    action = args.plugins_command
    if action not in {"list", "install", "enable", "disable", "remove"}:
        build_parser().print_help()
        return 2
    db = None if action == "list" else _plugin_db(host)
    try:
        if action == "list":
            found = plugins.states(settings)
            if not found:
                print("plugins: none configured", flush=True)
            for item in found:
                detail = f" - {item.reason}" if item.reason else ""
                print(f"plugin {item.name}: {item.state}{detail}", flush=True)
            return 0
        actor = os.environ.get("USER", "") or os.environ.get("USERNAME", "")
        if action == "install":
            item = plugins.install(settings, args.name, actor=actor, db=db)
        elif action == "enable":
            item = plugins.enable(settings, args.name, actor=actor, db=db)
        elif action == "disable":
            item = plugins.disable(settings, args.name, actor=actor, db=db)
        else:
            item = plugins.remove(settings, args.name, actor=actor, db=db)
    except (plugins.PluginError, OSError) as exc:
        print(f"monoagens: {exc}", file=sys.stderr, flush=True)
        return 2
    finally:
        if db is not None:
            db.dispose()
    detail = f" - {item.reason}" if item.reason else ""
    print(f"plugin {item.name}: {item.state}{detail}", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monoagens", description="MonoAgens persistent agent harness"
    )
    parser.add_argument(
        "--config", default=None, help="path to config.yaml (default: ./config.yaml)"
    )
    parser.add_argument("--env", default=".env", help="path to .env (default: ./.env)")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="run the gateways enabled in config.yaml")
    run.add_argument("--takeover", action="store_true", help="take the lease from another instance")
    console = sub.add_parser("console", help="local REPL over the same soul and database")
    console.add_argument(
        "--takeover", action="store_true", help="take the lease (default for console)"
    )
    console.add_argument(
        "--no-takeover", action="store_true", help="do not take the lease; exit when it is held"
    )
    sub.add_parser("selftest", help="quick check of stores, tools, and the LLM")
    sub.add_parser("backfill", help="pull old history through a throwaway device")
    init = sub.add_parser("init", help="write config.yaml, roles.yaml, soul.yaml, and .env")
    init.add_argument("--dir", default=".", help="target directory")
    init.add_argument("--force", action="store_true", help="replace existing files without asking")
    init.add_argument(
        "--provider", default=None, help="provider name (asked on a terminal; required when piped)"
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
        "--non-interactive",
        action="store_true",
        help="never prompt; fail when --provider is missing",
    )
    init.add_argument("--no-color", action="store_true", help="plain output (also via NO_COLOR)")
    sub.add_parser("models", help="list the model ids the configured provider serves")
    config = sub.add_parser("config", help="config operations")
    config_sub = config.add_subparsers(dest="config_command")
    config_sub.add_parser("export", help="write the file mirrors from the DB documents")
    config_sub.add_parser("import", help="upsert the DB documents from host files")
    database = sub.add_parser("db", help="database operations")
    database_sub = database.add_subparsers(dest="db_command")
    database_sub.add_parser("upgrade", help="create and seed the database schema")
    db_check = database_sub.add_parser("check", help="schema and database content summary")
    db_check.add_argument("--json", action="store_true", help="emit JSON")
    db_backup = database_sub.add_parser("backup", help="pg_dump a custom-format archive")
    db_backup.add_argument("--out", required=True, help="target file")
    db_restore = database_sub.add_parser("restore", help="restore from a backup")
    db_restore.add_argument("file", help="backup file")
    db_restore.add_argument("--yes", action="store_true", help="confirm the restore")
    db_restore.add_argument("--force", action="store_true", help="ignore an active lease")
    people = sub.add_parser("people", help="person identity operations")
    people_sub = people.add_subparsers(dest="people_command")
    people_sub.add_parser("list", help="list identities, roles, and linked addresses")
    people_link = people_sub.add_parser("link", help="link a gateway address to an identity")
    people_add = people_sub.add_parser("add", help="create/link the first identity for a role")
    people_add.add_argument("gateway", help="gateway name from config.yaml")
    people_add.add_argument("address", help="the gateway's own address for that person")
    people_add.add_argument("--role", default="administrator", help="role document to append to")
    people_add.add_argument("--name", default="", help="display name")
    people_link.add_argument("person_id", help="per_… identity id")
    people_link.add_argument("gateway", help="gateway name from config.yaml")
    people_link.add_argument("address", help="the gateway's own address for that person")
    people_unlink = people_sub.add_parser("unlink", help="remove one gateway link")
    people_unlink.add_argument("person_id", help="per_… identity id")
    people_unlink.add_argument("gateway", help="gateway name")
    people_sub.add_parser("adopt", help="move legacy raw addresses onto per_… identities")
    memory = sub.add_parser("memory", help="canonical memory maintenance")
    memory_sub = memory.add_subparsers(dest="memory_command")
    memory_purge = memory_sub.add_parser("purge", help="delete expired, unpinned memories")
    memory_purge.add_argument(
        "--dry-run", action="store_true", help="report what would be purged, change nothing"
    )
    memory_purge.add_argument("--batch", type=int, default=500, help="rows per batch")
    plugin_parser = sub.add_parser("plugins", help="plugin lifecycle")
    plugin_sub = plugin_parser.add_subparsers(dest="plugins_command")
    plugin_sub.add_parser(
        "list", help="show absent, installed, enabled, and unavailable plugins"
    )
    for verb, text in (
        ("install", "place verified plugin code under plugin.root"),
        ("enable", "install if needed, then register the plugin at boot"),
        ("disable", "switch the plugin off and keep its files"),
        ("remove", "delete a disabled plugin's code and config entry"),
    ):
        item = plugin_sub.add_parser(verb, help=text)
        item.add_argument("name", help="plugin name from its manifest")
    return parser




def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = build_parser().parse_args(argv)
    load_env(args.env)
    try:
        if args.command == "init":
            return _init(args)
        if args.command == "models":
            return _models(args)
        if args.command == "config" and args.config_command == "export":
            return _config_export(args)
        if args.command == "config" and args.config_command == "import":
            return _config_import(args)
        dispatch = {
            "people": _people_dispatch,
            "memory": _memory_dispatch,
            "plugins": _plugins_dispatch,
        }
        if args.command in dispatch:
            return dispatch[args.command](args)
        if args.command == "db":
            handlers = {
                "upgrade": _db_upgrade,
                "check": _db_check,
                "backup": _db_backup,
                "restore": _db_restore,
            }
            if args.db_command in handlers:
                return handlers[args.db_command](args)
            build_parser().print_help()
            return 2
        if args.command == "selftest":
            return asyncio.run(selftest(load_config(args.config, require_key=False)))
        if args.command == "backfill":
            return asyncio.run(backfill(args.config))
        if args.command == "console":
            return asyncio.run(
                serve(args.config, console=True, takeover=not args.no_takeover)
            )
        return asyncio.run(serve(args.config, takeover=args.takeover))
    except ConfigError as exc:
        print(f"monoagens: {exc}", file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
