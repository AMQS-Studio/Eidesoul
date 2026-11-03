"""Database archive contract: rich upsert, windows, quotes, seeding, and the intake rows."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import text as sql_text
from support import database

from eidesoul.engine.context import Media, Message, Quote
from eidesoul.engine.schema import messages
from eidesoul.engine.stores.history import HistoryStore


def message(
    mid: str,
    text: str = "",
    *,
    when: datetime | None = None,
    sender_name: str = "Budi",
    sender_address: str = "628222@s.whatsapp.net",
    sender_kind: str = "person",
    media: Media | None = None,
    quote: Quote | None = None,
    outgoing: bool = False,
    author: str = "unknown",
    visibility: int = 2,
    sender_id: str = "628222",
    view_once: bool = False,
    unreadable: bool = False,
) -> Message:
    return Message(
        id=mid,
        sender_name=sender_name,
        sender_id=sender_id,
        when=when or datetime(2026, 1, 1, 10),
        text=text,
        media=media,
        quote=quote,
        outgoing=outgoing,
        sender_address=sender_address,
        sender_kind=sender_kind,
        author=author,
        visibility=visibility,
        view_once=view_once,
        unreadable=unreadable,
    )


def test_recent_order_and_find(tmp_path) -> None:
    store = HistoryStore(database())
    store.add("chat", message("a", "satu", when=datetime(2026, 1, 1, 10)))
    store.add("chat", message("b", "dua", when=datetime(2026, 1, 1, 11)))

    assert [item.id for item in store.recent("chat", 10)] == ["a", "b"]
    assert store.find("chat", "a").text == "satu"
    assert store.find("chat", "hilang") is None


def test_upsert_keeps_richer_fields(tmp_path) -> None:
    store = HistoryStore(database())
    media = Media(
        kind="image", mimetype="image/png", filename="a.png", size=3, proto=None, ref=b"abc"
    )
    store.add("chat", message("a", ""))
    store.add(
        "chat",
        message("a", "teks asli", media=media, quote=Quote("q", "Ani", "9", "halo")),
    )
    store.add("chat", message("a", ""))

    item = store.find("chat", "a")
    assert item.text == "teks asli"
    assert item.media is not None and item.media.filename == "a.png"
    assert item.media.ref == b"abc"
    assert item.quote is not None and item.quote.text == "halo"
    assert store.message_count() == 1


def test_chat_name_and_recent_threads(tmp_path) -> None:
    store = HistoryStore(database())
    store.add("a@g.us", message("a", "x", when=datetime(2026, 1, 1, 10)))
    store.add("b@s.whatsapp.net", message("b", "y", when=datetime(2026, 1, 1, 11)))
    store.set_chat_name("a@g.us", "  Grup   Logistik ")
    store.set_chat_name("a@g.us", "")

    assert store.chat_name("a@g.us") == "Grup Logistik"
    assert store.chat_name("b@s.whatsapp.net") == ""
    assert store.recent_threads(10) == ["b@s.whatsapp.net", "a@g.us"]


def test_people_and_style_seed_skip_outgoing(tmp_path) -> None:
    store = HistoryStore(database())
    store.add("chat", message("a", "halo", when=datetime.now(), sender_name="Ani"))
    store.add("chat", message("b", "diam", when=datetime.now(), outgoing=True))

    people = store.people_seed()
    assert [row["text"] for row in people] == ["halo"]
    assert people[0]["sender_address"] == "628222@s.whatsapp.net"
    assert [row["text"] for row in store.style_sample()] == ["halo"]


def test_sender_address_and_kind_roundtrip(tmp_path) -> None:
    store = HistoryStore(database())
    store.add("chat", message("a", "halo", sender_kind=""))
    store.add("chat", message("b", "halo"))

    plain = store.find("chat", "a")
    person = store.find("chat", "b")

    assert plain.sender_address == "628222@s.whatsapp.net"
    assert plain.sender_kind == ""
    assert person.sender_kind == "person"


def test_author_and_visibility_roundtrip(tmp_path) -> None:
    store = HistoryStore(database())
    store.add("chat", message("a", "halo", author="manual_owner", visibility=3))

    item = store.find("chat", "a")

    assert item is not None
    assert item.author == "manual_owner"
    assert item.visibility == 3


def test_merge_never_downgrades_author_or_visibility(tmp_path) -> None:
    store = HistoryStore(database())
    store.add("chat", message("a", "halo", sender_name="Owner", sender_id="628567770837"))
    store.add(
        "chat",
        message("a", "", sender_name="Owner manual", author="manual_owner", visibility=3),
    )
    store.add(
        "chat",
        message("a", "", sender_name="eidesoul", sender_id="628567770837", author="bot"),
    )
    store.add("chat", message("a", "", sender_name="", sender_id="", author="unknown"))
    store.add("chat", message("a", "", sender_name="x", visibility=2))

    item = store.find("chat", "a")

    assert item is not None
    assert item.author == "bot"
    assert item.sender_name == "eidesoul"
    assert item.sender_id == "628567770837"
    assert item.visibility == 3
    assert item.text == "halo"


def test_visibility_only_rises_with_set_visibility(tmp_path) -> None:
    store = HistoryStore(database())
    store.add("chat", message("a", "satu"))
    store.add("chat", message("b", "dua"))

    assert store.set_visibility("chat", ("a", "b", "hilang", ""), 3) == 2
    assert store.set_visibility("chat", ("a",), 1) == 0

    assert store.find("chat", "a").visibility == 3
    assert store.find("chat", "b").visibility == 3


def test_unknown_author_values_fall_back_to_unknown(tmp_path) -> None:
    store = HistoryStore(database())
    store.add("chat", message("a", "x"))
    with store.db.connect() as conn:
        conn.execute(messages.update().values(author="weird", visibility=9))

    item = store.find("chat", "a")

    assert item is not None
    assert item.author == "unknown"
    assert item.visibility == 4


def test_mark_deleted_keeps_the_archived_text(tmp_path) -> None:
    store = HistoryStore(database())
    store.add("chat", message("a", "isi lama"))

    assert store.mark_deleted("chat", "a", "628999@s.whatsapp.net", "Ani") is True
    item = store.find("chat", "a")

    assert item is not None
    assert item.deleted is True
    assert item.deleted_admin is True
    assert item.deleted_by == "Ani"
    assert item.text == "isi lama"
    assert store.mark_deleted("chat", "a", "628999@s.whatsapp.net", "Ani") is False


def test_author_deletion_is_not_admin_and_later_upsert_keeps_the_flag(tmp_path) -> None:
    store = HistoryStore(database())
    store.add("chat", message("a", "isi"))
    store.mark_deleted("chat", "a", "628222@s.whatsapp.net")
    store.add("chat", message("a", ""))

    item = store.find("chat", "a")

    assert item is not None
    assert item.deleted is True
    assert item.deleted_admin is False
    assert item.text == "isi"


def test_view_once_and_unreadable_flags_roundtrip(tmp_path) -> None:
    store = HistoryStore(database())
    store.add("chat", message("a", "sekali", view_once=True))
    store.add("chat", message("b", "", unreadable=True))

    once = store.find("chat", "a")
    blank = store.find("chat", "b")

    assert once.view_once is True and once.unreadable is False
    assert blank.unreadable is True and blank.view_once is False


def test_real_message_merge_replaces_the_unreadable_placeholder(tmp_path) -> None:
    store = HistoryStore(database())
    store.add("chat", message("a", "", unreadable=True))
    assert store.find("chat", "a").unreadable is True

    store.add("chat", message("a", "isi asli", view_once=True))
    item = store.find("chat", "a")

    assert item.text == "isi asli"
    assert item.view_once is True
    assert item.unreadable is False


def _count(store: HistoryStore, table: str) -> int:
    return int(store.db.scalar(sql_text(f"SELECT count(*) FROM {table}")) or 0)


class ExplodingSink:
    """The W3.2 job insert is the last write in the intake transaction."""

    def enqueue(self, conn, candidate) -> None:
        raise RuntimeError("job insert failed")


def test_intake_is_one_transaction_with_memory_and_job(tmp_path) -> None:
    store = HistoryStore(database(), job_sink=ExplodingSink())

    with pytest.raises(RuntimeError, match="job insert failed"):
        store.intake("chat", message("m1", "aku suka kopi"), "628222")

    assert _count(store, "messages") == 0
    assert _count(store, "turns") == 0
    assert _count(store, "memories") == 0
    assert _count(store, "memory_events") == 0


def test_intake_writes_the_pending_candidate_and_event(tmp_path) -> None:
    store = HistoryStore(database())
    assert store.intake("chat", message("m1", "aku suka kopi"), "628222") is True
    assert store.intake("chat", message("m1", "aku suka kopi"), "628222") is False

    candidate = store.db.fetch_one(sql_text("SELECT * FROM memories"))
    event = store.db.fetch_one(sql_text("SELECT * FROM memory_events"))
    assert candidate["state"] == "pending" and candidate["level"] is None
    assert candidate["trust"] == 3 and candidate["person_id"] == "628222"
    assert candidate["refs"] == ["message:m1"]
    assert event["memory_id"] == candidate["id"]
    assert event["content"] == "aku suka kopi"
    assert _count(store, "memories") == 1
    assert _count(store, "memory_events") == 1


def test_intake_hard_exclusion_keeps_the_archive_only(tmp_path) -> None:
    store = HistoryStore(database())

    assert store.intake("chat", message("m1", "api_key=abc123"), "628222") is True

    assert _count(store, "messages") == 1
    assert _count(store, "turns") == 1
    assert _count(store, "memories") == 0
    assert _count(store, "memory_events") == 0
