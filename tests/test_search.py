"""Search: FTS first, trigram as the second net, scope unchanged (ADR-0026)."""


from datetime import datetime

from support import database

from monoagens.engine.config import SearchConfig
from monoagens.engine.stores.history import HistoryStore
from monoagens.engine.turn.context import Message


def message(mid: str, text: str, stamp: float = 0.0, sender: str = "1") -> Message:
    return Message(
        id=mid,
        sender_name="Budi",
        sender_id=sender,
        when=datetime.fromtimestamp(stamp or 1_700_000_000),
        text=text,
    )


def store(**kwargs) -> HistoryStore:
    return HistoryStore(database(), search=SearchConfig(**kwargs))


def test_multi_word_query_uses_fts_and_keeps_order() -> None:
    rows = store()
    rows.add("chat", message("a", "Rekap RAPAT logistik", 9))
    rows.add("chat", message("b", "jadwal ronda", 10))
    rows.add("chat", message("c", "rapat panitia", 11))

    assert [hit.id for hit in rows.search("chat", "rapat logistik")] == ["a"]
    assert [hit.id for hit in rows.search("chat", "RAPAT")] == ["c", "a"]


def test_accent_variant_falls_back_to_trigram() -> None:
    rows = store()
    rows.add("chat", message("a", "kopi café enak"))

    assert [hit.id for hit in rows.search("chat", "cafe")] == ["a"]


def test_scope_is_the_thread_and_paging_does_not_duplicate() -> None:
    rows = store()
    rows.add("room-a", message("a1", "rapat satu", 10))
    rows.add("room-a", message("a2", "rapat dua", 20))
    rows.add("room-b", message("b1", "rapat tiga", 30))

    first = rows.search("room-a", "rapat", limit=1)
    assert [hit.id for hit in first] == ["a2"]
    rest = rows.search("room-a", "rapat", before=first[0].when.timestamp(), limit=5)
    assert [hit.id for hit in rest] == ["a1"]
    assert all(hit.id != "b1" for hit in first + rest)


def test_search_backend_reflects_the_config() -> None:
    assert store().search_backend() == "fts+trgm"
    assert store(trgm=False).search_backend() == "fts"
    assert store(fts=False, trgm=False).search_backend() == "like"


def test_rooms_of_uses_recorded_presence() -> None:
    rows = store()
    rows.add("room-a", message("a1", "halo", sender="person-1"))
    rows.add("room-b", message("b1", "hai", sender="person-2"))

    assert rows.rooms_of("person-1") == {"room-a"}
    assert rows.rooms_of("person-1") != {"room-a", "room-b"}
