"""Background service lifecycle: native daemon, compose stack, logs, and reload.

The CLI never guesses where the process lives. `monoagens start` either spawns
`python -m monoagens run` in its own session with a PID file under the state directory, or
drives the compose stack the deployment was installed with. `monoagens reload` validates the
three host files, refreshes the derived document cache, and leaves a request marker next to the
PID file: the running primary reads that marker explicitly. No `mtime` polling is involved.
"""


import contextlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import make_url

from ..engine.config import (
    Config,
    ConfigError,
    HostConfig,
    Paths,
    load_config,
    load_host,
    resolve_database_url,
)

log = logging.getLogger(__name__)

PID_NAME = "monoagens.pid"
LOG_NAME = "monoagens.log"
RELOAD_NAME = "reload.request"
IPC_NAME = "reload.ipc"
STACK_NAME = "stack"
CONTAINER_NAME = "monoagens"
COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yml")
NATIVE = "native"
COMPOSE = "compose"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
STOP_TIMEOUT = 30.0
START_GRACE = 0.6


@dataclass(frozen=True)
class Service:
    """One deployment: where its files are, how it runs, and what it is called."""

    paths: Paths
    host: HostConfig
    root: Path
    state: Path
    mode: str

    def pid_file(self) -> Path:
        return self.state / "run" / PID_NAME

    def log_file(self) -> Path:
        return self.state / "logs" / LOG_NAME

    def reload_file(self) -> Path:
        return self.state / RELOAD_NAME


def state_dir(paths: Paths) -> Path:
    """The host state directory, always beside the config that names this deployment."""
    return Path(paths.config).parent / ".monoagens"


def compose_file(root: Path) -> Path | None:
    for name in COMPOSE_FILES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def container_engine() -> str | None:
    """The compose driver binary, when the host has one."""
    for name in ("podman", "docker"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _stack_marker(state: Path) -> str:
    try:
        return (state / STACK_NAME).read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""


def detect_mode(root: Path, state: Path, host: HostConfig) -> str:
    """`compose` only when the deployment says so; a bare checkout stays native.

    A compose stack names its database host after the compose service, so a URL whose host is
    `postgres` is the second signal. `MONOAGENS_STACK` and a `.monoagens/stack` marker beat
    both, which is how the installer pins the decision.
    """
    declared = os.environ.get("MONOAGENS_STACK", "").strip().lower() or _stack_marker(state)
    if declared in {NATIVE, COMPOSE}:
        return declared
    url = resolve_database_url(host.database)
    if compose_file(root) is not None and url:
        try:
            hostname = make_url(url).host or ""
        except Exception:  # a malformed URL is a doctor problem, not a mode decision
            hostname = ""
        if hostname == CONTAINER_NAME:
            return COMPOSE
    return NATIVE


def load(config_path: str | None = None) -> Service:
    host, paths = load_host(config_path)
    state = state_dir(paths)
    root = paths.config.parent
    return Service(
        paths=paths,
        host=host,
        root=root,
        state=state,
        mode=detect_mode(root, state, host),
    )


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def read_pid(service: Service) -> int | None:
    """The live primary: the PID file first, the reload IPC when it is missing or stale."""
    pid = 0
    try:
        text = service.pid_file().read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    if text:
        with contextlib.suppress(ValueError):
            pid = int(text)
    if pid and _alive(pid):
        return pid
    ready = ipc_ready(service) or {}
    try:
        candidate = int(ready.get("pid") or 0)
    except (TypeError, ValueError):
        return None
    if candidate and candidate != pid and _alive(candidate) and _engine_process(candidate):
        return candidate
    return None


def write_pid(path: Path, pid: int) -> None:
    """Write a PID file whose removal belongs to the process that still holds `pid`."""
    _write_pid(path, pid)


def _write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")


def _run(argv: list[str], cwd: Path) -> int:
    """The one container seam: every compose call goes through here, so tests can stub it."""
    completed = subprocess.run(argv, cwd=str(cwd), check=False)
    return int(completed.returncode)


def compose_argv(service: Service) -> list[str] | None:
    engine = container_engine()
    if service.mode != COMPOSE or engine is None:
        return None
    return [engine, "compose"]


def version() -> str:
    from ..engine.plugins import engine_version

    return engine_version()


def lease_holder(service: Service) -> dict | None:
    """Best-effort lease row; a stopped database reports `None`, never an exception."""
    url = resolve_database_url(service.host.database)
    if not url:
        return None
    from ..engine.db import Database
    from .database import lease_active

    try:
        db = Database(url, pool_size=1, pool_timeout=2)
    except Exception:  # a malformed URL is a doctor problem, not a status failure
        return None
    try:
        return lease_active(db)
    except Exception:  # the primary may own a store this CLI cannot read yet
        return None
    finally:
        db.dispose()


def _compose_running(service: Service) -> bool:
    argv = compose_argv(service)
    if argv is None:
        return False
    completed = subprocess.run(
        [*argv, "ps", "-q", CONTAINER_NAME],
        cwd=str(service.root),
        check=False,
        capture_output=True,
        text=True,
    )
    return bool(completed.stdout.strip())


def status(service: Service) -> dict:
    pid = read_pid(service)
    running = pid is not None
    if service.mode == COMPOSE:
        running = _compose_running(service)
    lease = lease_holder(service) if running else None
    return {
        "mode": service.mode,
        "pid": pid,
        "running": running,
        "version": version(),
        "lease": lease,
        "log": str(service.log_file()),
        "reload_pending": service.reload_file().is_file(),
    }


def format_status(data: dict) -> list[str]:
    holder = data.get("lease") or {}
    who = ""
    if holder:
        who = f"{holder.get('host') or '?'} pid {holder.get('pid') or '?'}"
        who += f" generation {holder.get('generation') or '?'}"
    lines = [
        f"mode: {data['mode']}",
        f"running: {'yes' if data['running'] else 'no'}"
        + (f" (pid {data['pid']})" if data.get("pid") else ""),
        f"lease: {who or 'none'}",
        f"version: {data['version']}",
        f"log: {data['log']}",
    ]
    if data.get("reload_pending"):
        lines.append("reload: pending")
    return lines


def _start_native(service: Service) -> int:
    pid = read_pid(service)
    if pid is not None:
        print(f"monoagens: already running (pid {pid})", flush=True)
        return 0
    log_path = service.log_file()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "monoagens",
        "--config",
        str(service.paths.config),
        "run",
    ]
    with open(log_path, "ab") as stream:
        process = subprocess.Popen(
            command,
            cwd=str(service.root),
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=stream,
            start_new_session=True,
            close_fds=True,
        )
    _write_pid(service.pid_file(), process.pid)
    time.sleep(START_GRACE)
    if process.poll() is not None:
        service.pid_file().unlink(missing_ok=True)
        print(
            f"monoagens: the engine exited early (code {process.returncode}); see {log_path}",
            file=sys.stderr,
            flush=True,
        )
        return 1
    print(f"monoagens: started (pid {process.pid}); logs: {log_path}", flush=True)
    return 0


def _compose_profiles(service: Service) -> list[str]:
    from .extensions import sandbox_enabled

    return ["--profile", "sandbox"] if sandbox_enabled(service.host) else []


def start(service: Service) -> int:
    if service.mode == COMPOSE:
        argv = compose_argv(service)
        if argv is None:
            raise ConfigError(
                f"stack is compose but no podman/docker binary was found; "
                f"config: {service.paths.config}"
            )
        code = _run([*argv, *_compose_profiles(service), "up", "-d", CONTAINER_NAME], service.root)
        if code:
            return code
        print(f"monoagens: compose stack up ({service.root})", flush=True)
        return 0
    return _start_native(service)


def _wait_stopped(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return not _alive(pid)


def _stop_native(service: Service) -> int:
    pid = read_pid(service)
    if pid is None:
        service.pid_file().unlink(missing_ok=True)
        print("monoagens: not running", flush=True)
        return 0
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    if not _wait_stopped(pid, STOP_TIMEOUT):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
        _wait_stopped(pid, 5.0)
    service.pid_file().unlink(missing_ok=True)
    print(f"monoagens: stopped (pid {pid})", flush=True)
    return 0


def stop(service: Service) -> int:
    if service.mode == COMPOSE:
        argv = compose_argv(service)
        if argv is None:
            raise ConfigError("stack is compose but no podman/docker binary was found")
        return _run([*argv, "stop", CONTAINER_NAME], service.root)
    return _stop_native(service)


def restart(service: Service) -> int:
    code = stop(service)
    if code:
        return code
    return start(service)


def level_from_args(args) -> str:
    """One verbosity contract for `run` and `logs`; `--level` wins over the shorthands."""
    explicit = str(getattr(args, "level", "") or "").strip().upper()
    if explicit:
        if explicit not in LOG_LEVELS:
            raise ConfigError(
                f"unknown log level '{explicit}'; choose one of {', '.join(LOG_LEVELS)}"
            )
        return explicit
    if getattr(args, "debug", False):
        return "DEBUG"
    if getattr(args, "verbose", False):
        return "INFO"
    return "INFO"


def _line_level(line: str) -> str | None:
    """The level token of one log line; text and json formats both carry a bare level."""
    for token in line.split():
        clean = token.strip("[]{}\",:'")
        if clean in LOG_LEVELS:
            return clean
    return None


def filter_lines(lines, minimum: str) -> list[str]:
    """Keep lines at or above `minimum`; a line without a level is always kept."""
    floor = LOG_LEVELS.index(minimum) if minimum in LOG_LEVELS else 0
    kept: list[str] = []
    for line in lines:
        found = _line_level(line)
        if found is None or LOG_LEVELS.index(found) >= floor:
            kept.append(line)
    return kept


def _tail(path: Path, minimum: str) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"monoagens: no log file yet at {path} ({exc.strerror})", file=sys.stderr)
        return 0
    for line in filter_lines(text.splitlines(), minimum):
        print(line, flush=True)
    return 0


def _follow(path: Path, minimum: str) -> int:
    _tail(path, minimum)
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            stream.seek(0, os.SEEK_END)
            while True:
                chunk = stream.readline()
                if not chunk:
                    time.sleep(0.5)
                    continue
                for line in filter_lines([chunk.rstrip("\n")], minimum):
                    print(line, flush=True)
    except KeyboardInterrupt:
        return 0
    except OSError as exc:
        print(f"monoagens: cannot follow {path}: {exc}", file=sys.stderr)
        return 1


def logs(
    service: Service, *, follow: bool = False, minimum: str = "INFO", level: str | None = None
) -> int:
    """Print or stream the service log; `level` is accepted as a synonym for `minimum`."""
    minimum = (level or minimum).upper()
    if service.mode == COMPOSE:
        argv = compose_argv(service)
        if argv is None:
            raise ConfigError("stack is compose but no podman/docker binary was found")
        command = [*argv, "logs", "--no-color"]
        if follow:
            command.append("-f")
        command.append(CONTAINER_NAME)
        if follow:
            return _run(command, service.root)
        completed = subprocess.run(
            command, cwd=str(service.root), check=False, capture_output=True, text=True
        )
        for line in filter_lines(completed.stdout.splitlines(), minimum):
            print(line, flush=True)
        if completed.stderr.strip():
            print(completed.stderr.strip(), file=sys.stderr, flush=True)
        return int(completed.returncode)
    path = service.log_file()
    return _follow(path, minimum) if follow else _tail(path, minimum)


def refresh_cache(service: Service) -> int:
    """Refresh the derived document cache; an unreachable store is reported, never fatal."""
    url = resolve_database_url(service.host.database)
    if not url:
        return 0
    from ..engine.db import Database
    from ..engine.stores.documents import DocumentStore

    db = Database(url, pool_size=1, pool_timeout=2)
    try:
        return DocumentStore(db, service.paths, sync=True).import_from_files()
    except Exception as exc:  # a reload is still requested when the cache cannot be refreshed
        log.warning("document cache was not refreshed: %s", exc)
        return 0
    finally:
        db.dispose()


def validate(service: Service) -> Config:
    """Load all three host files through the real loader; a broken file names itself."""
    return load_config(service.paths.config, require_key=False, require_model=False)


def request_reload(service: Service, *, reason: str = "cli", refresh: bool = True) -> Path:
    """Validate, refresh the cache, and leave the explicit reload marker for the primary."""
    config = validate(service)
    if refresh:
        changes = refresh_cache(service)
        if changes:
            print(f"reload: refreshed {changes} cached document(s)", flush=True)
    payload = {
        "at": time.time(),
        "pid": read_pid(service),
        "reason": str(reason),
        "config": str(service.paths.config),
        "roles": str(service.paths.roles),
        "soul": str(service.paths.soul_yaml),
        "gateways": [name for name, item in config.gateways.items() if item.enabled],
    }
    target = service.reload_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp")
    temp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, target)
    return target


def ipc_ready(service: Service) -> dict | None:
    """The running engine's reload handshake; written once its SIGUSR1 handler is installed."""
    try:
        body = json.loads((service.state / IPC_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) and body.get("protocol") == 1 else None


def _engine_process(pid: int) -> bool:
    """Do not signal a PID that was reused by something else after a crash."""
    try:
        return b"monoagens" in Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False


def _signal_primary(service: Service, pid: int | None) -> bool:
    """SIGUSR1 is the live-reload IPC; the marker is the durable record of the request."""
    number = getattr(signal, "SIGUSR1", None)
    ready = ipc_ready(service)
    if number is None or ready is None:
        return False
    if service.mode == COMPOSE:
        argv = compose_argv(service)
        if argv is None:
            return False
        return _run([*argv, "kill", "--signal", "SIGUSR1", CONTAINER_NAME], service.root) == 0
    if pid is None or int(ready.get("pid") or 0) != pid or not _engine_process(pid):
        return False
    try:
        os.kill(pid, number)
    except OSError:
        return False
    return True


def reload(config_path: str | None = None, *, reason: str = "manual") -> int:
    service = load(config_path)
    try:
        marker = request_reload(service, reason=reason)
    except ConfigError as exc:
        print(f"reload: {exc}", file=sys.stderr, flush=True)
        return 2
    pid = read_pid(service)
    signalled = _signal_primary(service, pid)
    where = f"primary pid {pid}" if pid else "no running primary"
    mode = "signalled" if signalled else "marker only"
    print(f"reload: requested ({where}, {mode}); marker: {marker}", flush=True)
    return 0


def notify(config_path: str | None, *, reason: str) -> None:
    """Called after a CLI mutation; a broken sibling file warns without undoing the edit."""
    try:
        service = load(config_path)
        request_reload(service, reason=reason, refresh=True)
    except ConfigError as exc:
        print(f"reload: requested after the change, but the host files are invalid: {exc}",
              file=sys.stderr, flush=True)
        return
    except Exception as exc:  # a reload never turns a completed mutation into a failure
        log.warning("reload request failed: %s", exc)
        return
    _signal_primary(service, read_pid(service))
    print("reload: requested", flush=True)
