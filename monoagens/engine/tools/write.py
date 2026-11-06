"""Tool write: write a text file in the workspace."""


from .. import io
from ..registry import Handler
from . import ToolContext, _resolve_write


async def _write(args: dict, ctx: ToolContext) -> str:
    path = _resolve_write(ctx, args.get("path", ""))
    content = str(args.get("content", ""))
    await io.run(path.parent.mkdir, parents=True, exist_ok=True)
    await io.run(path.write_text, content, encoding="utf-8")
    # Report the workspace-relative path the model can pass back to `read`/`send_file`.
    workspace = await io.run(ctx.config.workspace.resolve)
    try:
        shown = path.relative_to(workspace)
    except ValueError:
        shown = path
    return f"ok: wrote {len(content)} characters to {shown}"


TOOL = Handler(
    name="write",
    target="file",
    path_arg="path",
    approval_class="consent",
    scan=("path",),
    family="write",
    description=(
        "Write text under scratch/; public/ is read-only; trusted roles may write other "
        "workspace paths."
    ),
    fn=_write,
    aliases=("save",),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path inside the workspace (relative or absolute).",
            },
            "content": {"type": "string", "description": "Full file content."},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
)
