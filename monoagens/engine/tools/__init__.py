"""Tool registry: one module per tool with `TOOL = Handler(...)`; a single execute door."""


import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from ..access.permissions import command_allowed
from ..access.roles import Role
from ..config import Config
from ..guard import CONSENT, ActionRequest, approval_summary, scope_proof
from ..inbound import Address, Directory
from ..jobs.lease import Superseded
from ..registry import Handler, Registry, allows, discover

log = logging.getLogger(__name__)

DEFAULT_OUTPUT = 20_000
MAX_ERROR = 500
TRUNCATED = "\n...[truncated: output exceeds {limit} chars; {hint}]"
DEFAULT_HINT = "next: narrow the query/read range or ask for the rest"
INVALID_ARGS = "error: invalid arguments for {tool}: {detail}"

PUBLIC = "public"
SCRATCH = "scratch"
PRIVATE = "private"
LIST_LIMIT = 20
# A direct address the archive does not know: a phone number, or `name@server`.
DIRECT_ADDRESS = re.compile(r"^(?:[0-9+][0-9 ()+-]{4,}|[^@\s]+@[^@\s]+)$")


class ToolContext:
    def __init__(
        self,
        config: Config,
        roles: tuple[Role, ...],
        allowed: tuple[str, ...],
        *,
        thread_id: str = "",
        sender: str = "",
        request: str = "",
        history: Any = None,
        memory: Any = None,
        ledger: Any = None,
        skills: Any = None,
        recipes: Any = None,
        affect: Any = None,
        agent: Any = None,
        gateway: Any = None,
        approved: bool = False,
        approval_id: str = "",
        person_id: str = "",
        direct: bool = False,
        app: str = "",
        turn_id: str = "",
    ):
        self.config = config
        self.roles = tuple(roles) if isinstance(roles, (tuple, list)) else (roles,)
        self.allowed = allowed
        self.thread_id = thread_id
        self.sender = sender
        self.request = request
        self.history = history
        self.memory = memory
        self.ledger = ledger
        self.skills = skills
        self.recipes = recipes
        self.affect = affect
        self.agent = agent
        self.gateway = gateway
        self.approved = approved
        self.approval_id = approval_id
        # ADR-0006 §4.5: one turn may stage several approvals; they share the batch the store
        # assigned to the first one, so `/approve batch` can decide them together.
        self.approvals: list[str] = []
        self.approval_batch = ""
        self.person_id = person_id or sender
        self.direct = bool(direct)
        self.app = str(app or "")
        self.turn_id = str(turn_id or "")
        self.used: list[str] = []
        self.sent: list[dict] = []
        self.reads: set[str] = set()
        self.cross_reads: list[str] = []
        self.memory_hits: list = []
        self.research_cards: list = []
        self.activated: set[str] = set()

    @property
    def role(self) -> Role | None:
        return self.roles[0] if self.roles else None

    @property
    def trusted(self) -> bool:
        return trusted_roles(self.roles)

    @property
    def roots(self) -> tuple[Path, ...]:
        return workspace_roots(self.config, self.roles, self.thread_id)

    @property
    def scratch(self) -> Path | None:
        return (
            self.config.workspace / SCRATCH / safe_chat(self.thread_id)
            if self.thread_id
            else None
        )

    @property
    def memory_admin(self) -> bool:
        """Global memory (and its deletion) is limited to roles marked memory_admin."""
        return any(role.memory_admin for role in self.roles)

    def role_names(self) -> str:
        return ", ".join(role.name for role in self.roles) or "-"


def _int(args: dict, key: str, default: int) -> int:
    try:
        return int(args.get(key, default))
    except (TypeError, ValueError):
        return default


def safe_chat(thread_id: str) -> str:
    """A scratch directory name that never merges two threads (ADR-0053 §4.1.3).

    The sanitized part is for humans; the digest keeps `app:user_1` and `app_user:1` apart.
    """
    raw = str(thread_id or "")
    value = re.sub(r"[^A-Za-z0-9_.-]", "_", raw).strip("._")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"{(value or 'chat')[:80]}_{digest}"


def trusted_roles(roles) -> bool:
    return any("*" in (role.tools or ()) for role in roles)


def workspace_roots(config: Config, roles, thread_id: str) -> tuple[Path, ...]:
    base = config.workspace.resolve()
    if trusted_roles(roles):
        return (base,)
    found = [base / PUBLIC]
    if thread_id:
        found.append(base / SCRATCH / safe_chat(thread_id))
    return tuple(found)


def resolve_path(config: Config, raw: str, roots: tuple[Path, ...]) -> Path:
    """Resolve a path and reject anything outside the roots the role may touch."""
    text = str(raw or "").strip()
    if not text:
        raise PermissionError("path is empty")
    base = config.workspace.resolve()
    path = Path(text).expanduser()
    path = (path if path.is_absolute() else base / path).resolve()
    if not any(path.is_relative_to(root) for root in roots):
        raise PermissionError(f"path is not allowed for this role: {text}")
    return path


def resolve_for(config: Config, raw: str, roles, thread_id: str) -> Path:
    """Map the model-facing prefix (`public/`, `scratch/`, `private/`) and resolve per role."""
    text = str(raw or "").strip()
    if not text:
        raise PermissionError("path is empty")
    trusted = trusted_roles(roles)
    if (
        not trusted
        and not thread_id
        and not (text == PUBLIC or text.startswith(PUBLIC + "/"))
    ):
        raise PermissionError("scratch needs an active chat")
    if text == PUBLIC or text.startswith(PUBLIC + "/"):
        target = text
    elif text == SCRATCH or text.startswith(SCRATCH + "/"):
        rest = text[len(SCRATCH) :].lstrip("/")
        target = str(Path(SCRATCH) / safe_chat(thread_id) / rest)
    elif text == PRIVATE or text.startswith(PRIVATE + "/") or trusted:
        target = text
    else:
        target = str(Path(SCRATCH) / safe_chat(thread_id) / text)
    return resolve_path(config, target, workspace_roots(config, roles, thread_id))


def _resolve(ctx: ToolContext, raw: str) -> Path:
    return resolve_for(ctx.config, raw, ctx.roles, ctx.thread_id)


def _resolve_write(ctx: ToolContext, raw: str) -> Path:
    path = _resolve(ctx, raw)
    if not ctx.trusted and path.is_relative_to(ctx.config.workspace.resolve() / PUBLIC):
        raise PermissionError("public is read-only for this role")
    return path


def truncate(text: str, limit: int = DEFAULT_OUTPUT, *, hint: str = "") -> str:
    """Cut to the tool's cap with an explicit marker and a continuation hint (ADR-0021 §4.1)."""
    size = max(200, int(limit or DEFAULT_OUTPUT))
    body = str(text)
    if len(body) <= size:
        return body
    marker = TRUNCATED.format(limit=size, hint=hint or DEFAULT_HINT)
    return body[: max(0, size - len(marker))] + marker


# --------------------------------------------------------------- arguments

def _type_ok(value: object, kind: str) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "null":
        return value is None
    return True


def _value_errors(label: str, value: object, schema: dict) -> list[str]:
    kind = str(schema.get("type") or "")
    if kind and not _type_ok(value, kind):
        return [f"{label} must be {kind}"]
    errors: list[str] = []
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{label} must be one of: {', '.join(str(item) for item in enum)}")
    if kind in {"integer", "number"} and isinstance(value, (int, float)):
        numeric = not isinstance(value, bool)
        low, high = schema.get("minimum"), schema.get("maximum")
        if numeric and isinstance(low, (int, float)) and value < low:
            errors.append(f"{label} must be >= {low}")
        if numeric and isinstance(high, (int, float)) and value > high:
            errors.append(f"{label} must be <= {high}")
    if kind == "string" and isinstance(value, str):
        low, high = schema.get("minLength"), schema.get("maxLength")
        if isinstance(low, int) and len(value) < low:
            errors.append(f"{label} must be at least {low} characters")
        if isinstance(high, int) and len(value) > high:
            errors.append(f"{label} must be at most {high} characters")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and not re.search(pattern, value):
            errors.append(f"{label} must match {pattern}")
    if kind == "array" and isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors.extend(_value_errors(f"{label}[{index}]", item, schema["items"]))
    if kind == "object" and isinstance(value, dict):
        errors.extend(_object_errors(value, schema, prefix=label))
    return errors


def _object_errors(args: dict, schema: dict, *, prefix: str = "") -> list[str]:
    declared = schema.get("properties")
    properties = declared if isinstance(declared, dict) else {}
    listed = schema.get("required")
    required = listed if isinstance(listed, list) else []
    errors: list[str] = []
    for field in required:
        if field not in args:
            errors.append(f"{field} is required")
    if not schema.get("additionalProperties", False):
        for key in args:
            if key not in properties:
                errors.append(f"unexpected field {key}")
    for key, value in args.items():
        schema_value = properties.get(key)
        if isinstance(schema_value, dict):
            label = f"{prefix}.{key}" if prefix else key
            errors.extend(_value_errors(label, value, schema_value))
    return errors


def argument_errors(parameters: dict, args: object, limit: int = 5) -> list[str]:
    """ADR-0024 §4.4: deterministic, field-named, bounded; never an exception."""
    if not isinstance(args, dict):
        return ["arguments must be an object"]
    if not isinstance(parameters, dict) or not parameters:
        return []
    errors = _object_errors(args, parameters)
    return errors[: max(1, int(limit or 5))]


def invalid_arguments(tool: str, errors: list[str]) -> str:
    return INVALID_ARGS.format(tool=tool, detail="; ".join(errors))[:MAX_ERROR]


# ---------------------------------------------------------------- rooms

def rooms_of(ctx: ToolContext) -> set[str]:
    """Recorded presence (ADR-0007 §4.5 layer 2); empty when no archive is wired."""
    if ctx.history is None or not (ctx.person_id or ctx.sender):
        return set()
    try:
        return set(ctx.history.rooms_of(ctx.person_id or ctx.sender))
    except Exception:
        log.exception("rooms_of failed for %s", ctx.person_id or ctx.sender)
        return set()


def _count_capability(ctx: ToolContext, counter: str) -> None:
    """Count a body capability the request needed and the body did not have (ADR-0014 §4.5)."""
    runtime = getattr(getattr(ctx, "agent", None), "runtime", None)
    method = getattr(runtime, counter, None) if runtime is not None else None
    if callable(method):
        method()


def body_of(ctx: ToolContext, name: str = "") -> object | None:
    """The body a cross-body tool targets: the named one, else the current body."""
    wanted = str(name or "").strip() or str(getattr(ctx.gateway, "name", "") or "")
    lookup = getattr(getattr(ctx, "agent", None), "body", None)
    if callable(lookup):
        return lookup(wanted)
    return ctx.gateway if wanted == str(getattr(ctx.gateway, "name", "")) else None


def resolve_address(ctx: ToolContext, body, query: str) -> tuple[Address | None, str]:
    """The address a query names for a body, or a refusal text (ADR-0004 §4.1).

    The archive answers first (`Directory`), then a direct form the body normalizes. A direct
    address carries an empty `proof`: the caller must require a `reason` for it, and the door
    evaluates the send as AUTHORIZATION.
    """
    name = str(getattr(body, "name", "") or "")
    raw = str(query or "").strip()
    if not raw:
        return None, "error: chat must be a room name, a person, or an address"
    if isinstance(body, Directory):
        target, candidates = body.resolve_target(raw)
        if target is not None:
            return Address(name, str(target[0]), "gateway"), ""
        if candidates:
            listing = "\n".join(
                f"- {label} ({thread})" if label else f"- {thread}"
                for thread, label in candidates[:LIST_LIMIT]
            )
            return None, f'error: several targets match "{raw}":\n{listing}'
    normalize = getattr(body, "normalize_address", None)
    direct = str(normalize(raw) if callable(normalize) else raw).strip()
    if DIRECT_ADDRESS.fullmatch(direct):
        return Address(name, direct), ""
    return None, f'error: target "{raw}" is not in the archive of {name or "this body"}'


def record_send(
    ctx: ToolContext,
    target: str,
    target_name: str,
    kind: str,
    summary: str,
    *,
    ok: bool = True,
) -> None:
    """ADR-0007 §4.5: the ledger keeps the class of the send, never the message content."""
    if ctx.ledger is None or not ctx.thread_id:
        return
    label = "cross_send" if target != ctx.thread_id and kind in {"text", "file"} else kind
    try:
        ctx.ledger.log_action(
            ctx.thread_id,
            str(target),
            label,
            str(summary) if ok else f"failed — {summary}",
            source_name=(
                ctx.gateway.chat_name(ctx.thread_id) if ctx.gateway is not None else ""
            ),
            target_name=str(target_name or ""),
            request=ctx.request,
        )
    except Exception:
        log.exception("failed to log a send to %s", target)


def cross_refusal(ctx: ToolContext, thread_id: str) -> str:
    """`""` when the requester may read that room, `denied: room` otherwise.

    Gateway facts are authoritative; recorded presence is the fallback, and every unproven
    case is refused (fail closed, ADR-0007 §4.5). The signal carries no content.
    """
    if not thread_id or thread_id == ctx.thread_id:
        return ""
    rooms = ctx.config.memory.rooms
    if not rooms.require_membership:
        return ""
    requester = ctx.person_id or ctx.sender
    gateway = ctx.gateway
    recorded = rooms_of(ctx)
    if rooms.source != "recorded":
        if isinstance(gateway, Directory):
            members = gateway.participants(thread_id)
            if members is not None:
                # The body proved the membership: the read is allowed, no archive fallback.
                return "" if requester and requester in members else "denied: room"
            _count_capability(ctx, "miss_participants")
        elif gateway is not None:
            _count_capability(ctx, "miss_directory")
            return "denied: room"
    if requester and thread_id in recorded:
        known = set(ctx.reads) - {ctx.thread_id}
        if thread_id not in known and len(known) >= int(rooms.max_rooms_per_turn):
            return "denied: room"
        return ""
    return "denied: room"


# ---------------------------------------------------------------- execute

def side_effecting(spec: Handler) -> bool:
    """A tool that can change the world: a write, a send, a file, or a process."""
    return (
        str(spec.approval_class or "authorization") != "none"
        or bool(spec.destructive)
        or str(spec.family or "") == "write"
    )


def lease_refusal(ctx: ToolContext, name: str) -> str:
    """Ask the live lease immediately before a side-effecting tool runs (ADR-0012 §4.3).

    `""` when the call may run; an error string when the generation moved on. A context with no
    lease (tests, fakes, an admin path) is not refused here.
    """
    guard = getattr(getattr(getattr(ctx, "agent", None), "lease", None), "guard", None)
    require = getattr(guard, "require", None)
    if not callable(require):
        return ""
    try:
        require(f"tool:{name}")
    except Superseded as exc:
        log.warning("tool %s refused: %s", name, exc)
        return f"error: refused by the live lease: {exc}"[:MAX_ERROR]
    return ""


def resolve_spec(specs: dict[str, Handler], name: str) -> Handler | None:
    """The canonical handler a name or alias names; `None` for an unknown tool.

    The one alias rule of the door: the tool loop resolves first, so the step cache, the step
    log, and the non-idempotent check all read the canonical `Handler.name`.
    """
    found = specs.get(name)
    if found is not None:
        return found
    return next((item for item in specs.values() if name in item.aliases), None)


async def execute(specs: dict[str, Handler], name: str, args: dict, ctx: ToolContext) -> str:
    """Single entry point: name, permission, arguments, safety/approval, run, cap output."""
    spec = resolve_spec(specs, name)
    if spec is None:
        return f"error: unknown tool: {name}"
    if not allows(ctx.allowed, spec.name):
        return f"error: tool '{name}' is not allowed for role '{ctx.role_names()}'"
    if spec.fn is None:
        return f"error: tool '{name}' has no implementation"
    errors = argument_errors(spec.parameters, args, ctx.config.tools.max_errors)
    if errors:
        return invalid_arguments(spec.name, errors)
    if ctx.agent is not None and not ctx.approved:
        resolved: tuple[str, ...] = ()
        raw_path = args.get(spec.path_arg) if spec.path_arg and isinstance(args, dict) else None
        if isinstance(raw_path, str) and raw_path.strip():
            try:
                resolved = (str(_resolve(ctx, raw_path)),)
            except PermissionError:
                resolved = ()
        request = ActionRequest(
            tool=spec.name,
            args=dict(args),
            thread_id=ctx.thread_id,
            sender=ctx.sender,
            gateway=getattr(ctx.gateway, "name", "") or "",
            roles=tuple(role.name for role in ctx.roles),
            workspace=str(ctx.config.workspace),
            roots=tuple(str(root) for root in ctx.roots),
            paths=resolved,
            target=spec.target,
            declared_class=spec.approval_class,
            destructive=spec.destructive,
            path_arg=spec.path_arg,
            scan=spec.scan,
        )
        decision = ctx.agent.safety.check(request)
        if "command" in spec.scan and decision.verdict == "allow":
            guard = getattr(ctx.agent, "sandbox_guard", None)
            if guard is not None:
                decision = await guard.check(
                    str(args.get("command") or ""),
                    can_approve=command_allowed(ctx.roles, "approve"),
                    thread_id=ctx.thread_id,
                )
        if decision.verdict == "deny":
            return f"error: {decision.reason or 'action denied by safety policy'}"
        if decision.verdict == "pending":
            proof = scope_proof(request, extra=ctx.config.guard.credential_patterns)
            if proof["class"] == CONSENT and not ctx.agent.approvals.consent_allowed(
                ctx.sender
            ):
                return (
                    "error: too many consent approvals in the last hour; "
                    "wait or ask the approver"
                )
            approval = await ctx.agent.approvals.create(
                kind="tool",
                thread_id=ctx.thread_id,
                sender=ctx.sender,
                gateway=getattr(ctx.gateway, "name", "") or "",
                tool=spec.name,
                args=dict(args),
                summary=approval_summary(spec.name, request, proof),
                roles=tuple(role.name for role in ctx.roles),
                approval_class=str(proof["class"]),
                scope_proof=proof,
                batch_id=ctx.approval_batch,
            )
            ctx.approval_id = str(approval.id)
            if approval.batch_id and not ctx.approval_batch:
                ctx.approval_batch = approval.batch_id
            if ctx.approval_id not in ctx.approvals:
                ctx.approvals.append(ctx.approval_id)
            waiting = ", ".join(f"#{item}" for item in ctx.approvals)
            return (
                f"pending approval #{approval.id}: {approval.summary}. "
                f"Tell the sender to run /approve {approval.id}"
                + (f" (staged in this turn: {waiting})" if len(ctx.approvals) > 1 else "")
                + "."
            )
    if side_effecting(spec):
        refusal = lease_refusal(ctx, spec.name)
        if refusal:
            return refusal
    ctx.used.append(spec.name)
    try:
        result = await spec.fn(args, ctx)
    except PermissionError as exc:  # a rejected path needs no traceback
        result = f"error: {exc}"[:MAX_ERROR]
    except Exception as exc:  # no tool may take the process down
        log.exception("tool %s failed", name)
        result = f"error: {type(exc).__name__}: {exc}"[:MAX_ERROR]
    return truncate(str(result), spec.max_output_chars)


REGISTRY = Registry(
    discover(__name__, Path(__file__).parent, "TOOL", "tool"),
    "tool",
)


class ToolIndex:
    """Tools keyed by body: core, plugins, and each enabled body's own (ADR-0032 §4.6).

    The index is rebuilt at boot from `gateway.tools()` and never stored. A name collision
    between two bodies is impossible by construction; a body tool shadows a core name with one
    warning, and a qualified call (`<body>__<tool>`) names another body's handler explicitly.
    """

    def __init__(
        self,
        core: Registry,
        body: dict[str, list[Handler]],
        plugins: Registry | None = None,
    ) -> None:
        self.core = core
        self.plugins = plugins or Registry([], "tool")
        self.body: dict[str, Registry] = {
            str(name): Registry(handlers, f"tool:{name}") for name, handlers in body.items()
        }
        self._registries: dict[str, Registry] = {}
        for name, registry in self.body.items():
            shadowed = sorted(set(registry.by_name) & set(self.core.by_name))
            if shadowed:
                log.warning(
                    "body tool shadows a core tool body=%s names=%s", name, ",".join(shadowed)
                )

    def specs(self, gateway: str) -> dict[str, Handler]:
        """Everything the current body may call: core, plugins, then its own overrides."""
        merged = dict(self.core.by_name)
        merged.update(self.plugins.by_name)
        own = self.body.get(str(gateway))
        if own is not None:
            merged.update(own.by_name)
        return merged

    def registry(self, gateway: str) -> Registry:
        """The registry a caller filters by role; the body wins on a name it also declares."""
        name = str(gateway or "")
        cached = self._registries.get(name)
        if cached is not None:
            return cached
        handlers = list(self.specs(name).values())
        merged = Registry(handlers, "tool") if handlers else Registry([], "tool")
        self._registries[name] = merged
        return merged

    def resolve(self, name: str, gateway: str) -> tuple[str, Handler] | None:
        """`<body>__<tool>` names that body; a bare name is this body, core, then plugins."""
        wanted = str(name or "").strip()
        if not wanted:
            return None
        if "__" in wanted:
            prefix, _, rest = wanted.partition("__")
            own = self.body.get(prefix)
            if own is None:
                return None
            found = own.get(rest)
            return (str(found.name), found) if found is not None else None
        current = self.body.get(str(gateway or ""))
        for registry in (current, self.core, self.plugins):
            if registry is None:
                continue
            found = registry.get(wanted)
            if found is not None:
                return (str(found.name), found)
        return None

    def get(self, name: str, gateway: str = "") -> Handler | None:
        found = self.resolve(name, gateway)
        return found[1] if found is not None else None

    def names(self, gateway: str) -> tuple[str, ...]:
        return tuple(sorted(self.specs(gateway)))

    def other_bodies(self, gateway: str) -> tuple[str, ...]:
        """The other enabled bodies, for the one prompt line (ADR-0025 keeps the prefix stable)."""
        return tuple(sorted(name for name in self.body if name != str(gateway or "")))
