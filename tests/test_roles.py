"""Role contract: one roles.yaml mapping, multi-role union, closed defaults, memory_admin.

Also ADR-0022: `administrator` is the protected top role (the `owner` name is gone, the loader
rejects it), every role may carry a display-only `label`, and only `roles.yaml` + `config import`
may change the protected role.
"""


import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from support import build_config, database, inbound_message, make_agent

from eidesoul.admin import templates
from eidesoul.engine.access.permissions import (
    command_allowed,
    command_patterns,
    tool_patterns,
    tools_for,
)
from eidesoul.engine.access.roles import (
    LABEL_MAX,
    PROTECTED,
    Role,
    RoleError,
    assert_protected_roles,
    load_roles,
)
from eidesoul.engine.commands import REGISTRY as COMMANDS
from eidesoul.engine.stores.documents import DocumentStore
from eidesoul.engine.tools import REGISTRY as TOOLS

ROOT = Path(__file__).resolve().parents[1]
ADMIN = (
    "name: administrator\nusers: ['628111']\ntools: ['*']\ncommands: ['*']\n"
    "memory_admin: true\n"
)


def test_load_role_mapping_and_union() -> None:
    source = {
        "administrator": ADMIN,
        "panitia": 'name: panitia\npriority: 10\nusers: ["628111"]\ntools: ["read"]\n'
        'commands: ["help"]\ncontext: "Panitia."\n',
        "guest": "name: guest\n",
    }

    registry = load_roles(source, "guest")

    assert registry.names() == ["administrator", "guest", "panitia"]
    roles = registry.for_person("628111")
    assert [role.name for role in roles] == ["panitia", "administrator"]
    assert tool_patterns(roles) == ("read", "*")
    assert command_patterns(roles) == ("help", "*")
    assert registry.for_person("628999")[0].name == "guest"
    # the core compares canonical identities only; a gateway hands in the bare identity
    assert registry.for_person("628111@s.whatsapp.net")[0].name == "guest"


def test_name_must_match_key() -> None:
    with pytest.raises(RoleError, match="does not match"):
        load_roles({"member": "name: other\ntools: []\n"}, "member")


def test_bad_list_field_rejected() -> None:
    with pytest.raises(RoleError, match="tools"):
        load_roles({"member": 'name: member\ntools: "read"\n'}, "member")


def test_unknown_default_role_rejected() -> None:
    with pytest.raises(RoleError, match="default_role"):
        load_roles({"member": "name: member\n"}, "administrator")


def test_role_documents_mapping() -> None:
    docs = {
        "member": 'name: member\ntools: ["read"]\ncommands: ["help"]\n',
        "guest": "name: guest\n",
    }
    registry = load_roles(docs, "guest")
    assert registry.get("member").tools == ("read",)
    assert registry.override(("member",))[0].name == "member"
    with pytest.raises(RoleError):
        registry.override(("hilang",))


def test_memory_admin_parses_true_and_defaults_false() -> None:
    registry = load_roles(
        {
            "lead": "name: lead\nmemory_admin: true\n",
            "guest": "name: guest\n",
        },
        "guest",
    )
    assert registry.get("lead").memory_admin is True
    assert registry.get("guest").memory_admin is False


def test_role_without_defaults_denies_tools() -> None:
    empty = Role("member")
    assert tool_patterns((empty,)) == ()
    assert tools_for(TOOLS, (empty,)) == []
    assert len(TOOLS.by_name) > 0
    assert len(COMMANDS.by_name) > 0


# ------------------------------------------------------------ ADR-0022


def test_a_missing_default_role_is_refused() -> None:
    with pytest.raises(RoleError) as caught:
        load_roles({"guest": "name: guest\n"}, "owner")

    assert "default_role 'owner' is not among the loaded roles" in str(caught.value)


def test_administrator_is_protected_and_needs_the_full_contract() -> None:
    valid = load_roles({"administrator": ADMIN, "guest": "name: guest\n"}, "guest")
    administrator = valid.get("administrator")
    assert administrator.protected is True
    assert administrator.memory_admin is True
    assert command_allowed((administrator,), "approve") is True

    with pytest.raises(RoleError, match="protected"):
        load_roles(
            {
                "administrator": "name: administrator\ntools: ['*']\ncommands: ['*']\n"
                "protected: false\n"
            },
            "administrator",
        )
    with pytest.raises(RoleError, match="tools"):
        load_roles(
            {"administrator": "name: administrator\ntools: ['read']\ncommands: ['*']\n"},
            "administrator",
        )
    with pytest.raises(RoleError, match="approve"):
        load_roles(
            {"administrator": "name: administrator\ntools: ['*']\ncommands: ['help']\n"},
            "administrator",
        )


def test_administrator_boot_gate() -> None:
    no_users = load_roles(
        {"administrator": "name: administrator\ntools: ['*']\ncommands: ['*']\n"}, "administrator"
    )
    with pytest.raises(RoleError, match="users"):
        no_users.require_administrator()

    ready = load_roles({"administrator": ADMIN, "guest": "name: guest\n"}, "guest")
    assert ready.require_administrator().name == PROTECTED

    with pytest.raises(RoleError, match="administrator"):
        load_roles({"member": "name: member\n"}, "member").require_administrator()


def test_label_is_optional_validated_and_display_only() -> None:
    registry = load_roles(
        {
            "administrator": ADMIN.replace("memory_admin", 'label: "Majikan"\nmemory_admin'),
            "member": 'name: member\nlabel: "Dev"\ntools: ["read"]\ncommands: ["help"]\n',
            "guest": "name: guest\n",
        },
        "guest",
    )
    administrator = registry.get("administrator")
    member = registry.get("member")
    assert administrator.label == "Majikan"
    assert member.label == "Dev"
    assert registry.get("guest").label == ""

    # a label is never a permission key: the same role with and without it permits the same tools
    assert tool_patterns((administrator,)) == tool_patterns((replace(administrator, label=""),))
    assert command_patterns((member,)) == command_patterns((replace(member, label=""),))

    with pytest.raises(RoleError, match="label"):
        load_roles({"member": f'name: member\nlabel: "{"x" * (LABEL_MAX + 1)}"\n'}, "member")
    with pytest.raises(RoleError, match="whitespace"):
        load_roles({"member": 'name: member\nlabel: "   "\n'}, "member")


def test_protected_role_cannot_be_deleted_or_renamed() -> None:
    with pytest.raises(RoleError, match="cannot be deleted or renamed"):
        assert_protected_roles({"administrator", "member"}, {"member"})
    with pytest.raises(RoleError, match="cannot be deleted or renamed"):
        assert_protected_roles({"administrator", "member"}, {"admin", "member"})
    assert_protected_roles({"administrator", "member"}, {"administrator", "guest"})


def test_whoami_help_and_guard_agree_on_the_name(tmp_path) -> None:
    administrator = Role(
        "administrator", tools=("*",), commands=("*",), memory_admin=True, protected=True
    )
    member = Role("member", tools=("read",), commands=("help", "ping"))
    env = make_agent(
        tmp_path,
        roles={"administrator": administrator, "member": member},
        default_role="administrator",
    )

    def run(name: str, roles) -> str:
        return asyncio.run(
            env.agent._command(
                name,
                "",
                "cli:local",
                "local",
                roles,
                env.gateway,
                inbound_message(f"/{name}", thread="cli:local"),
            )
        )

    whoami = run("whoami", (administrator,))
    assert "Administrator" in whoami
    assert "Owner" not in whoami

    assert "/approve" in run("help", (administrator,))
    assert "/approve" not in run("help", (member,))

    # the guard's sandbox path asks exactly this question (tools/__init__.py: can_approve)
    assert command_allowed((administrator,), "approve") is True
    assert command_allowed((member,), "approve") is False


def test_shipped_role_surfaces_use_administrator() -> None:
    shipped = yaml.safe_load((ROOT / "roles.yaml").read_text(encoding="utf-8"))["roles"]
    assert "owner" not in shipped
    registry = load_roles(shipped, "guest")
    assert registry.get("administrator").protected is True

    template = templates.ROLES
    assert "owner" not in template
    assert template["administrator"]["tools"] == ["*"]
    assert template["administrator"]["commands"] == ["*"]
    assert template["administrator"]["memory_admin"] is True
    assert template["administrator"]["protected"] is True
    assert templates.GATEWAY["gateways"]["cli"]["role"] == "administrator"


def test_no_owner_role_literal_in_the_role_surfaces() -> None:
    surfaces = (
        ROOT / "roles.yaml",
        ROOT / "eidesoul" / "admin" / "templates.py",
        ROOT / "eidesoul" / "engine" / "access" / "roles.py",
        ROOT / "eidesoul" / "engine" / "access" / "permissions.py",
        ROOT / "eidesoul" / "engine" / "commands" / "whoami.py",
        ROOT / "eidesoul" / "engine" / "commands" / "approve.py",
    )
    found: list[str] = []
    for path in surfaces:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "LEGACY" in line:
                continue
            if '"owner"' in line or "'owner'" in line or line.strip().startswith("owner:"):
                found.append(f"{path.name}:{number}: {line.strip()}")
    assert found == []


# ----------------------------------------------------- config import


def _roles_file(tmp_path, roles: dict) -> DocumentStore:
    config = build_config(tmp_path)
    docs = DocumentStore(database(), config.paths)
    config.paths.roles.write_text(
        yaml.safe_dump({"roles": roles}, sort_keys=False), encoding="utf-8"
    )
    return docs
