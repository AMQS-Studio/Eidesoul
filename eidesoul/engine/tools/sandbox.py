"""Tool sandbox: run a command in a Podman/Docker container with the workspace mounted."""


import asyncio
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

from ..config import SandboxConfig
from ..registry import Handler
from . import PUBLIC, SCRATCH, ToolContext, _int, safe_chat

MAX_OUTPUT_BYTES = 64 * 1024
TRUNCATED = "(truncated)\n"
# Variables allowed into the container; everything else (API keys, etc.) is not inherited.
CONTAINER_ENV = ("TZ", "LANG")
# Client variables the runtime CLI itself needs (not container env).
CLIENT_ENV = ("PATH", "HOME", "XDG_RUNTIME_DIR", "TERM")
RUNTIMES = ("podman", "docker")


def resolve_runtime(cfg: SandboxConfig) -> str | None:
    """Use the runtime from config when set, otherwise try podman and then docker."""
    if cfg.runtime:
        return cfg.runtime if shutil.which(cfg.runtime) else None
    for name in RUNTIMES:
        if shutil.which(name):
            return name
    return None


def build_argv(
    runtime: str,
    cfg: SandboxConfig,
    mounts: tuple[tuple[Path, str, bool], ...],
    workdir: str,
    command: str,
) -> list[str]:
    """Build the argv per runtime: Podman-only flags must not reach Docker."""
    podman = Path(runtime).name == "podman"
    args = [runtime, "run", "--rm", "-i"]
    if podman:
        args.append("--userns=keep-id")
    elif Path(runtime).name == "docker":
        # Files under /work must belong to the bot uid, not the root daemon.
        args += ["--user", f"{os.getuid()}:{os.getgid()}"]
    args += [
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(cfg.pids_limit),
        "--memory",
        cfg.memory,
        "--cpus",
        str(cfg.cpus),
        "--network",
        cfg.network,
    ]
    if cfg.proxy:
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            args += ["--env", f"{name}={cfg.proxy}"]
        args += ["--env", "NO_PROXY=127.0.0.1,localhost"]
    for name in dict.fromkeys((*CONTAINER_ENV, *cfg.env)):
        if name in os.environ:
            args += ["--env", f"{name}={os.environ[name]}"]
    for source, target, read_only in mounts:
        if podman:
            spec = f"{source}:{target}:{'ro,' if read_only else ''}Z"
        else:
            spec = f"{source}:{target}" + (":ro" if read_only else "")
        args += ["-v", spec]
    args += ["-w", workdir, cfg.image, "bash", "-lc", command]
    return args


def client_env() -> dict[str, str]:
    """Minimal env for the CLI process; API keys in the bot env are not passed on."""
    return {name: os.environ[name] for name in CLIENT_ENV if name in os.environ}


def _runtime_error(cfg: SandboxConfig) -> str:
    wanted = cfg.runtime or "podman/docker"
    return (
        f"error: sandbox runtime '{wanted}' not found in PATH; "
        "install podman or docker (or set sandbox.runtime in config.yaml)"
    )


def _source_error(source: Path) -> str:
    return f"error: workspace '{source}' is not a directory"


DOCKER_SOCKET_HINT = (
    "error: docker.sock cannot be accessed by the container uid (permission denied).\n"
    "The 'docker' group in the image must use the same gid as the socket on the host. "
    "Rebuild with:\n"
    "  --build-arg DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)\n"
    "Check on the host: stat -c '%g %a' /var/run/docker.sock\n"
    "Make sure the eidesoul user is in that group, then restart the container."
)


def _tail(raw: bytes) -> str:
    if len(raw) <= MAX_OUTPUT_BYTES:
        return raw.decode("utf-8", errors="replace")
    tail = raw[-MAX_OUTPUT_BYTES:].decode("utf-8", errors="replace")
    return TRUNCATED + tail.lstrip("\n")


async def _sandbox(args: dict, ctx: ToolContext) -> str:
    cfg = ctx.config.sandbox
    if not cfg.proxy:
        return "error: sandbox egress proxy is not configured (sandbox.proxy)"
    if cfg.network in {"", "none", "bridge", "host", "default"}:
        return "error: sandbox.network must be a dedicated internal network (not bridge/none/host)"
    parsed = urlparse(cfg.proxy)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "error: sandbox.proxy must be a valid http(s) URL"
    runtime = resolve_runtime(cfg)
    if runtime is None:
        return _runtime_error(cfg)
    source = cfg.host_workspace or ctx.config.workspace
    # host_workspace lives in the host namespace (Docker daemon), so it is not always visible
    # from the bot container. Do not reject here; the daemon reports a wrong path.
    if cfg.host_workspace is None and not source.is_dir():
        return _source_error(source)
    public_host = source / PUBLIC
    scratch_host = source / SCRATCH / safe_chat(ctx.thread_id)
    if cfg.host_workspace is None:
        public_host.mkdir(parents=True, exist_ok=True)
        scratch_host.mkdir(parents=True, exist_ok=True)
    if ctx.trusted:
        mounts = ((source, "/work", False),)
        workdir = "/work"
    else:
        mounts = (
            (public_host, "/work/public", True),
            (scratch_host, "/work/scratch", False),
        )
        workdir = "/work/scratch"
    command = str(args.get("command", ""))
    timeout = max(1, _int(args, "timeout", int(cfg.timeout_seconds)))
    argv = build_argv(runtime, cfg, mounts, workdir, command)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=client_env(),
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return f"error: timeout after {timeout} seconds"
    if proc.returncode != 0:
        low = out.lower()
        if b"permission denied" in low and b"docker.sock" in low:
            return DOCKER_SOCKET_HINT
    return f"exit={proc.returncode}\n{_tail(out).strip()}".strip()


TOOL = Handler(
    name="sandbox",
    family="sandbox",
    description=(
        "Run a command with /work/public (read-only) and /work/scratch (writable); trusted "
        "roles get /work. Egress only through the configured proxy. Every command passes the "
        "Eidesoul sandbox guard; suspicious commands need an approver role."
    ),
    fn=_sandbox,
    aliases=("container",),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command inside the container."},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default from config)."},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
)
