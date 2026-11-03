"""MCP tool family: the real tools are registered by eidesoul/mcp.py when Hub.start() runs."""


from ..registry import Handler

TOOL = Handler(
    name="mcp",
    family="mcp",
    description="Tools exposed by MCP servers (one family, names mcp__<server>__<tool>).",
    pattern="mcp__*",
    hidden=True,
)
