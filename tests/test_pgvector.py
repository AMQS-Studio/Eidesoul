"""pgvector through the memory port: a real nearest neighbour and a real miss (ADR-0051)."""


import asyncio

from support import build_config, database, stub_vector

from monoagens.engine import embed
from monoagens.engine.memory import build_memory
from monoagens.engine.memory.store import MemoryStore


def test_build_memory_keeps_the_native_store(tmp_path) -> None:
    """The store is the shipped one, so `build_memory` must attach nothing beside Postgres."""
    config = build_config(tmp_path)
    db = database()
    store = asyncio.run(build_memory(config, db=db))
    try:
        assert isinstance(store, MemoryStore)
        assert store.available is True
        assert store.reason == ""
        assert store.label == "postgres"
        assert store.index is None
    finally:
        asyncio.run(store.close())
        db.dispose()


def test_pgvector_nearest_neighbour_and_miss(tmp_path) -> None:
    config = build_config(tmp_path)
    db = database()
    store = MemoryStore(db, config)
    try:
        coffee = asyncio.run(
            store.add(
                "kopi hitam tanpa gula",
                scope="global",
                kind="fact",
                level=0,
                confirmed=True,
            )
        )
        meeting = asyncio.run(
            store.add(
                "jadwal rapat besok pagi",
                scope="global",
                kind="fact",
                level=0,
                confirmed=True,
            )
        )
        assert coffee
        assert meeting

        hits = asyncio.run(store.search("kopi hitam", scopes=("global",)))
        assert hits
        assert hits[0].id == coffee[0]
        assert hits[0].text == "kopi hitam tanpa gula"

        # The stub space is lexical: a disjoint query is orthogonal, so nothing passes the
        # store's relevance threshold instead of a nearest-but-wrong row coming back.
        assert (
            embed.cosine(stub_vector("rendang padang"), stub_vector("kopi hitam tanpa gula"))
            == 0.0
        )
        miss = asyncio.run(store.search("rendang padang", scopes=("global",)))
        assert miss == []

        assert asyncio.run(store.forget(coffee[0], requester_id="selftest", allow_global=True))
        gone = asyncio.run(store.search("kopi hitam", scopes=("global",)))
        assert all(hit.id != coffee[0] for hit in gone)
    finally:
        db.dispose()
