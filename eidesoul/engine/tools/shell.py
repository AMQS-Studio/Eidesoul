"""Tool shell: run a shell command on the host in the workspace directory."""

from __future__ import annotations

import asyncio
import os

from ..config import ShellConfig
from ..registry import Handler
from . import ToolContext, _int


def child_env(config: ShellConfig) -> dict[str, str]:
    """ADR-0024 §4.6: a computed environment; the process one (keys, DSN) never travels."""
    return {name: os.environ[name] for name in config.env if name in os.environ}


async def _shell(args: dict, ctx: ToolContext) -> str:
    command = str(args.get("command", ""))
    timeout = max(1, _int(args, "timeout", 60))
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(ctx.config.workspace),
        env=child_env(ctx.config.shell),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return f"error: timeout after {timeout} seconds"
    text = out.decode("utf-8", errors="replace").strip()
    return f"exit={proc.returncode}\n{text}".strip()


TOOL = Handler(
    name="shell",
    family="shell",
    description=(
        "Run a shell command on the host in the workspace directory. The child sees only "
        "the configured environment names; secrets and the database URL are not passed."
    ),
    fn=_shell,
    aliases=("bash", "exec"),
    max_output_chars=8_000,
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command."},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 60)."},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
)
