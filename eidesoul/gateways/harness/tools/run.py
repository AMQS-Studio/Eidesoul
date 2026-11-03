"""Tool run: execute a shell command for the harness body through the same guard as `shell`."""

from __future__ import annotations

from ....engine.registry import Handler
from ....engine.tools import ToolContext


async def _run(args: dict, ctx: ToolContext) -> str:
    from ....engine.tools import REGISTRY

    spec = REGISTRY.get("shell")
    if spec is None or spec.fn is None:  # the core primitive was compiled out
        return "error: the shell primitive is unavailable"
    return await spec.fn(args, ctx)


TOOL = Handler(
    name="run",
    family="shell",
    description="Run a shell command on the host in the workspace directory.",
    fn=_run,
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
