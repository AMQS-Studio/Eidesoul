"""pgvector through the memory port: a real nearest neighbour and a real miss (ADR-0029)."""

from __future__ import annotations

import asyncio

import pytest
from support import StubEncoder, build_config, database, stub_vector

from eidesoul.engine import embed
from eidesoul.engine.memory import build_memory
from eidesoul.engine.memory.mem0 import Mem0Backend
from eidesoul.engine.memory.store import MemoryStore


@pytest.fixture(autouse=True)
def mem0_stub_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mem0's embedder factory gets the same deterministic stub the engine uses."""
    from mem0.utils.factory import EmbedderFactory

    monkeypatch.setenv("K", "key")
    monkeypatch.setenv("MEM0_TELEMETRY", "false")
    stub = StubEncoder()
    monkeypatch.setattr(EmbedderFactory, "create", classmethod(lambda cls, *args, **kwargs: stub))


def test_build_memory_keeps_the_semantic_path(tmp_path) -> None:
    """The store is the shipped one, so `build_memory` must attach Mem0 as extractor/index."""
    config = build_config(tmp_path)
    db = database()
    store = asyncio.run(build_memory(config, db=db))
    try:
        assert isinstance(store, MemoryStore)
        assert store.available is True
        assert store.reason == ""
        assert isinstance(store.index, Mem0Backend)
        assert store.index.available is True
    finally:
        asyncio.run(store.close())
        db.dispose()


def test_pgvector_nearest_neighbour_and_miss(tmp_path) -> None:
    config = build_config(tmp_path)
    db = database()
    backend = Mem0Backend(db, config)
    asyncio.run(backend.ready())
    try:
        coffee = asyncio.run(
            backend.add(
                "kopi hitam tanpa gula",
                scope="global",
                kind="fact",
                infer=False,
                level=0,
                confirmed=True,
            )
        )
        meeting = asyncio.run(
            backend.add(
                "jadwal rapat besok pagi",
                scope="global",
                kind="fact",
                infer=False,
                level=0,
                confirmed=True,
            )
        )
        assert coffee and meeting

        hits = asyncio.run(backend.search("kopi hitam", scopes=("global",)))
        assert hits and hits[0].id == coffee[0]
        assert hits[0].text == "kopi hitam tanpa gula"

        # The stub space is lexical: a disjoint query is orthogonal, so nothing passes the
        # store's relevance threshold instead of a nearest-but-wrong row coming back.
        assert (
            embed.cosine(stub_vector("rendang padang"), stub_vector("kopi hitam tanpa gula"))
            == 0.0
        )
        miss = asyncio.run(backend.search("rendang padang", scopes=("global",)))
        assert miss == []

        assert asyncio.run(backend.forget(coffee[0], requester_id="selftest", allow_global=True))
        gone = asyncio.run(backend.search("kopi hitam", scopes=("global",)))
        assert all(hit.id != coffee[0] for hit in gone)
    finally:
        asyncio.run(backend.close())
        db.dispose()
