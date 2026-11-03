"""Operator contract: `people adopt` moves legacy addresses onto `per_…` identities."""

from __future__ import annotations

import asyncio
import json

import yaml
from support import build_config, database

from eidesoul.admin import people as admin_people
from eidesoul.engine.config import DatabaseConfig
from eidesoul.engine.identity import IdentityStore, is_person_id
from eidesoul.engine.memory.rekey import rekey_memory
from eidesoul.engine.memory.store import MemoryStore
from eidesoul.engine.schema import people, state, thread_people
from eidesoul.engine.stores.documents import DocumentStore

LEGACY = "6285111242960"
BUCKET = {
    "n": 3,
    "lengths": [10, 20, 30],
    "lower": 2,
    "upper": 0,
    "emoji": 0,
    "question": 0,
    "exclaim": 0,
    "laugh": 0,
    "repeat": 0,
    "particles": {"aja": 2},
    "emojis": {},
}


def seed(tmp_path):
    db = database()
    config = build_config(tmp_path)
    documents = DocumentStore(db, config.paths)
    with db.connect() as conn:
        db.upsert(
            conn,
            people,
            [{"person_id": LEGACY, "name": "Muqsith", "role": "owner", "trust": 0.7}],
            ["person_id"],
        )
        db.upsert(
            conn,
            thread_people,
            [{"thread_id": "chat", "person_id": LEGACY, "interactions": 3, "last_seen": 5.0}],
            ["thread_id", "person_id"],
        )
        db.upsert(
            conn,
            state,
            [
                {
                    "key": "style",
                    "value": json.dumps(
                        {
                            "seeded": True,
                            "global": BUCKET,
                            "threads": {},
                            "people": {LEGACY: BUCKET},
                        }
                    ),
                    "updated_at": 1.0,
                }
            ],
            ["key"],
        )
    documents.put(
        "role:owner",
        yaml.safe_dump({"name": "owner", "users": [LEGACY], "tools": ["*"]}, sort_keys=False),
    )
    return db, config, documents


def test_adopt_moves_every_legacy_address_and_rewrites_the_roles(tmp_path) -> None:
    db, config, documents = seed(tmp_path)
    identity = IdentityStore(db)

    rows = asyncio.run(admin_people.adopt(db, config, documents))

    assert len(rows) == 1
    old, new, gateway, address = rows[0]
    assert (old, gateway, address) == (LEGACY, "whatsapp", LEGACY)
    assert is_person_id(new)

    person = identity.person(new)
    assert person["name"] == "Muqsith"
    assert person["trust"] == 0.7
    assert [(item["gateway"], item["address"]) for item in identity.links(new)] == [
        ("whatsapp", LEGACY)
    ]
    assert [row["person_id"] for row in db.fetch_all(thread_people.select())] == [new]
    assert list(json.loads(db.fetch_one(state.select())["value"])["people"]) == [new]

    stored = yaml.safe_load(documents.get("role:owner"))
    assert stored["users"] == [new]
    mirror = yaml.safe_load(config.paths.roles.read_text(encoding="utf-8"))
    assert mirror["roles"]["owner"]["users"] == [new]
    assert identity.legacy_count() == 0


def test_adopt_is_idempotent(tmp_path) -> None:
    db, config, documents = seed(tmp_path)
    asyncio.run(admin_people.adopt(db, config, documents))

    assert asyncio.run(admin_people.adopt(db, config, documents)) == []
    assert IdentityStore(db).legacy_count() == 0


def test_people_link_creates_a_missing_person_row(tmp_path) -> None:
    db = database()
    person = "per_abcdefghjk"

    assert admin_people.link(db, person, "whatsapp", LEGACY) == "ok"
    assert admin_people.link(db, "per_zzzzzzzzzz", "whatsapp", LEGACY) == "taken"
    assert admin_people.summary(db)[0].startswith(f"{person}  -  -  whatsapp:{LEGACY}")
    assert admin_people.unlink(db, person, "whatsapp") is True
    assert admin_people.summary(db)[0] == f"{person}  -  -"


def test_rekey_moves_person_and_chat_entries(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("K", "key")
    new = "per_abcdefghjk"
    config = build_config(
        tmp_path,
        database_config=DatabaseConfig(url="postgresql+psycopg://u:p@db:5432/eidesoul", url_env=""),
    )
    memory = MemoryStore(database(), config)
    asyncio.run(
        memory.add(
            "suka kopi",
            scope="person",
            person_id=LEGACY,
            thread_id="chat",
            owner_id=LEGACY,
            subject_id=LEGACY,
        )
    )
    asyncio.run(memory.add("tiket konser", scope="thread", thread_id="chat", owner_id=LEGACY))
    asyncio.run(
        memory.add("punya orang lain", scope="person", person_id="per_other000", thread_id="chat")
    )

    moved = asyncio.run(rekey_memory(memory, LEGACY, new))

    assert moved == 2
    assert asyncio.run(memory.list(scope="person", person_id=LEGACY)) == []
    texts = [hit.text for hit in asyncio.run(memory.list(scope="person", person_id=new))]
    assert texts == ["suka kopi"]
    chat = asyncio.run(memory.list(scope="thread", thread_id="chat"))
    assert [hit.text for hit in chat] == ["tiket konser"]
    assert chat[0].owner_id == new
