"""Derived-procedure contract (ADR-0020): retrieval, revisions, scope, decay, owner command."""


import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from support import build_config, database, role

from eidesoul.engine.commands import CommandContext
from eidesoul.engine.commands.skill import COMMAND as SKILLCMD
from eidesoul.engine.config import Config
from eidesoul.engine.db import Database
from eidesoul.engine.schema import skill_files
from eidesoul.engine.stores.skills import SkillStore, parse_frontmatter
from eidesoul.engine.tools import REGISTRY as TOOLS

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
