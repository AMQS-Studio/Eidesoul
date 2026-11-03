"""ADR-0001/0002 §7 recall and precision over the canonical lexical path.

The retrieval measured here is `MemoryStore._rows`: ILIKE over the `memories_readable` view with
scope isolation and veto/dispute exclusion. It is *not* a Mem0 semantic benchmark; a semantic
eval belongs beside the extractor, not over canonical search.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from support import build_config, database

from eidesoul.engine.memory.integrity import WriteQuota
from eidesoul.engine.memory.store import MemoryStore

DATA = Path(__file__).resolve().parent / "data" / "memory_eval.json"
RECALL_FLOOR = 0.7
PRECISION_FLOOR = 0.9
DUPLICATE_CEILING = 0.05


class FakeIndex:
    available = True
    backend = "fake"

    def __init__(self) -> None:
        self.added: list[str] = []

    async def add(self, text: str, **kwargs) -> list[str]:
        self.added.append(text)
        return [f"ext-{len(self.added)}"]

    async def delete(self, external_id: str) -> bool:
        return True

    async def purge_library(self, external_id: str = "", text: str = "") -> int:
        return 0


def fixture() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def seed(store: MemoryStore, case: dict) -> dict[str, str]:
    """Write one case's facts; returns fixture id -> canonical id."""
    found: dict[str, str] = {}
    for fact in case["facts"]:
        scope = str(fact["scope"])
        found[fact["id"]] = store.write(
            fact["text"],
            scope=scope,
            kind=str(fact["kind"]),
            level=int(fact["level"]),
            person_id=str(fact.get("person") or case["person_id"]),
            thread_id=str(fact.get("thread") or case["thread_id"]),
            audience=str(fact.get("audience") or case["audience"]),
            owner_id=str(fact.get("owner") or case["person_id"]),
            subject_id=str(fact.get("subject") or fact.get("owner") or case["person_id"]),
            pinned=bool(fact.get("pinned")),
            state="ok",
        )
        if fact.get("veto"):
            asyncio.run(store.veto(found[fact["id"]], subject_id=str(fact["veto"])))
    return found


def report(store: MemoryStore, body: dict) -> dict:
    hits_total = 0
    expected_total = 0
    found_total = 0
    canonical_ids: set[str] = set()
    texts: list[str] = []
    for case in body["cases"]:
        mapping = seed(store, case)
        canonical_ids.update(mapping.values())
        reverse = {value: key for key, value in mapping.items()}
        texts.extend(fact["text"] for fact in case["facts"])
        for query in case["queries"]:
            hits = asyncio.run(
                store.search(
                    query["q"],
                    person_id=str(case["person_id"]),
                    thread_id=str(case["thread_id"]),
                    limit=int(query.get("limit") or 12),
                )
            )
            # An id that is not part of this query's case is still a returned row: it counts
            # against precision instead of being silently dropped.
            got = [reverse.get(str(hit.id), str(hit.id)) for hit in hits]
            wanted = list(query.get("expect") or [])
            absent = set(query.get("expect_absent") or [])
            assert not (absent & set(got)), f"{case['id']}/{query['q']}: {absent & set(got)}"
            hits_total += len(got)
            expected_total += len(wanted)
            found_total += len(set(wanted) & set(got))
    return {
        "recall": found_total / expected_total if expected_total else 1.0,
        "precision": found_total / hits_total if hits_total else 1.0,
        "rows": len(canonical_ids),
        "distinct": len(set(texts)),
        "duplicate_rate": 1 - (len(set(texts)) / len(texts)) if texts else 0.0,
    }


def test_canonical_lexical_recall_and_precision(tmp_path) -> None:
    store = MemoryStore(
        database(), build_config(tmp_path), quota=WriteQuota(enabled=False)
    )
    summary = report(store, fixture())

    assert summary["recall"] >= RECALL_FLOOR, summary
    assert summary["precision"] >= PRECISION_FLOOR, summary
    assert summary["duplicate_rate"] < DUPLICATE_CEILING, summary


def test_backfill_index_fills_once_and_is_idempotent(tmp_path) -> None:
    store = MemoryStore(
        database(),
        build_config(tmp_path),
        index=FakeIndex(),
        quota=WriteQuota(enabled=False),
    )
    body = fixture()
    rows = sum(
        1
        for case in body["cases"]
        for fact in case["facts"]
        if not fact.get("veto")  # a vetoed row is not readable, so it has no index entry
    )
    for case in body["cases"]:
        seed(store, case)

    first = asyncio.run(store.backfill_index(limit=500))
    assert first == rows
    assert asyncio.run(store.backfill_index(limit=500)) == 0
