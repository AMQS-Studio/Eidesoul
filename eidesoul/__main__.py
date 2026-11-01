"""Entrypoint: `eidesoul run|console|init|config|db|memory|selftest|backfill|library`."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

from .engine.config import Config, ConfigError, load_config, load_env, load_host, resolve_database_url

log = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


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
    from .engine.agent import Agent
    from .engine.ambient import Ambient
    from .engine.config import load_host, resolve_database_url
    from .engine.db import Database
    from .engine.lease import Lease, lock_data_dir
    from .engine.llm import LLM
    from .engine.mcp import Hub
    from .engine.memory import build_memory
    from .engine.stores.affect import AffectStore
    from .engine.stores.documents import DocumentStore
    from .engine.stores.history import HistoryStore
    from .engine.stores.recipes import RecipeStore
    from .engine.stores.skills import SkillStore
    from .engine.stores.currents import CurrentsStore
    from .engine.stores.style import StyleStore
    from .engine.stores.vibe import VibeRunner
    from .engine.context import WorkingMemory
    from .engine.memory.ledger import ActionLedger
    from .engine.supervisor import Supervisor
    from .engine.tools import REGISTRY as TOOLS
    from .gateways import build_gateways, ensure_schemas

    host, paths = load_host(config_path)
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError("database URL is empty; set it in config.yaml or EIDESOUL_DATABASE_URL")
    db = Database(url, pool_size=host.database.pool_size, pool_timeout=host.database.pool_timeout)
    db.ensure()
    ensure_schemas(db)
    documents = DocumentStore(db, paths, sync=host.sync_documents)
    documents.seed()
    config = load_config(paths.config, documents=documents, require_gateway=True)
    history = HistoryStore(db, config.workspace)
    windows = WorkingMemory(store=history)
    ledger = ActionLedger(db)
    skills = SkillStore(db, config.skills.max_index_chars)
    recipes = RecipeStore(db)
    memory = await build_memory(config, db=db)
    affect = (
        AffectStore(db, config.affect, soul=config.soul.persona) if config.affect.enabled else None
    )
    style = StyleStore(db, config.style) if config.style.enabled else None
    currents = CurrentsStore(db)
    vibe = None
    if affect is not None and config.affect.vibe.enabled:
        vibe = VibeRunner(db, config.affect.vibe)
    hub = Hub(config.mcp)
    await hub.start()
    llm = LLM(config)
    force = ("cli",) if console else ()
    only = ("cli",) if console else ()
    gateways = build_gateways(
        config, db=db, history=history, windows=windows, only=only, force=force
    )
    ambient = None
    if config.ambient.enabled:
        ambient = Ambient(config, llm, affect, style, memory, history, windows, currents)
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
        currents=currents,
        vibe=vibe,
        gateways=tuple(gateways),
        documents=documents,
    )
    lease = Lease(db)
    if console:
        lock = None
    else:
        try:
            lock = lock_data_dir(data_dir(config) / ".eidesoul.lock")
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
    from .engine.lease import Lease

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
    """Pair a throwaway device for history sync, then log it out; the primary is untouched."""
    from neonize.aioze.client import NewAClient
    from neonize.aioze.events import ConnectedEv, HistorySyncEv

    from .engine.config import drop_session, secure_db
    from .engine.db import Database
    from .engine.stores.documents import DocumentStore
    from .engine.stores.history import HistoryStore
    from .gateways import ensure_schemas
    from .gateways.whatsapp import directory, history_sync, ingress
    from .gateways.whatsapp.config import WhatsAppSettings

    host, paths = load_host(config_path)
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError("database URL is empty")
    db = Database(url, pool_size=host.database.pool_size, pool_timeout=host.database.pool_timeout)
    db.ensure()
    ensure_schemas(db)
    documents = DocumentStore(db, paths, sync=host.sync_documents)
    documents.seed()
    config = load_config(paths.config, documents=documents, require_gateway=True)
    raw = config.gateways.get("whatsapp")
    if raw is None:
        raise ConfigError("gateway 'whatsapp' is not configured in config.yaml")
    settings = WhatsAppSettings.from_mapping(raw.enabled, raw.options, paths.config.parent)
    history = HistoryStore(db, config.workspace)
    session = settings.session_db.parent / "backfill.db"
    drop_session(session)
    client = NewAClient(str(session), props=history_sync.history_props(True))
    done = asyncio.Event()
    paired = False

    @client.event(ConnectedEv)
    async def on_connected(accepted: NewAClient, ev) -> None:
        nonlocal paired
        paired = True
        secure_db(session)
        named = await directory.sync_group_names(accepted, db)
        log.info("backfill: synced group names: %d", named)
        log.info("backfill connected; waiting for history sync from the phone")

    @client.event(HistorySyncEv)
    async def on_history(accepted: NewAClient, ev) -> None:
        if ev.Data is None:
            return
        count, chat_ids, finished = history_sync.ingest_sync(
            db, history, ev.Data, ingress.self_jid(accepted.me)
        )
        log.info(
            "backfill: %d messages, %d chats, sync=%s progress=%d%s",
            count,
            len(chat_ids),
            ev.Data.syncType,
            ev.Data.progress,
            " (done)" if finished else "",
        )
        if finished:
            done.set()

    try:
        await client.connect()
        secure_db(session)
        try:
            await asyncio.wait_for(done.wait(), 900)
        except TimeoutError:
            log.warning("backfill timed out after 900 seconds")
    finally:
        logged_out = False
        if paired:
            try:
                await client.logout()
                logged_out = True
            except Exception:
                log.exception("failed to log out the backfill device")
        with contextlib.suppress(Exception):
            await client.stop()
        if logged_out:
            drop_session(session)
        db.dispose()
    return 0 if paired and done.is_set() else 1


async def selftest(config: Config) -> int:
    """Exercise stores, tools, and the LLM against a throwaway database."""
    import tempfile
    from datetime import datetime

    from .engine.commands import REGISTRY as COMMANDS
    from .engine.context import ChatMessage
    from .engine.db import Database
    from .engine.decision import Appraisal, Decision, RelationshipDelta
    from .engine.i18n import t
    from .engine.llm import LLM
    from .engine.mcp import Hub
    from .engine.permissions import commands_for, tool_patterns, tools_for
    from .engine.tools import REGISTRY, ToolContext, execute
    from .engine.tools.sandbox import resolve_runtime
    from .engine.memory import MemoryHit, build_memory, memory_status
    from .engine.memory.ledger import ActionLedger
    from .engine.stores.affect import AffectStore
    from .engine.stores.documents import DocumentStore
    from .engine.stores.history import HistoryStore
    from .engine.stores.recipes import RecipeStore
    from .engine.stores.skills import SkillStore
    from .engine.stores.style import StyleStore
    from .gateways import ensure_schemas

    hub = Hub(config.mcp)
    await hub.start()
    root = Path(tempfile.mkdtemp(prefix="eidesoul-selftest-"))
    db = Database(f"sqlite+pysqlite:///{root / 'selftest.db'}")
    db.ensure()
    ensure_schemas(db)
    docs = DocumentStore(db, config.paths, sync=False)
    docs.seed()
    history = HistoryStore(db, config.workspace)
    ledger = ActionLedger(db)
    affect = AffectStore(db, config.affect, soul=config.soul.persona)
    style = StyleStore(db, config.style)
    skills = SkillStore(db, config.skills.max_index_chars)
    recipes = RecipeStore(db)
    memory = await build_memory(config, db=db)
    results: list[bool] = []
    try:
        role = _role(config)
        for name, item in config.roles.by_name.items():
            tools = [handler.name for handler in tools_for(REGISTRY, (item,))]
            tools += [handler.name for handler in hub.specs((item,))]
            commands = [handler.name for handler in commands_for(COMMANDS, (item,))]
            print(f"role {name}: command={', '.join(commands) or '-'} | tool={', '.join(tools) or '-'}", flush=True)

        config.workspace.mkdir(parents=True, exist_ok=True)
        ctx = ToolContext(config, role, tool_patterns((role,)), memory=memory, skills=skills)
        specs = {handler.name: handler for handler in REGISTRY.by_name.values() if not handler.hidden}

        wrote = await execute(
            specs, "write", {"path": "private/selftest.txt", "content": "eidesoul-selftest"}, ctx
        )
        read = await execute(specs, "read", {"path": "private/selftest.txt"}, ctx)
        passed = wrote.startswith("ok:") and "eidesoul-selftest" in read
        results.append(_report("write/read", "PASS" if passed else "FAIL", "" if passed else f"{wrote} | {read[:80]}"))

        history.add(
            "selftest",
            ChatMessage(
                id="selftest-1",
                sender_name="Tester",
                sender_id="p1",
                when=datetime.now(),
                text="eidesoul-selftest-archive",
                author="manual_owner",
                visibility=3,
            ),
        )
        found = history.search("selftest", "eidesoul-selftest-archive")
        history.mark_deleted("selftest", "selftest-1", "p2", "Admin")
        removed = history.find("selftest", "selftest-1")
        passed = [item.id for item in found] == ["selftest-1"] and (
            found[0].author == "manual_owner" and found[0].visibility == 3
        ) and removed is not None and removed.deleted is True and removed.deleted_admin is True
        results.append(_report("history", "PASS" if passed else "FAIL", "" if passed else "archive not readable"))

        action_id = ledger.log_action("selftest", "selftest", "send", "eidesoul-selftest-action")
        passed = action_id > 0 and any(
            row["summary"] == "eidesoul-selftest-action" for row in ledger.recent_actions()
        )
        results.append(_report("ledger", "PASS" if passed else "FAIL", "" if passed else "action not recorded"))

        if getattr(memory, "available", False):
            ids = await memory.add(
                "eidesoul-selftest-memory",
                scope="global",
                kind="note",
                source="selftest",
                level=0,
                owner_id="selftest",
                subject_id="selftest",
                confirmed=True,
            )
            hits = await memory.search("eidesoul-selftest-memory", scopes=("global",))
            removed = bool(ids) and await memory.forget(
                ids[0], requester_id="selftest", allow_global=True
            )
            passed = bool(hits) and removed
            results.append(_report("memory", "PASS" if passed else "FAIL", "" if passed else "entry not found"))
        else:
            _report("memory", "SKIP", memory_status(memory))

        from .engine.config import PrivacyConfig
        from .engine.memory.privacy import PrivacyPolicy

        policy = PrivacyPolicy(PrivacyConfig())
        probe = MemoryHit(id="x", text="t", level=3, owner_id="a", subject_id="a", confirmed=True)
        vetoed = MemoryHit(id="y", text="t", level=1, vetoed=True)
        checks = (
            policy.show(probe, is_dm=False) is False,
            policy.show(probe, is_dm=True) is True,
            policy.disclose(probe, requester="a", is_dm=False) == "ask",
            policy.disclose(probe, requester="a", is_dm=True) == "allow",
            policy.disclose(probe, requester="b", is_dm=True) == "deny",
            policy.disclose(vetoed, requester="a", is_dm=True) == "deny",
        )
        results.append(
            _report("privacy", "PASS" if all(checks) else "FAIL", "" if all(checks) else str(checks))
        )

        skills.save("selftest", "Selftest check.", "Selftest steps.\n", overwrite=True)
        passed = "selftest: Selftest check." in skills.index()
        results.append(_report("skill", "PASS" if passed else "FAIL", "" if passed else skills.index()[:120]))

        recipes.save(
            "selftest",
            "recipe",
            "Selftest recipe.",
            steps=[{"tool": "read", "args": {"path": "selftest.txt"}}],
            overwrite=True,
        )
        passed = recipes.get("selftest") is not None
        results.append(_report("recipe", "PASS" if passed else "FAIL", "" if passed else "recipe missing"))

        shell = await execute(specs, "shell", {"command": "echo shell-ok"}, ctx)
        passed = "shell-ok" in shell
        results.append(_report("shell", "PASS" if passed else "FAIL", "" if passed else shell[:120]))

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
            results.append(_report("sandbox", "PASS" if passed else "FAIL", "" if passed else box[:200]))

        search = await execute(specs, "web_search", {"query": "python asyncio", "max_results": 3}, ctx)
        passed = bool(search.strip()) and not search.startswith("error:")
        results.append(_report("web_search", "PASS" if passed else "FAIL", "" if passed else search[:200]))

        llm = LLM(config)
        answer = await llm.run("Reply with: ok", "ping", [], ctx, session="eidesoul-selftest")
        passed = bool(answer.strip()) and answer != t("agent.api_error", config.soul.language)
        results.append(_report("llm", "PASS" if passed else "FAIL", "" if passed else answer[:200]))

        affect.turn_start("selftest", "selftest-1")
        affect.turn_end(
            "selftest",
            "p1",
            "Tester",
            "goblok banget sih kamu",
            "member",
            "p1@s.whatsapp.net",
            Decision(
                addressed="me",
                act="reply",
                mode="daily",
                tone="hurt",
                bubbles=1,
                appraisal=Appraisal("hostility", -0.4, 0.3, 0.1, "dihina"),
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
        results.append(_report("affect", "PASS" if passed else "FAIL", "" if passed else "PAD did not move"))

        style.learn("iyaa aku udah cek, aman kok")
        text = style.profile()
        passed = "How people chat here" in text and "particles:" in text
        results.append(_report("style", "PASS" if passed else "FAIL", "" if passed else text[:120]))

        for name in config.mcp:
            found = [spec.name for spec in hub.specs((role,)) if spec.name.startswith(f"mcp__{name}__")]
            print(f"mcp {name}: {', '.join(found) or '(no tools)'}", flush=True)
    finally:
        await memory.close()
        await hub.stop()
        db.dispose()
        shutil.rmtree(root, ignore_errors=True)

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
    print(f"schema version: {data['schema_version']}; documents: {len(data['documents'])}", flush=True)
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
        raise ConfigError("database URL is empty; set it in config.yaml or EIDESOUL_DATABASE_URL")
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
    docs, db = _doc_store(args)
    try:
        count = docs.import_from_files()
    finally:
        db.dispose()
    print(f"config import: {count} documents from files", flush=True)
    return 0


def _library_dispatch(args: argparse.Namespace) -> int:
    if args.library_command == "resolve":
        return _library_resolve(args)
    build_parser().print_help()
    return 2


def _library_resolve(args: argparse.Namespace) -> int:
    """Resolve `style` from the soul text; the DB document still needs `config import`."""
    from .admin import library

    _, paths = load_host(args.config)
    soul_path = paths.soul_yaml
    try:
        body = yaml.safe_load(soul_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        print(f"eidesoul: soul file not found: {soul_path}", file=sys.stderr, flush=True)
        return 2
    except (OSError, yaml.YAMLError) as exc:
        print(f"eidesoul: soul file is invalid: {exc}", file=sys.stderr, flush=True)
        return 2
    if not args.force and isinstance(body, dict) and body.get("style") is not None:
        print("style already set; use --force to re-resolve", flush=True)
        return 0
    try:
        text, candidates = library.resolve_soul(soul_path, use_embedding=True)
    except ValueError as exc:
        print(f"eidesoul: {exc}", file=sys.stderr, flush=True)
        return 2
    soul_path.write_text(text, encoding="utf-8")
    print("note: soul.yaml is rewritten from its parsed keys; comments are not preserved")
    if args.explain:
        for candidate in candidates:
            print(
                f"{candidate.pack} {candidate.kind} {candidate.score:.3f} {candidate.reason}"
            )
    print("next: eidesoul config import", flush=True)
    return 0


def _init(args: argparse.Namespace) -> int:
    from .admin import init as admin_init

    try:
        written, skipped = admin_init.init(
            args.dir, force=args.force, provider=args.provider, model=args.model
        )
    except ValueError as exc:
        print(f"eidesoul: {exc}", file=sys.stderr, flush=True)
        return 2
    print(
        admin_init.report(args.dir, written, skipped, provider=args.provider, model=args.model),
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eidesoul", description="Eidesoul soul engine")
    parser.add_argument("--config", default=None, help="path to config.yaml (default: ./config.yaml)")
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
    init = sub.add_parser("init", help="scaffold config.yaml, roles.yaml, soul.yaml, and .env")
    init.add_argument("--dir", default=".", help="target directory")
    init.add_argument("--force", action="store_true", help="overwrite existing files")
    init.add_argument("--provider", default="opencode", help="provider name (default: opencode)")
    init.add_argument(
        "--model", default="", help="model id to write; omitted leaves it for the operator"
    )
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
    library = sub.add_parser("library", help="character library operations")
    library_sub = library.add_subparsers(dest="library_command")
    resolve = library_sub.add_parser(
        "resolve", help="write soul.yaml style from the soul's own text"
    )
    resolve.add_argument("--explain", action="store_true", help="print the evidence per pack")
    resolve.add_argument("--force", action="store_true", help="re-resolve an existing style")
    db_backup = database_sub.add_parser("backup", help="pg_dump / copy SQLite")
    db_backup.add_argument("--out", required=True, help="target file")
    db_restore = database_sub.add_parser("restore", help="restore from a backup")
    db_restore.add_argument("file", help="backup file")
    db_restore.add_argument("--yes", action="store_true", help="confirm the restore")
    db_restore.add_argument("--force", action="store_true", help="ignore an active lease")
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
        if args.command == "library":
            return _library_dispatch(args)
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
        print(f"eidesoul: {exc}", file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
