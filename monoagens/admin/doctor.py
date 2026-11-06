"""Health checks with a repair mode: config, database, migrations, policy, cache, plugins.

`monoagens doctor` reads the deployment exactly as boot would, then names the first failing
layer instead of a stack trace. `--fix` repairs what is mechanical — missing role policy
blocks and a stale document cache — and re-checks; anything a human must decide stays a
failure.
"""


import os

import yaml
from sqlalchemy import text

from ..engine.access.roles import RoleError
from ..engine.config import (
    ConfigError,
    HostConfig,
    Paths,
    load_config,
    load_host,
    resolve_database_url,
)
from ..engine.db import SCHEMA_VERSION, Database
from ..engine.stores.documents import DocumentStore
from . import gateway as admin_gateway
from . import service


def _dump(body: object) -> str:
    return yaml.safe_dump(body, sort_keys=False, allow_unicode=True)


def _config_check(paths: Paths) -> str:
    """Load without the boot gates, then apply the gates doctor is meant to name."""
    config = load_config(paths.config, require_key=False, require_model=False)
    problems: list[str] = []
    model = str(config.llm.model or "").strip()
    if not model:
        problems.append(
            "engine.llm.model is empty; choose one with `monoagens models` and set it with "
            "`monoagens init --model <id>` or in config.yaml"
        )
    key_env = str(config.llm.api_key_env or "").strip()
    if not config.llm.api_key_optional and not os.environ.get(key_env):
        problems.append(f"env {key_env or 'engine.llm.api_key_env'} is not set (put it in .env)")
    try:
        config.roles.require_administrator()
    except RoleError as exc:
        problems.append(str(exc))
    if problems:
        raise ConfigError("; ".join(problems))
    return (
        f"{paths.config.name}, {paths.soul_yaml.name}, and {paths.roles.name} load; "
        f"model {model}; env {key_env} set; administrator present"
    )


def _database(host: HostConfig) -> Database:
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError(
            "database URL is empty; set database.url in config.yaml or "
            f"{host.database.url_env or 'MONOAGENS_DATABASE_URL'}"
        )
    return Database(url, pool_size=1, pool_timeout=2)


def _database_check(host: HostConfig) -> str:
    db = _database(host)
    try:
        db.scalar(text("SELECT 1"))
        return f"reachable ({db.label})"
    finally:
        db.dispose()


def _migrations_check(host: HostConfig) -> str:
    db = _database(host)
    try:
        version = db.scalar(text("SELECT value FROM schema_meta WHERE key = 'version'"))
    finally:
        db.dispose()
    if version is None:
        raise ConfigError("schema_meta has no version row; run `monoagens db upgrade`")
    if str(version) != str(SCHEMA_VERSION):
        raise ConfigError(
            f"schema {version} is behind the code {SCHEMA_VERSION}; run `monoagens db upgrade`"
        )
    return f"at head ({SCHEMA_VERSION})"


def _policy_check(config_path: str | None) -> str:
    missing = admin_gateway.missing_policies(config_path)
    if missing:
        raise ConfigError(
            "missing role policy: "
            + "; ".join(f"roles.{role}.gateways.{name}" for name, role in missing)
        )
    return "every enabled gateway has a policy block for every role"


def _documents_parts(paths: Paths) -> dict[str, str]:
    expected: dict[str, str] = {}
    try:
        config = yaml.safe_load(paths.config.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        config = {}
    engine = config.get("engine") if isinstance(config, dict) else None
    if isinstance(engine, dict):
        expected["engine"] = _dump(engine)
    if paths.soul_yaml.is_file():
        expected["soul"] = paths.soul_yaml.read_text(encoding="utf-8")
    try:
        roles_body = yaml.safe_load(paths.roles.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        roles_body = {}
    roles = roles_body.get("roles") if isinstance(roles_body, dict) else None
    if isinstance(roles, dict):
        for name, body in roles.items():
            expected[f"role:{name}"] = _dump(body)
    return expected


def _documents_check(host: HostConfig, paths: Paths) -> str:
    expected = _documents_parts(paths)
    db = _database(host)
    try:
        store = DocumentStore(db, paths, sync=True)
        actual = {name: store.get(name) for name in store.names()}
    finally:
        db.dispose()
    stale = sorted(name for name, text in expected.items() if actual.get(name) != text)
    extra = sorted(name for name in actual if name not in expected)
    if stale or extra:
        parts = []
        if stale:
            parts.append(f"stale: {', '.join(stale)}")
        if extra:
            parts.append(f"orphaned: {', '.join(extra)}")
        raise ConfigError("document cache " + "; ".join(parts))
    return f"{len(expected)} document(s) match the files"


def _plugins_check(host: HostConfig, paths: Paths) -> str:
    from ..engine import plugins

    settings = plugins.PluginLoader.from_host(host, paths)
    found = plugins.states(settings)
    broken = [item for item in found if item.state == "unavailable"]
    if broken:
        raise ConfigError(
            "plugin(s) unavailable: "
            + "; ".join(f"{item.name}: {item.reason}" for item in broken)
        )
    return f"{len(found)} configured"


def _fix(config_path: str | None, paths: Paths) -> list[str]:
    fixed: list[str] = []
    missing = admin_gateway.missing_policies(config_path)
    for name in sorted({item[0] for item in missing}):
        admin_gateway.materialize(config_path, name)
    if missing:
        fixed.append(f"policy: wrote {len(missing)} missing block(s)")
    host = load_host(str(paths.config))[0]
    url = resolve_database_url(host.database)
    if url:
        db = Database(url, pool_size=1, pool_timeout=2)
        try:
            changed = DocumentStore(db, paths, sync=True).import_from_files()
        finally:
            db.dispose()
        fixed.append(f"documents: refreshed {changed} cache row(s)")
    if fixed:
        try:
            service.request_reload(
                service.load(config_path), reason="doctor --fix", refresh=False
            )
        except ConfigError as exc:
            fixed.append(f"reload: not requested ({exc})")
    return fixed


def _line(outcome: str, label: str, detail: str) -> str:
    return f"{outcome:<4} {label}: {detail}"


def _checks(config_path: str | None, paths: Paths, host: HostConfig) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for label, check in (
        ("config", lambda: _config_check(paths)),
        ("database", lambda: _database_check(host)),
        ("migrations", lambda: _migrations_check(host)),
        ("policy", lambda: _policy_check(config_path)),
        ("documents", lambda: _documents_check(host, paths)),
        ("plugins", lambda: _plugins_check(host, paths)),
    ):
        try:
            results.append((label, check()))
        except ConfigError as exc:
            results.append((label, f"FAIL {exc}"))
        except Exception as exc:  # one broken layer must not hide the others
            results.append((label, f"FAIL {type(exc).__name__}: {exc}"))
    return results


def run(config_path: str | None = None, *, fix: bool = False) -> int:
    try:
        host, paths = load_host(config_path)
    except ConfigError as exc:
        print(_line("FAIL", "config", str(exc)), flush=True)
        return 1
    results = _checks(config_path, paths, host)
    if fix:
        problems = [label for label, detail in results if detail.startswith("FAIL")]
        if problems:
            for line in _fix(config_path, paths):
                print(_line("FIX", "repair", line), flush=True)
            results = _checks(config_path, paths, host)
    failed = [label for label, detail in results if detail.startswith("FAIL")]
    for label, detail in results:
        outcome = "FAIL" if detail.startswith("FAIL") else "ok"
        body = detail[5:] if detail.startswith("FAIL") else detail
        print(_line(outcome, label, body), flush=True)
    if failed:
        print(f"doctor: {len(failed)} problem(s): {', '.join(failed)}", flush=True)
        return 1
    print("doctor: every check passed", flush=True)
    return 0
