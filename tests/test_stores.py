"""Store seams: staged promotion resolves the column each evidence id belongs to."""


from support import build_config, database

from monoagens.engine.memory.store import MemoryStore
from monoagens.engine.schema import memories, memory_events


def memory_store(tmp_path) -> MemoryStore:
    return MemoryStore(database(), build_config(tmp_path))


def state_of(store: MemoryStore, memory_id: str) -> str:
    assert store.db is not None
    row = store.db.fetch_one(
        memories.select().where(memories.c.id == memory_id)
    )
    assert row is not None
    return str(row["state"])


def source(store: MemoryStore, text: str, refs: list[str]) -> str:
    return store.write(text, person_id="per_source", level=1, refs=refs)


def test_promote_staged_resolves_message_refs_and_ignores_non_uuid_ids(tmp_path) -> None:
    store = memory_store(tmp_path)
    source(store, "first message", ["message:msg-1"])
    source(store, "second message", ["message:msg-2"])
    staged = store.write(
        "two sources agree",
        person_id="per_source",
        level=1,
        state="staged",
        refs=["message:msg-1", "message:msg-2"],
    )
    lonely = store.write(
        "two unknown messages",
        person_id="per_source",
        level=1,
        state="staged",
        refs=["message:msg-8", "message:msg-9"],
    )
    malformed = store.write(
        "ids that are not uuids",
        person_id="per_source",
        level=1,
        state="staged",
        refs=["not-a-uuid-at-all", "still-not-a-uuid"],
    )

    # A non-UUID id in `refs` must not reach `memories.id` (it is a UUID column).
    assert store.promote_staged() == 1
    assert state_of(store, staged) == "ok"
    assert state_of(store, lonely) == "staged"
    assert state_of(store, malformed) == "staged"


def test_promote_staged_still_accepts_memory_uuid_refs(tmp_path) -> None:
    store = memory_store(tmp_path)
    first = source(store, "uuid evidence one", ["note:one"])
    second = source(store, "uuid evidence two", ["note:two"])
    staged = store.write(
        "derived from two rows",
        person_id="per_source",
        level=1,
        state="staged",
        refs=[first, second],
    )
    staged_alone = store.write(
        "derived from one row",
        person_id="per_source",
        level=1,
        state="staged",
        refs=[first, "00000000-0000-0000-0000-000000000000"],
    )

    assert store.promote_staged() == 1
    assert state_of(store, staged) == "ok"
    assert state_of(store, staged_alone) == "staged"


def test_promote_staged_counts_message_events_as_evidence(tmp_path) -> None:
    store = memory_store(tmp_path)
    assert store.db is not None
    with store.db.connect() as conn:
        conn.execute(
            memories.insert().values(
                id="10000000-0000-4000-8000-000000000001",
                scope="person",
                person_id="per_source",
                thread_id="chat",
                kind="fact",
                text="captured from msg-9",
                state="ok",
                level=1,
                confirmed=True,
                owner_id="per_source",
                subject_id="per_source",
                via="turn",
                refs=[],
                trust=2,
                audience="private",
                source="turn",
                created_at=0.0,
                updated_at=0.0,
            )
        )
        conn.execute(
            memories.insert().values(
                id="10000000-0000-4000-8000-000000000002",
                scope="person",
                person_id="per_source",
                thread_id="chat",
                kind="fact",
                text="captured from msg-10",
                state="ok",
                level=1,
                confirmed=True,
                owner_id="per_source",
                subject_id="per_source",
                via="turn",
                refs=[],
                trust=2,
                audience="private",
                source="turn",
                created_at=0.0,
                updated_at=0.0,
            )
        )
        for index, message_id in enumerate(("msg-9", "msg-10"), start=1):
            conn.execute(
                memory_events.insert().values(
                    id=f"20000000-0000-4000-8000-00000000000{index}",
                    memory_id=f"10000000-0000-4000-8000-00000000000{index}",
                    thread_id="chat",
                    person_id="per_source",
                    message_id=message_id,
                    kind="message",
                    content="body",
                    content_hash=f"hash-{index}",
                    created_at=0.0,
                )
            )
    staged = store.write(
        "the events support this",
        person_id="per_source",
        level=1,
        state="staged",
        refs=["message:msg-9", "message:msg-10"],
    )

    assert store.promote_staged() == 1
    assert state_of(store, staged) == "ok"
