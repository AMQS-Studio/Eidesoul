"""Google Workspace through one service account, identity per sender.

A plugin tool (ADR-0028): the engine loads this module only when the `google` plugin is
enabled and its manifest digest verifies. The plugin service stays lazy so a host without the
plugin dependencies keeps the module importable and inert instead of failing at import time.
"""


import logging
import weakref

from eidesoul.engine.registry import Handler
from eidesoul.engine.tools import ToolContext

log = logging.getLogger(__name__)

MISSING = "Google Workspace is not available in this build."
_SERVICES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _module():
    """The vendored Google service, or None when this build does not carry it."""
    try:
        from ..google_workspace import service
    except ImportError:
        return None
    return service


def _service(ctx: ToolContext):
    """Cached per-agent service; raises when there is no agent to attach it to."""
    vendor = _module()
    if vendor is None:
        raise RuntimeError(MISSING)
    agent = ctx.agent
    if agent is not None:
        cached = _SERVICES.get(agent)
        if cached is not None:
            return cached
        service = vendor.GoogleWorkspace(agent.db)
        _SERVICES[agent] = service
        return service
    raise vendor.GoogleError("database is not available for Google Workspace")


def _record(ctx: ToolContext, action: str, file_id: str) -> None:
    if ctx.ledger is None:
        return
    try:
        target_name = ctx.gateway.chat_name(ctx.thread_id) if ctx.gateway else ""
        ctx.ledger.log_action(
            ctx.thread_id,
            ctx.thread_id,
            "google",
            f"{action} {file_id or ''}".strip(),
            source_name="chat",
            target_name=target_name,
            request=ctx.request,
        )
    except Exception:
        log.exception("failed to log the google action chat=%s", ctx.thread_id)


async def _google(args: dict, ctx: ToolContext) -> str:
    vendor = _module()
    if vendor is None:
        return f"error: {MISSING}"
    return await _dispatch(args, ctx, vendor)


async def _dispatch(args: dict, ctx: ToolContext, vendor) -> str:
    action = str(args.get("action") or "").strip().lower()
    person_id = str(ctx.person_id or ctx.sender or "").strip()
    if not person_id:
        return "error: no sender identity"
    file_id = str(args.get("file_id") or "").strip()
    _record(ctx, action, file_id)
    try:
        service = _service(ctx)
        if action == "login":
            return await service.login(person_id, direct=ctx.direct)
        if action == "status":
            return await service.status(person_id, direct=ctx.direct)
        if action not in {"list", "read", "write"}:
            return "error: unknown action; use login, status, list, read, or write"
        identity = service.identity(person_id)
        if identity is None:
            return "error: Google account not connected; run action=login in a DM"
        email = str(identity["email"])
        if action == "list":
            try:
                limit = int(args.get("limit") or 10)
            except (TypeError, ValueError):
                limit = 10
            return await service.list_files(
                email, ctx.trusted, str(args.get("query") or ""), limit
            )
        if not file_id:
            return "error: file_id is required"
        if action == "read":
            return await service.read(
                file_id, email, ctx.trusted, str(args.get("range") or "")
            )
        return await service.write(
            file_id,
            email,
            ctx.trusted,
            str(args.get("range") or ""),
            args.get("values") or [],
        )
    except vendor.GoogleError as exc:
        return f"error: {exc}"
    except (OSError, ValueError) as exc:
        return f"error: {type(exc).__name__}: {exc}"


TOOL = Handler(
    name="google",
    family="mcp",
    description=(
        "Google Workspace through Eidesoul's one service account; identity-linked per sender."
    ),
    fn=_google,
    aliases=("gworkspace",),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["login", "status", "list", "read", "write"],
                "description": (
                    "login/status link a Google identity in a DM; list/read/write use it."
                ),
            },
            "file_id": {"type": "string", "description": "Drive file ID."},
            "range": {
                "type": "string",
                "description": "A1 range for Sheets (read default A1:Z1000; required for write).",
            },
            "values": {
                "type": "array",
                "items": {"type": "array", "items": {"type": "string"}},
                "description": "Rows to write to a Sheet.",
            },
            "query": {"type": "string", "description": "Drive name filter."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
)
