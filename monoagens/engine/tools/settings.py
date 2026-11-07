"""Settings tool: read/change engine/soul/role documents and host config files."""


import contextlib
import difflib
import os
import re
import time
from pathlib import Path

import yaml

from ..access.roles import NAME as ROLE_NAME
from ..config import ConfigError, load_config
from ..db import redact_dsn
from ..guard import ActionRequest, scope_proof
from ..registry import Handler
from ..schema import config_changes
from . import ToolContext, workspace_roots

MAX_CONTENT = 64 * 1024
SECRET = re.compile(r"(api_key|password|secret|token)", re.IGNORECASE)
USERINFO = re.compile(r"(://[^/@\s:]+):([^/@\s]+)@")
FILES = ("engine", "host", "gateway", "soul", "role")
DOC_FILES = ("engine", "soul", "role")
RESERVED_SECTIONS = ("engine", "gateways")
MIRRORS = ("config.yaml", "roles.yaml", "soul.yaml")  # the only files allowed as mirrors
RAW_FILES = ("engine", "host", "gateway")  # a whole-file read is refused for these


def _strip_userinfo(text: str) -> str:
    return USERINFO.sub(r"\1:***@", str(text))


def _redact(value, path: str = ""):
    """ADR-0015 §4.7: a secret key -> `***`, `database.url` -> `redact_dsn`, userinfo stripped."""
    if isinstance(value, dict):
        return {
            key: (
                "***"
                if SECRET.search(str(key)) and str(key) != "api_key_env"
                else _redact(item, f"{path}.{key}" if path else str(key))
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, path) for item in value]
    leaf = str(path or "").split(".")[-1]
    if SECRET.search(leaf) and leaf != "api_key_env":
        return "***"
    text = str(value)
    if str(path or "").endswith("database.url") or str(path or "") == "database.url":
        return redact_dsn(text)
    return _strip_userinfo(text)


# --------------------------------------------------------------- yaml util


def deep_get(body: object, path: str):
    node = body
    for piece in [part for part in str(path or "").split(".") if part]:
        if not isinstance(node, dict) or piece not in node:
            raise KeyError(f"path not found: {path}")
        node = node[piece]
    return node


def deep_set(body: dict, path: str, value) -> None:
    parts = [piece for piece in str(path or "").split(".") if piece]
    if not parts:
        raise ValueError("path is required")
    node = body
    for piece in parts[:-1]:
        child = node.get(piece)
        if not isinstance(child, dict):
            child = {}
            node[piece] = child
        node = child
    node[parts[-1]] = value


def deep_unset(body: dict, path: str) -> bool:
    parts = [piece for piece in str(path or "").split(".") if piece]
    if not parts:
        raise ValueError("path is required")
    node = body
    for piece in parts[:-1]:
        child = node.get(piece)
        if not isinstance(child, dict):
            return False
        node = child
    return node.pop(parts[-1], None) is not None


def dump(body: object) -> str:
    return yaml.safe_dump(body, sort_keys=False, allow_unicode=True)


def _diff(old: str, new: str, label: str) -> str:
    lines = difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        fromfile=f"{label} (current)",
        tofile=f"{label} (new)",
        lineterm="",
    )
    text = "\n".join(lines)
    return text if len(text) <= 4000 else text[:3999] + "…"


def _schema_lines(config) -> list[str]:
    from ..self_knowledge import schema_paths

    return [f"- {line}" for line in schema_paths("engine", config)]


# ------------------------------------------------------------------ state


def _read_config(agent) -> dict:
    path = agent.config.paths.config
    if not path.is_file():
        return {}
    try:
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"config.yaml is invalid: {exc}") from exc
    return body if isinstance(body, dict) else {}


def _write_config(agent, body: dict) -> None:
    path = agent.config.paths.config
    _backup(path)
    _atomic(path, dump(body))


def _read_roles(agent) -> dict:
    path = agent.config.paths.roles
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"roles.yaml is invalid: {exc}") from exc
    body = raw.get("roles") if isinstance(raw, dict) else None
    return body if isinstance(body, dict) else {}


def _write_roles(agent, roles: dict) -> None:
    path = agent.config.paths.roles
    _backup(path)
    _atomic(path, dump({"roles": roles}))
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def _mirror(agent, file: str) -> Path | None:
    paths = agent.config.paths
    if file in {"engine", "gateway"}:
        return paths.config
    if file == "soul":
        return paths.soul_yaml
    if file == "role":
        return paths.roles
    return None


def _current(agent, file: str, role_name: str) -> tuple[str, str]:
    """(kind, name) for the requested file; kind is host, gateway, or document."""
    if file == "role":
        if not ROLE_NAME.fullmatch(role_name or ""):
            raise ValueError("invalid role name")
        return "document", f"role:{role_name}"
    if file in DOC_FILES:
        return "document", file
    if file == "host":
        return "host", "host"
    if file == "gateway":
        return "gateway", "gateway"
    raise ValueError(f"file must be one of: {', '.join(FILES)}")


def _read(agent, file: str, role_name: str) -> tuple[str, str, str]:
    """ADR-0046 §4.1: the file is read; the cache is never the read source."""
    kind, name = _current(agent, file, role_name)
    if kind == "host":
        text = agent.config.paths.config.read_text(encoding="utf-8")
        return kind, name, text
    if kind == "gateway":
        raw = _read_config(agent)
        return kind, name, dump({"gateways": raw.get("gateways") or {}})
    if name == "engine":
        raw = _read_config(agent)
        body = raw.get("engine")
        return kind, name, dump(body) if isinstance(body, dict) else ""
    if name == "soul":
        path = agent.config.paths.soul_yaml
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        return kind, name, text
    roles = _read_roles(agent)
    body = roles.get(name.split(":", 1)[1]) if ":" in name else None
    return kind, name, dump(body) if isinstance(body, dict) else ""


def _validate(agent, file: str, name: str, text: str) -> None:
    if len(text.encode("utf-8")) > MAX_CONTENT:
        raise ValueError(f"content exceeds {MAX_CONTENT} bytes")
    if file in {"host", "gateway"}:
        body = yaml.safe_load(text)
        if not isinstance(body, dict):
            raise ValueError(f"{file} must be a YAML mapping")
        if file == "gateway":
            gateways = body.get("gateways")
            if gateways is not None and not isinstance(gateways, dict):
                raise ValueError("gateway.gateways must be a mapping")
            for gateway_name, value in (gateways or {}).items():
                if value is not None and not isinstance(value, dict):
                    raise ValueError(f"gateway '{gateway_name}' must be a mapping")
        else:
            if not isinstance(body.get("database"), dict) and not body.get("workspace"):
                raise ValueError("host config needs database/workspace")
            for key in RESERVED_SECTIONS:
                if key in body and not isinstance(body.get(key), dict):
                    raise ValueError(f"host section '{key}' must stay a mapping")
        return
    body = yaml.safe_load(text)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise ValueError(f"{name} must be a YAML mapping")
    try:
        if file == "engine":
            loaded = load_config(
                agent.config.paths.config,
                engine_override=body,
                require_key=False,
                require_model=False,
            )
        elif file == "soul":
            loaded = load_config(
                agent.config.paths.config,
                soul_override=body,
                require_key=False,
                require_model=False,
            )
        else:
            roles = dict(_read_roles(agent))
            roles[name.split(":", 1)[1]] = body
            loaded = load_config(
                agent.config.paths.config,
                roles_override=roles,
                require_key=False,
                require_model=False,
            )
    except ConfigError as exc:
        raise ValueError(str(exc)) from exc
    if not loaded.roles.get(loaded.default_role):
        raise ValueError("default_role must still exist")


def _audit(agent, actor: str, file: str, path: str, old: str, new: str) -> None:
    row = {
        "ts": time.time(),
        "actor": str(actor or ""),
        "file": file,
        "path": str(path or ""),
        "old": str(old)[:4000],
        "new": str(new)[:4000],
    }
    with agent.db.connect() as conn:
        conn.execute(config_changes.insert().values(row))


def _backup(path: Path) -> Path | None:
    resolved = path.resolve()
    if resolved.name not in MIRRORS or not resolved.is_file():
        return None
    target = resolved.with_name(f"{resolved.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    target.write_bytes(resolved.read_bytes())  # NOSONAR - target is mirror allow-list name MIRRORS
    with contextlib.suppress(OSError):
        target.chmod(0o600)
    return target


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_gateway_flag(
    agent, gateway_name: str, flag: str, enabled: bool
) -> dict[str, object]:
    """Set one `gateways.<name>.<flag>` value in config.yaml, the operator file.

    The host file stays the source of truth (AGENTS §3); the in-memory settings follow it so
    the next turn reads the switch without a reload. The write is offloaded by the caller.
    """
    name = str(gateway_name or "").strip()
    if not name:
        raise ValueError("a gateway name is required")
    key = str(flag or "").strip()
    if not key or key == "enabled":
        raise ValueError("a gateway flag name is required")
    body = _read_config(agent)
    gateways = body.get("gateways")
    if not isinstance(gateways, dict):
        gateways = {}
    block = gateways.get(name)
    if not isinstance(block, dict):
        block = {}
    block[key] = bool(enabled)
    gateways[name] = block
    body["gateways"] = gateways
    _write_config(agent, body)
    settings = agent.config.gateways.get(name)
    if settings is not None:
        settings.options[key] = bool(enabled)
    return dict(block)


async def _commit(agent, file: str, name: str, text: str, actor: str) -> None:
    """ADR-0046 §4.1: write the operator file first, then refresh its cache row."""
    if file == "host":
        _write_config(agent, yaml.safe_load(text) or {})
        return
    if file == "gateway":
        raw = _read_config(agent)
        body = yaml.safe_load(text) or {}
        raw["gateways"] = body.get("gateways") or {}
        _write_config(agent, raw)
        return
    mirror = _mirror(agent, file)
    if mirror is not None:
        _backup(mirror)
    if file == "engine":
        raw = _read_config(agent)
        raw["engine"] = yaml.safe_load(text) or {}
        _write_config(agent, raw)
    elif file == "soul":
        _atomic(agent.config.paths.soul_yaml, text)
    elif file == "role":
        roles = dict(_read_roles(agent))
        roles[name.split(":", 1)[1]] = yaml.safe_load(text) or {}
        _write_roles(agent, roles)
    else:
        raise ValueError(f"file must be one of: {', '.join(FILES)}")
    if agent.documents is not None:
        agent.documents.put(name, text, actor=actor)


# ------------------------------------------------------------------ actions


def _requires_approval(ctx: ToolContext, action: str) -> bool:
    if action in {"get", "schema", "reload"}:
        return False
    return bool(ctx.config.approval.settings) and not ctx.approved


def _payload(
    file: str, role_name: str, action: str, path: str, value, content: str, actor: str
) -> dict:
    return {
        "file": file,
        "role": role_name,
        "action": action,
        "path": path,
        "value": value,
        "content": content,
        "actor": actor,
    }


def _change(
    agent, file: str, role_name: str, action: str, path: str, value, content: str
) -> tuple[str, str, str]:
    """Compute the new content without writing; return (kind, name, new_text)."""
    kind, name, current = _read(agent, file, role_name)
    if action == "write":
        if not content:
            raise ValueError("content is empty")
        new_text = content
    else:
        if file == "gateway" and path.startswith("gateways."):
            raise ValueError("gateway paths are relative to the gateways mapping")
        if file == "host":
            head = str(path or "").split(".", 1)[0]
            if head in RESERVED_SECTIONS:
                raise ValueError("use file=engine, file=gateway, or file=soul")
        body = yaml.safe_load(current) if current.strip() else {}
        if body is None:
            body = {}
        if not isinstance(body, dict):
            raise ValueError(f"{name} is not a YAML mapping")
        if file == "gateway":
            gateways = body.get("gateways")
            body = gateways if isinstance(gateways, dict) else {}
        if action == "set":
            deep_set(body, path, value)
        elif action == "unset":
            if not deep_unset(body, path):
                raise KeyError(f"path does not exist: {path}")
        else:
            raise ValueError("action must be get, schema, set, unset, write, or reload")
        new_text = dump({"gateways": body}) if file == "gateway" else dump(body)
    if path and SECRET.search(path) and path != "api_key_env":
        raise ValueError("sensitive keys cannot be changed through settings")
    _validate(agent, file, name, new_text)
    return kind, name, new_text


async def _settings(args: dict, ctx: ToolContext) -> str:
    agent = ctx.agent
    if agent is None:
        return "error: settings is only available at agent runtime"
    action = str(args.get("action") or "schema").strip().lower()
    file = str(args.get("file") or "").strip().lower()
    role_name = str(args.get("role") or "").strip()
    path = str(args.get("path") or "").strip()
    actor = ctx.sender or "unknown"

    if action == "schema":
        lines = [f"Files: {', '.join(FILES)}"]
        lines.append("Engine dot-paths:")
        lines.extend(_schema_lines(ctx.config))
        return "\n".join(lines)

    if action == "reload":
        return await agent.reload()

    if action == "get":
        if not path and file in RAW_FILES:
            return (
                "error: a path is required; use schema for the structure and get <path> "
                "for one value (a whole configuration file is never returned)"
            )
        _kind, name, text = _read(agent, file, role_name)
        body = yaml.safe_load(text) if text.strip() else {}
        if path:
            try:
                value = deep_get(body, path)
            except KeyError as exc:
                return f"error: {exc}"
            value = _redact(value, path)
            if isinstance(value, (dict, list)):
                return dump(value).strip()
            return str(value)
        return _strip_userinfo(text or "(empty)")

    if action not in {"set", "unset", "write"}:
        return "error: action must be schema, get, set, unset, write, or reload"
    try:
        _kind, name, new_text = _change(
            agent, file, role_name, action, path, args.get("value"), str(args.get("content") or "")
        )
    except (ValueError, KeyError, ConfigError) as exc:
        return f"error: {exc}"
    _, _, old_text = _read(agent, file, role_name)
    diff = _diff(old_text, new_text, name)
    display = new_text
    if path == "database.url" and new_text:
        display = redact_dsn(new_text)
    if _requires_approval(ctx, action):
        request = ActionRequest(
            tool="settings",
            args={"file": file, "path": path},
            thread_id=ctx.thread_id,
            sender=ctx.sender,
            gateway=getattr(ctx.gateway, "name", "") or "",
            roles=tuple(role.name for role in ctx.roles),
            workspace=str(ctx.config.workspace),
            roots=tuple(
                str(root)
                for root in workspace_roots(ctx.config, ctx.roles, ctx.thread_id)
            ),
        )
        proof = scope_proof(request, extra=ctx.config.guard.credential_patterns)
        approval = await agent.approvals.create(
            kind="settings",
            thread_id=ctx.thread_id,
            sender=ctx.sender,
            gateway=getattr(ctx.gateway, "name", "") or "",
            tool="settings",
            args={"file": file, "path": path},
            payload=_payload(
                file,
                role_name,
                action,
                path,
                args.get("value"),
                str(args.get("content") or ""),
                actor,
            ),
            summary=f"settings {action} {name}{(' ' + path) if path else ''}",
            diff=diff,
            roles=tuple(role.name for role in ctx.roles),
            approval_class=str(proof["class"]),
            scope_proof=proof,
        )
        return (
            f"pending approval #{approval.id}: settings {action} {name}. "
            f"Diff:\n{diff}\nTell the sender to run /approve {approval.id}."
        )
    await _commit(agent, file, name, new_text, actor)
    _audit(agent, actor, file, path or name, old_text, display)
    try:
        summary = await agent.reload()
    except ConfigError as exc:
        return f"ok: saved; {exc}"
    return f"ok: {action} {name} saved. {summary}"


async def apply_payload(agent, payload: dict) -> str:
    """Called by /approve: validate again, then write, mirror, audit, and reload."""
    file = str(payload.get("file") or "")
    action = str(payload.get("action") or "")
    role_name = str(payload.get("role") or "")
    path = str(payload.get("path") or "")
    actor = str(payload.get("actor") or "approval")
    _kind, name, new_text = _change(
        agent,
        file,
        role_name,
        action,
        path,
        payload.get("value"),
        str(payload.get("content") or ""),
    )
    _, _, old_text = _read(agent, file, role_name)
    await _commit(agent, file, name, new_text, actor)
    display = redact_dsn(new_text) if path == "database.url" else new_text
    _audit(agent, actor, file, path or name, old_text, display)
    try:
        summary = await agent.reload()
    except ConfigError as exc:
        return f"saved; {exc}"
    return summary


TOOL = Handler(
    name="settings",
    target="",
    approval_class="authorization",
    family="write",
    description=(
        "Read or change MonoAgens's own config/soul/roles. Actions: schema, get, set, unset, "
        "write, reload. Files: engine, host, gateway, soul, role:<name>. Engine lives in the "
        "`engine` section of config.yaml, the soul is the whole soul.yaml, roles live in "
        "roles.yaml; the DB documents are a derived cache. Host is the whole config.yaml. "
        "Sensitive changes wait for /approve when approval.settings is true."
    ),
    fn=_settings,
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "schema, get, set, unset, write, reload."},
            "file": {"type": "string", "description": "engine, host, gateway, soul, role."},
            "role": {"type": "string", "description": "Role name when file=role."},
            "path": {"type": "string", "description": "Dot-path inside the file."},
            "value": {"description": "New value for set (YAML/JSON)."},
            "content": {"type": "string", "description": "Full content for writing soul/role."},
        },
        "additionalProperties": False,
    },
)
