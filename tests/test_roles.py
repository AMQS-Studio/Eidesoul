"""Role contract: one roles.yaml mapping, multi-role union, closed defaults, memory_admin.

Also ADR-0006: `administrator` is the protected top role (the `owner` name is gone, the loader
rejects it), every role may carry a display-only `label`, and only `roles.yaml` + `config import`
may change the protected role.
"""


import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from support import build_config, database, inbound_message, make_agent

from monoagens.admin import templates
from monoagens.engine.access.permissions import (
    command_allowed,
    command_patterns,
    tool_allowed,
    tool_patterns,
    tools_for,
)
from monoagens.engine.access.roles import (
    LABEL_MAX,
    PROTECTED,
    Role,
    RoleError,
    assert_protected_roles,
    load_roles,
)
from monoagens.engine.commands import REGISTRY as COMMANDS
from monoagens.engine.stores.documents import DocumentStore
from monoagens.engine.tools import REGISTRY as TOOLS

ROOT = Path(__file__).resolve().parents[1]
PERSON = "per_aaaaaaaaaa"
OTHER = "per_bbbbbbbbbb"
ADMIN = (
    f"name: administrator\nusers: ['{PERSON}']\ntools: ['*']\ncommands: ['*']\n"
    "memory_admin: true\n"
)


def test_load_role_mapping_and_union() -> None:
    source = {
        "administrator": ADMIN,
        "panitia": f'name: panitia\npriority: 10\nusers: ["{PERSON}"]\ntools: ["read"]\n'
        'commands: ["help"]\ncontext: "Panitia."\n',
        "guest": "name: guest\n",
    }

    registry = load_roles(source, "guest")

    assert registry.names() == ["administrator", "guest", "panitia"]
    roles = registry.for_person(PERSON)
    assert [role.name for role in roles] == ["panitia", "administrator"]
    # declared patterns first, then the children every matched parent implies
    assert tool_patterns(roles) == ("read", "*", "grep", "sed", "fetch", "send_file")
    assert command_patterns(roles) == ("help", "*")
    assert registry.for_person(OTHER)[0].name == "guest"
    # the core compares canonical identities only; a raw address is not a person id
    assert registry.for_person("628111@s.whatsapp.net")[0].name == "guest"


def test_gateway_policy_comes_only_from_the_highest_priority_role() -> None:
    source = {
        "administrator": (
            f"name: administrator\npriority: 100\nusers: ['{PERSON}']\n"
            "tools: ['*']\ncommands: ['*']\n"
            "gateways:\n  whatsapp:\n    dm: true\n    groups: all\n    reply_to_bot: true\n"
        ),
        "member": (
            f"name: member\npriority: 10\nusers: ['{PERSON}']\n"
            "gateways:\n  whatsapp:\n    dm: true\n    groups: mention\n    reply_to_bot: true\n"
        ),
        "guest": "name: guest\n",
    }

    registry = load_roles(source, "guest")

    role, block = registry.policy_for(PERSON, "whatsapp")
    assert role.name == "administrator"
    assert block == {"dm": True, "groups": "all", "reply_to_bot": True}


def test_a_role_without_a_block_falls_back_to_one_that_defines_it() -> None:
    source = {
        "administrator": (
            f"name: administrator\npriority: 100\nusers: ['{PERSON}']\ntools: ['*']\n"
            "commands: ['*']\n"
        ),
        "member": (
            f"name: member\npriority: 10\nusers: ['{PERSON}']\n"
            "gateways:\n  whatsapp:\n    dm: true\n"
        ),
        "guest": "name: guest\n",
    }

    role, block = load_roles(source, "guest").policy_for(PERSON, "whatsapp")

    assert role.name == "member"
    assert block == {"dm": True}


def test_a_denying_block_on_the_highest_role_still_wins() -> None:
    source = {
        "administrator": (
            f"name: administrator\npriority: 100\nusers: ['{PERSON}']\ntools: ['*']\n"
            "commands: ['*']\ngateways:\n  whatsapp:\n    dm: false\n"
        ),
        "member": (
            f"name: member\npriority: 10\nusers: ['{PERSON}']\n"
            "gateways:\n  whatsapp:\n    dm: true\n"
        ),
        "guest": "name: guest\n",
    }

    role, block = load_roles(source, "guest").policy_for(PERSON, "whatsapp")

    assert role.name == "administrator"
    assert block == {"dm": False}


def test_unlinked_sender_uses_the_default_role_policy() -> None:
    source = {
        "guest": (
            "name: guest\n"
            "gateways:\n  whatsapp:\n    dm: false\n    groups: none\n    reply_to_bot: false\n"
        )
    }

    role, block = load_roles(source, "guest").policy_for(OTHER, "whatsapp")

    assert role.name == "guest"
    assert block["dm"] is False


def test_malformed_gateway_map_is_a_load_error() -> None:
    with pytest.raises(RoleError, match="gateways"):
        load_roles({"guest": "name: guest\ngateways: [whatsapp]\n"}, "guest")
    with pytest.raises(RoleError, match=r"gateways\.whatsapp"):
        load_roles({"guest": "name: guest\ngateways:\n  whatsapp: yes\n"}, "guest")


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


# ------------------------------------------------------------ ADR-0006


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
                "console:local",
                "local",
                roles,
                env.gateway,
                inbound_message(f"/{name}", thread="console:local"),
            )
        )

    whoami = run("whoami", (administrator,))
    assert "Administrator" in whoami
    assert "Owner" not in whoami

    assert "/approve" in run("help", (administrator,))
    assert "/approve" not in run("help", (member,))
    assert "fake:console:local" in whoami
    assert "628111@s.whatsapp.net" in whoami

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
    assert all(
        "role" not in body for body in templates.GATEWAY["gateways"].values()
    )


def test_no_owner_role_literal_in_the_role_surfaces() -> None:
    surfaces = (
        ROOT / "roles.yaml",
        ROOT / "monoagens" / "admin" / "templates.py",
        ROOT / "monoagens" / "engine" / "access" / "roles.py",
        ROOT / "monoagens" / "engine" / "access" / "permissions.py",
        ROOT / "monoagens" / "engine" / "commands" / "whoami.py",
        ROOT / "monoagens" / "engine" / "commands" / "approve.py",
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


def test_people_add_bootstraps_an_administrator_and_writes_the_mirror(tmp_path) -> None:
    from monoagens.admin import people as admin_people

    config = build_config(tmp_path)
    db = database()
    docs = DocumentStore(db, config.paths)
    config.paths.roles.parent.mkdir(parents=True, exist_ok=True)
    config.paths.roles.write_text(
        "roles:\n"
        "  administrator:\n"
        "    name: administrator\n"
        "    users: []\n"
        "    tools: ['*']\n"
        "    commands: ['*']\n"
        "    memory_admin: true\n",
        encoding="utf-8",
    )

    person = admin_people.add(db, config.paths, "console", "local", "administrator", name="Boss")

    stored = yaml.safe_load(docs.get("role:administrator"))
    assert stored["users"] == [person]
    mirror = yaml.safe_load(config.paths.roles.read_text(encoding="utf-8"))
    assert mirror["roles"]["administrator"]["users"] == [person]
    assert person.startswith("per_")
    assert docs.names("role:") == ["role:administrator"]


def test_people_add_refuses_a_non_administrator_first_user(tmp_path) -> None:
    from monoagens.admin import people as admin_people

    config = build_config(tmp_path)
    db = database()
    config.paths.roles.parent.mkdir(parents=True, exist_ok=True)
    config.paths.roles.write_text(
        "roles:\n  member:\n    name: member\n    tools: ['read']\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="first user"):
        admin_people.add(db, config.paths, "console", "local", "member")


def test_a_parent_tool_grants_its_child() -> None:
    source = {
        "member": (
            f'name: member\npriority: 10\nusers: ["{PERSON}"]\n'
            'tools: ["read", "write", "web_search", "send_message"]\n'
        ),
        "guest": "name: guest\n",
    }
    roles = load_roles(source, "guest").for_person(PERSON)
    assert tool_patterns(roles) == (
        "read",
        "write",
        "web_search",
        "send_message",
        "grep",
        "sed",
        "fetch",
        "send_file",
    )
    assert {item.name for item in tools_for(TOOLS, roles)} >= {
        "read",
        "grep",
        "write",
        "sed",
        "web_search",
        "fetch",
        "send_message",
        "send_file",
    }


def test_a_child_tool_alone_grants_nothing_else() -> None:
    source = {"guest": "name: guest\ntools: ['grep']\n"}
    roles = load_roles(source, "guest").for_person(OTHER)
    assert tool_patterns(roles) == ("grep",)
    assert tool_allowed(roles, "read") is False


def test_a_listed_room_grants_the_role() -> None:
    source = {
        "administrator": ADMIN,
        "member": (
            "name: member\npriority: 10\ntools: ['read']\n"
            "rooms: ['whatsapp:12036304@g.us', 'telegram:-1001234567890:42']\n"
        ),
        "guest": "name: guest\n",
    }
    registry = load_roles(source, "guest")
    assert registry.for_person(OTHER, room="whatsapp:12036304@g.us")[0].name == "member"
    assert registry.for_person(OTHER, room="telegram:-1001234567890:42")[0].name == "member"
    assert registry.for_person(OTHER, room="whatsapp:999@g.us")[0].name == "guest"
    assert registry.for_person(OTHER)[0].name == "guest"


def test_a_room_member_holds_the_role_from_a_dm() -> None:
    source = {
        "administrator": ADMIN,
        "member": (
            "name: member\npriority: 10\ntools: ['read']\n"
            "rooms: ['whatsapp:12036304@g.us']\n"
        ),
        "guest": "name: guest\n",
    }
    registry = load_roles(source, "guest")
    seen: list[tuple[str, str]] = []

    def participants(gateway: str, thread: str):
        seen.append((gateway, thread))
        return {OTHER} if thread == "12036304@g.us" else None

    roles = registry.for_person(
        OTHER, room="whatsapp:628111@s.whatsapp.net", participants=participants
    )
    assert roles[0].name == "member"
    assert seen == [("whatsapp", "12036304@g.us")]


def test_a_room_entry_needs_a_gateway_and_a_thread() -> None:
    with pytest.raises(RoleError, match="gateway:thread"):
        load_roles({"guest": "name: guest\nrooms: ['12036304@g.us']\n"}, "guest")


def test_the_member_role_ships_every_isolated_tool() -> None:
    shipped = templates.ROLES["member"]["tools"]
    assert shipped == [
        "read",
        "write",
        "web_search",
        "memory",
        "info",
        "thread_history",
        "send_message",
        "task",
        "recipe",
        "relay",
        "sandbox",
    ]
    assert "settings" not in shipped
    assert "shell" not in shipped
    assert all(role["rooms"] == [] for role in templates.ROLES.values())
    root = yaml.safe_load((ROOT / "roles.yaml").read_text(encoding="utf-8"))["roles"]
    assert root["member"]["tools"] == shipped
    assert all(body["rooms"] == [] for body in root.values())


def test_the_console_identity_joins_administrator_once(tmp_path) -> None:
    from monoagens.admin import people as admin_people

    config = build_config(tmp_path)
    db = database()
    config.paths.roles.parent.mkdir(parents=True, exist_ok=True)
    config.paths.roles.write_text(
        "roles:\n"
        "  administrator:\n"
        "    name: administrator\n"
        "    users: []\n"
        "    tools: ['*']\n"
        "    commands: ['*']\n"
        "    memory_admin: true\n"
        "  guest:\n"
        "    name: guest\n",
        encoding="utf-8",
    )

    person = admin_people.ensure_console_administrator(db, config.paths)
    assert person.startswith("per_")
    mirror = yaml.safe_load(config.paths.roles.read_text(encoding="utf-8"))
    assert mirror["roles"]["administrator"]["users"] == [person]
    roles = load_roles(mirror["roles"], "guest")
    assert roles.for_person(person)[0].name == "administrator"

    # the second call is a no-op: same person, no duplicate entry, no rewrite
    before = config.paths.roles.read_text(encoding="utf-8")
    assert admin_people.ensure_console_administrator(db, config.paths) == ""
    assert config.paths.roles.read_text(encoding="utf-8") == before


def test_the_console_identity_skips_a_missing_roles_file(tmp_path) -> None:
    from monoagens.admin import people as admin_people

    config = build_config(tmp_path)
    config.paths.roles.unlink(missing_ok=True)
    assert admin_people.ensure_console_administrator(database(), config.paths) == ""
