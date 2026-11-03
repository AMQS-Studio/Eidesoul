"""Payload assembly contract: newest tail, quote anchors, and the character budget."""


import asyncio
from datetime import datetime, timedelta

from support import build_config, database

from eidesoul.engine.assembly import TAIL_MAX_MESSAGES, thread
from eidesoul.engine.context import Message, Quote, build_user_content
from eidesoul.engine.stores.history import HistoryStore

CHAT = "chat"
START = datetime(2026, 1, 1, 10)


def message(mid: str, text: str, when: datetime, *, quote: Quote | None = None) -> Message:
    return Message(
        id=mid,
        sender_name="Budi",
        sender_id="628111",
        sender_address="628111@s.whatsapp.net",
        when=when,
        text=text,
        quote=quote,
    )


def archive(tmp_path, count: int, *, size: int = 8) -> HistoryStore:
    store = HistoryStore(database())
    for index in range(count):
        text = f"pesan {index}".ljust(size, "x")
        store.add(CHAT, message(f"m{index}", text, START + timedelta(minutes=index)))
    return store


def quote_of(mid: str, text: str) -> Quote:
    return Quote(id=mid, sender_name="Budi", sender_id="628111", text=text)


def test_the_quoted_message_joins_the_newest_rows(tmp_path) -> None:
    store = archive(tmp_path, 30)
    current = message(
        "cur",
        "kenapa begitu?",
        START + timedelta(minutes=31),
        quote=quote_of("m0", "pesan 0"),
    )

    ids = [entry.id for entry in thread(store, CHAT, current, max_chars=4000)]

    assert "m0" in ids
    assert "m29" in ids
    assert "cur" in ids
    assert "m2" not in ids
    assert ids[-1] == "cur"
    assert len(ids) <= TAIL_MAX_MESSAGES + 3


def test_the_budget_drops_the_oldest_row_beyond_the_floor(tmp_path) -> None:
    store = archive(tmp_path, 10, size=200)
    current = message(
        "cur", "y" * 50, START + timedelta(minutes=11), quote=quote_of("m0", "pesan 0")
    )

    ids = [entry.id for entry in thread(store, CHAT, current, max_chars=300)]

    assert "cur" in ids
    assert "m0" in ids
    assert "m4" not in ids
    assert {"m5", "m6", "m7", "m8", "m9"}.issubset(set(ids))


def test_the_floor_wins_over_the_budget(tmp_path) -> None:
    store = archive(tmp_path, 6, size=8)
    current = message("cur", "y" * 900, START + timedelta(minutes=7))

    ids = [entry.id for entry in thread(store, CHAT, current, max_chars=50)]

    assert {"m3", "m4", "m5", "cur"}.issubset(set(ids))


def test_rows_newer_than_the_turn_never_enter_the_payload(tmp_path) -> None:
    store = archive(tmp_path, 5)
    current = message("cur", "sekarang", START + timedelta(minutes=2))
    store.add(CHAT, message("later", "belakangan", START + timedelta(minutes=9)))

    ids = [entry.id for entry in thread(store, CHAT, current, max_chars=4000)]

    assert "later" not in ids
    assert "cur" in ids


def test_the_rendered_payload_announces_a_thread_and_keeps_the_anchor(tmp_path) -> None:
    store = archive(tmp_path, 30)
    current = message(
        "cur",
        "kenapa begitu?",
        START + timedelta(minutes=31),
        quote=quote_of("m0", "pesan 0"),
    )
    config = build_config(tmp_path)

    rows = thread(store, CHAT, current, max_chars=4000)
    text = asyncio.run(build_user_content(config, CHAT, rows, current))

    assert text.startswith("Thread (")
    assert "pesan 0" in text
    assert "kenapa begitu?" in text
