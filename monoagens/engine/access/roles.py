"""Modular roles: one role per YAML block; explicit permissions, no handler defaults.

ADR-0022: the highest role is `administrator`, it is `protected` (the loader forces the flag and
rejects a role named `owner`), and every role may carry an optional `label` — the address text the
soul uses for its holder. The label is display-only: nothing compares it and it is never a key.
"""


import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import yaml

from ..registry import allows
from .identity import is_person_id

NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

PROTECTED = "administrator"
LABEL_MAX = 40


class RoleError(ValueError):
    """A role document breaks the contract."""


@dataclass(frozen=True)
class Role:
    name: str
    description: str = ""
    users: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    context: str = ""
    priority: int = 0
    memory_admin: bool = False
    label: str = ""
    protected: bool = False
    # ADR-0046 §4.2: gateway name -> that gateway's own policy mapping. The core stores the
    # mapping and never interprets a gateway name or a policy value; the gateway validates it.
    gateways: Mapping[str, Mapping[str, object]] = field(default_factory=dict)


@dataclass(frozen=True)
class RoleRegistry:
    by_name: dict[str, Role] = field(default_factory=dict)
    default: str = ""

    def names(self) -> list[str]:
        return sorted(self.by_name)

    def get(self, name: str) -> Role | None:
        return self.by_name.get(str(name or "").strip().lower())

    def default_role(self) -> Role:
        role = self.by_name.get(self.default)
        if role is not None:
            return role
        if not self.by_name:
            raise RoleError("no roles loaded")
        return self.by_name[self.names()[0]]

    def for_person(self, person_id: str) -> tuple[Role, ...]:
        """Every role that lists this person id, ordered by priority desc then name."""
        from .permissions import normalize  # local: permissions imports this module

        who = normalize(str(person_id or ""))
        matched = [
            role
            for role in self.by_name.values()
            if who and any(normalize(user) == who for user in role.users)
        ]
        if not matched:
            return (self.default_role(),)
        return tuple(sorted(matched, key=lambda role: (-role.priority, role.name)))

    def policy_for(
        self, person_id: str, gateway: str
    ) -> tuple[Role, Mapping[str, object] | None]:
        """ADR-0046 §4.3: the highest-priority role is the only policy source.

        The block is returned raw: the core knows no gateway name and no policy value. An absent
        block is `None` and stays a denial; a lower role never grants access.
        """
        role = self.for_person(person_id)[0]
        found = role.gateways.get(str(gateway or ""))
        return role, found

    def require_administrator(self) -> Role:
        """Boot gate (ADR-0022 §4.2): the protected role must exist and stay able to approve."""
        role = self.by_name.get(PROTECTED)
        if role is None:
            raise RoleError(f"no protected role '{PROTECTED}'; add it to roles.yaml")
        if not role.users:
            raise RoleError(
                f"role '{PROTECTED}' has no users; name at least one person in roles.yaml"
            )
        if "*" not in role.tools:
            raise RoleError(f"role '{PROTECTED}' must list tools: ['*']")
        if not allows(role.commands, "approve"):
            raise RoleError(f"role '{PROTECTED}' must allow the approve command")
        return role


def privileged(role: Role) -> bool:
    """ADR-0036 §4.3: one predicate for status, redaction, and diagnostics."""
    return bool(role.protected or "*" in (role.tools or ()))


def assert_protected_roles(before: Iterable[str], after: Iterable[str]) -> None:
    """Refuse any write that deletes or renames the protected role (ADR-0022 §4.2).

    `before` is the current role-name set, `after` the set the write would leave behind. Only
    roles.yaml plus `monoagens config import` (or the host owner) may change the identifier.
    """
    if PROTECTED in set(before) and PROTECTED not in set(after):
        raise RoleError(
            f"the protected role '{PROTECTED}' cannot be deleted or renamed; change it only "
            "through roles.yaml"
        )


def _str_tuple(value: object, field_name: str, source: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RoleError(f"{source}: '{field_name}' must be a list[str]")
    items: list[str] = []
    for item in value:
        if not isinstance(item, (str, int)) or not str(item).strip():
            raise RoleError(f"{source}: '{field_name}' must be a list[str]")
        items.append(str(item))
    return tuple(items)


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "yes", "1", "on"}


def _label(value: object, source: str) -> str:
    """Optional address text: free-form, at most `LABEL_MAX` characters, never blank."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RoleError(f"{source}: 'label' must be a string")
    if value == "":
        return ""
    text = " ".join(value.split())
    if not text:
        raise RoleError(f"{source}: 'label' must not be whitespace-only")
    if len(text) > LABEL_MAX:
        raise RoleError(f"{source}: 'label' is longer than {LABEL_MAX} characters")
    return text


def _users(value: object, source: str) -> tuple[str, ...]:
    users = _str_tuple(value, "users", source)
    for person in users:
        if not is_person_id(person):
            raise RoleError(
                f"{source}: 'users' must contain per_… person ids, not {person!r}"
            )
    return users


def _gateways(value: object, source: str) -> dict[str, dict[str, object]]:
    """ADR-0046 §4.2: gateway name -> opaque mapping; only the shape is the core's business."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise RoleError(f"{source}: 'gateways' must be a mapping")
    found: dict[str, dict[str, object]] = {}
    for name, block in value.items():
        key = str(name or "").strip()
        if not key:
            raise RoleError(f"{source}: 'gateways' keys must be names")
        if block is None:
            block = {}
        if not isinstance(block, Mapping):
            raise RoleError(f"{source}: gateways.{key} must be a mapping")
        found[key] = dict(block)
    return found


def _protected(
    declared: str, body: Mapping, tools: tuple[str, ...], commands: tuple[str, ...], source: str
) -> bool:
    protected = _bool(body.get("protected"))
    if declared != PROTECTED and protected:
        raise RoleError(f"{source}: only '{PROTECTED}' may be protected")
    if declared == PROTECTED:
        if "protected" in body and not protected:
            raise RoleError(
                f"{source}: role '{PROTECTED}' is protected; 'protected: false' is not allowed"
            )
        protected = True
        if "*" not in tools:
            raise RoleError(f"{source}: role '{PROTECTED}' must list tools: ['*']")
        if not allows(commands, "approve"):
            raise RoleError(f"{source}: role '{PROTECTED}' must allow the approve command")
    return protected


def parse_role(name: str, body: object, source: str) -> Role:
    if not isinstance(body, dict):
        raise RoleError(f"{source}: role must be a mapping")
    declared = str(body.get("name") or "").strip()
    if not declared:
        raise RoleError(f"{source}: field 'name' is required")
    if declared != name:
        raise RoleError(f"{source}: name '{declared}' does not match key '{name}'")
    if not NAME.fullmatch(declared):
        raise RoleError(f"{source}: invalid role name: '{declared}'")
    try:
        priority = int(body.get("priority") or 0)
    except (TypeError, ValueError) as exc:
        raise RoleError(f"{source}: 'priority' must be a number") from exc
    tools = _str_tuple(body.get("tools"), "tools", source)
    commands = _str_tuple(body.get("commands"), "commands", source)
    users = _users(body.get("users"), source)
    protected = _protected(declared, body, tools, commands, source)
    return Role(
        name=declared,
        description=str(body.get("description") or "").strip(),
        users=users,
        tools=tools,
        commands=commands,
        context=str(body.get("context") or "").strip(),
        priority=priority,
        memory_admin=_bool(body.get("memory_admin")),
        label=_label(body.get("label"), source),
        protected=protected,
        gateways=_gateways(body.get("gateways"), source),
    )


def load_roles(
    source: Mapping[str, object],
    default_role: str,
) -> RoleRegistry:
    """Load roles from one `name -> body` mapping (a YAML string or an already-parsed dict)."""
    by_name: dict[str, Role] = {}
    for name, value in source.items():
        label = f"role:{name}"
        if isinstance(value, str):
            try:
                body = yaml.safe_load(value)
            except yaml.YAMLError as exc:
                raise RoleError(f"{label}: invalid YAML: {exc}") from exc
        else:
            body = value
        role = parse_role(str(name), body, label)
        by_name[role.name] = role
    default = str(default_role or "").strip()
    if default not in by_name:
        raise RoleError(f"default_role '{default}' is not among the loaded roles")
    return RoleRegistry(by_name=by_name, default=default)
