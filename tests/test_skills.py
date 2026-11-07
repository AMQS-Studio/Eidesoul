"""Derived-procedure contract (ADR-0001): retrieval, revisions, scope, decay, owner command."""


import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from support import build_config, database, role

from monoagens.engine.commands import CommandContext
from monoagens.engine.commands.skill import COMMAND as SKILLCMD
from monoagens.engine.config import Config
from monoagens.engine.db import Database
from monoagens.engine.schema import skill_files
from monoagens.engine.stores.skills import SkillStore, parse_frontmatter
from monoagens.engine.tools import REGISTRY as TOOLS

BODY = """# Deploy aman

1. Jalankan test.
2. Deploy.
"""

OWNER = (role("owner", memory_admin=True),)
MEMBER = (role("member"),)


def make_config(root: Path) -> Config:
    return build_config(
        root, roles={"owner": role("owner")}, default_role="owner", workspace=root / "workspace"
    )


def add_file(db: Database, skill: str, path: str, content: str) -> None:
    with db.connect() as conn:
        db.upsert(
            conn,
            skill_files,
            [
                {
                    "skill": skill,
                    "path": path,
                    "content": content.encode("utf-8"),
                    "updated_at": 0.0,
                }
            ],
            ["skill", "path"],
        )


def test_parse_frontmatter_and_empty_store(tmp_path: Path) -> None:
    data, body = parse_frontmatter(
        "---\nname: deploy-aman\ndescription: Deploy service.\n---\n\n" + BODY
    )
    assert data["name"] == "deploy-aman"
    assert body.startswith("# Deploy aman")
    assert parse_frontmatter("# tanpa frontmatter")[0] == {}

    store = SkillStore(database())
    assert store.names() == []
    assert store.catalog() == ""


def test_save_view_read_and_revision(tmp_path: Path) -> None:
    db = database()
    store = SkillStore(db)
    store.save("deploy-aman", "Deploy service dengan cek test.", BODY, language="id")
    add_file(db, "deploy-aman", "references/checklist.md", "cek dulu")

    assert "deploy-aman" in store.catalog()
    assert "# Deploy aman" in store.view("deploy-aman")
    assert "cek dulu" in store.read("deploy-aman", "references/checklist.md")
    with pytest.raises(PermissionError):
        store.read("deploy-aman", "../secret.txt")
    with pytest.raises(FileNotFoundError):
        store.read("deploy-aman", "references/missing.md")

    store.save("deploy-aman", "Versi dua.", "# Dua\n", overwrite=True, language="id")
    revisions = store.revisions("deploy-aman")
    assert [row["revision"] for row in revisions] == [2, 1]
    assert "# Deploy aman" in revisions[1]["body"]
    store.restore("deploy-aman", 1, language="id")
    assert "# Deploy aman" in store.get("deploy-aman").body
    assert len(store.revisions("deploy-aman")) == 3


def test_save_duplicate_and_overwrite(tmp_path: Path) -> None:
    store = SkillStore(database())
    assert store.save("deploy", "Deploy aman.", "# Deploy\n", language="id") == "deploy"
    with pytest.raises(FileExistsError):
        store.save("deploy", "Lagi.", "# Lagi\n", language="id")
    store.save("deploy", "Versi dua.", "# Dua\n", overwrite=True, language="id")
    assert "Versi dua." in store.catalog()
    with pytest.raises(ValueError, match="skill name must be lowercase"):
        store.save("../escape", "x", "y", language="id")
    with pytest.raises(ValueError, match="never contains a secret"):
        store.save("secret", "api_key=abc", "# x\n", language="id")


def test_global_needs_t0_or_approval(tmp_path: Path) -> None:
    store = SkillStore(database())
    with pytest.raises(ValueError, match="global"):
        store.save("global-proc", "Shared.", "# Shared\n", scope="global", language="id")
    assert store.save(
        "global-proc", "Shared.", "# Shared\n", scope="global", trust="T0",
    language="id",
    ) == "global-proc"
    with pytest.raises(ValueError, match="global"):
        store.save("other-proc", "Shared.", "# Shared\n", scope="global", trust="T1", language="id")


def test_retrieval_is_scoped_and_updates_uses(tmp_path: Path) -> None:
    store = SkillStore(database())
    store.save(
        "deploy", "Deploy service to production.", "# Steps\n1. Run sandbox.\n",
        scope="thread", stated_by="chat-a",
    language="id",
    )
    store.save("notes", "Write meeting notes.", "# Notes\n1. read the file.\n", language="id")

    inside = store.retrieve(
        "deploy production",
        thread_id="chat-a",
        person_id="628111",
        held_tools=("sandbox", "read"),
        catalog_tools=("sandbox", "read"),
    )
    assert "deploy" in inside
    outside = store.retrieve(
        "deploy production",
        thread_id="chat-b",
        person_id="628111",
        held_tools=("sandbox", "read"),
        catalog_tools=("sandbox", "read"),
    )
    assert "deploy" not in outside
    row = store.get("deploy")
    assert row.uses >= 1
    assert row.last_used is not None

    assert store.retrieve(
        "deploy production",
        thread_id="chat-a",
        held_tools=("read",),
        catalog_tools=("sandbox", "read"),
    ) == ""


def test_retrieval_budget_and_decay(tmp_path: Path) -> None:
    store = SkillStore(database())
    store.save("long", "A long procedure.", "# " + "x" * 500 + "\n", language="id")
    assert store.retrieve("long procedure", max_chars=100) == ""

    store.save("old", "Old procedure.", "# Old\n", language="id")
    with store.db.connect() as conn:
        from sqlalchemy import text as sql

        conn.execute(
            sql("UPDATE skills SET created_at = :ts, last_used = NULL WHERE name = 'old'"),
            {"ts": time.time() - 90 * 86400},
        )
    assert store.decay(days=30) == 1
    assert "old" not in store.names()
    store.restore_archived("old")
    assert "old" in store.names()


def test_pin_and_forget(tmp_path: Path) -> None:
    store = SkillStore(database())
    store.save("deploy", "Deploy aman.", "# Deploy\n", language="id")
    assert store.pin("deploy") == "deploy"
    assert store.get("deploy").trust == "T0"
    assert store.forget("deploy") == "deploy"
    assert store.names() == []
    assert store.revisions("deploy") == []


def test_skill_tool_is_not_in_the_catalog() -> None:
    assert "skill" not in TOOLS.names()


def test_skill_command_owner_surface(tmp_path: Path) -> None:
    db = database()
    store = SkillStore(db)
    store.save("deploy", "Deploy aman.", "# Deploy\n", language="id")
    add_file(db, "deploy", "refs/x.md", "detail")

    class FakeApp:
        skills = store
        soul = SimpleNamespace(language="id")

    def out(args: str, roles=OWNER) -> str:
        ctx = CommandContext(
            agent=FakeApp(),
            gateway=None,
            roles=roles,
            thread_id="chat",
            args=args,
            commands=(),
            tools=(),
        )
        return asyncio.run(SKILLCMD.fn(ctx))

    assert "deploy" in out("")
    assert "# Deploy" in out("deploy")
    assert "detail" in out("read deploy refs/x.md")
    assert out("pin deploy")
    assert store.get("deploy").trust == "T0"
    assert out("forget deploy", roles=MEMBER)
    assert store.get("deploy") is not None
    assert out("forget deploy")
    assert store.get("deploy") is None


def test_app_scope_is_applied_before_the_audience_ceiling(tmp_path: Path) -> None:
    store = SkillStore(database())
    store.save(
        "a-proc", "Deploy on app a.", "# A\n", app="a", scope="global", trust="T0",
        language="id",
    )
    store.save("shared", "Shared deploy.", "# S\n", language="id")

    inside = store.retrieve(
        "deploy", app="a", thread_id="chat", person_id="p",
        catalog_tools=(), held_tools=(),
    )
    outside = store.retrieve(
        "deploy", app="b", thread_id="chat", person_id="p",
        catalog_tools=(), held_tools=(),
    )

    assert "a-proc" in inside
    assert "shared" in inside
    assert "a-proc" not in outside
    assert "shared" in outside


def test_app_combines_with_person_and_thread_scope(tmp_path: Path) -> None:
    store = SkillStore(database())
    store.save(
        "mine", "Only this person in app a.", "# M\n", app="a", scope="person",
        stated_by="per_aaaaaaaaaa", language="id",
    )
    store.save(
        "room", "Only this thread in app a.", "# R\n", app="a", scope="thread",
        stated_by="chat-a", language="id",
    )

    own = store.retrieve(
        "app", app="a", thread_id="chat-a", person_id="per_aaaaaaaaaa",
        catalog_tools=(), held_tools=(),
    )
    other = store.retrieve(
        "app", app="a", thread_id="chat-b", person_id="per_bbbbbbbbbb",
        catalog_tools=(), held_tools=(),
    )

    assert "mine" in own
    assert "room" in own
    assert "mine" not in other
    assert "room" not in other


def test_revise_without_app_keeps_the_namespace(tmp_path: Path) -> None:
    store = SkillStore(database())
    store.save("a-proc", "One.", "# One\n", app="a", language="id")
    store.revise("a-proc", "Two.", "# Two\n", language="id")
    assert store.get("a-proc").app == "a"


def test_the_turn_app_hint_selects_the_skill_corpus(tmp_path: Path) -> None:
    from sqlalchemy import select
    from support import inbound_message, make_agent

    from monoagens.engine.schema import turns

    env = make_agent(tmp_path)
    env.agent.skills.save(
        "a-proc", "Deploy on app a.", "# A\n", app="a", scope="global", trust="T0",
        language="id",
    )
    env.agent.skills.save(
        "b-proc", "Deploy on app b.", "# B\n", app="b", scope="global", trust="T0",
        language="id",
    )

    asyncio.run(env.agent.handle(inbound_message("deploy", app="a"), env.gateway))

    assert "a-proc" in env.llm.system
    assert "b-proc" not in env.llm.system
    row = env.db.fetch_one(select(turns.c.app))
    assert row["app"] == "a"


def test_two_apps_may_share_a_skill_name_without_overwriting(tmp_path: Path) -> None:
    store = SkillStore(database())
    assert store.save(
        "deploy", "Deploy corpus A.", "# A\n", app="a", language="id"
    ) == "deploy"
    assert store.save(
        "deploy", "Deploy corpus B.", "# B\n", app="b", language="id"
    ) == "deploy"

    first = store.get("deploy", app="a")
    second = store.get("deploy", app="b")
    assert first is not None
    assert second is not None
    assert (first.name, first.app, first.description) == ("deploy", "a", "Deploy corpus A.")
    assert (second.name, second.app, second.description) == ("deploy", "b", "Deploy corpus B.")
    assert store.get("deploy") in {first, second}
    assert [row["revision"] for row in store.revisions("deploy", app="a")] == [1]
    assert [row["revision"] for row in store.revisions("deploy", app="b")] == [1]

    store.save("deploy", "Deploy corpus A2.", "# A2\n", app="a", overwrite=True, language="id")
    assert store.get("deploy", app="a").description == "Deploy corpus A2."
    assert store.get("deploy", app="b").description == "Deploy corpus B."

    inside = store.retrieve(
        "deploy", app="b", thread_id="chat", person_id="p",
        catalog_tools=(), held_tools=(),
    )
    assert "# B" in inside
    assert "# A2" not in inside


def test_the_app_hint_is_ignored_without_a_gateway_declaration(tmp_path: Path) -> None:
    from dataclasses import replace

    from monoagens.engine.config import GatewaySettings

    undeclared = replace(
        build_config(tmp_path),
        gateways={"whatsapp": GatewaySettings("whatsapp", True, {})},
    )
    store = SkillStore(database(), undeclared)
    store.save(
        "deploy", "Undeclared.", "# U\n", app="a", gateway="whatsapp", language="id"
    )
    row = store.get("deploy")
    assert row is not None
    assert row.app == ""
    assert store.declared_app("whatsapp", "a") == ""

    declared = replace(
        build_config(tmp_path),
        gateways={"whatsapp": GatewaySettings("whatsapp", True, {"app": "a"})},
    )
    store = SkillStore(database(), declared)
    store.save(
        "deploy", "Declared.", "# D\n", app="a", gateway="whatsapp", language="id"
    )
    scoped = store.get("deploy", app="a")
    assert scoped is not None
    assert scoped.app == "a"
    assert store.declared_app("whatsapp", "b") == ""
