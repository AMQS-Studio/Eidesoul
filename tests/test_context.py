"""Payload contract: author role and visibility, not the transport `outgoing` flag."""


import asyncio
from datetime import datetime

from support import build_config

from monoagens.engine.soul import Soul
from monoagens.engine.turn.context import (
    DEFAULT_VISIBILITY,
    Message,
    build_user_content,
    prompt_entry,
)

QUIET = Soul(
    language="id",
    locale="id-ID",
    timezone="UTC",
    name="monoagens",
    persona="p",
)


def message(
    mid: str,
    text: str,
    *,
    sender_name: str = "Budi",
    sender_id: str = "628222",
    author: str = "unknown",
    visibility: int = 2,
) -> Message:
    return Message(
        id=mid,
        sender_name=sender_name,
        sender_id=sender_id,
        when=datetime(2026, 1, 1, 10),
        text=text,
        author=author,
        visibility=visibility,
    )


def test_chat_message_normalizes_author_and_clamps_visibility() -> None:
    entry = Message("a", "Budi", "1", datetime(2026, 1, 1), "x", author="ghost", visibility=9)
    low = Message("b", "Budi", "1", datetime(2026, 1, 1), "x", visibility=-3)
    broken = Message("c", "Budi", "1", datetime(2026, 1, 1), "x", visibility="tinggi")  # type: ignore[arg-type]

    assert entry.author == "unknown"
    assert entry.visibility == 4
    assert low.visibility == 0
    assert broken.visibility == DEFAULT_VISIBILITY


def test_payload_tags_manual_owner_without_calling_it_you(tmp_path) -> None:
    config = build_config(tmp_path, soul=QUIET)
    manual = message("m1", "aku ketik sendiri", author="manual_owner")
    bot = message("b1", "halo", sender_name="monoagens", sender_id="628567770837", author="bot")

    text = asyncio.run(build_user_content(config, "chat", [manual, bot], bot))

    manual_head = next(line for line in text.splitlines() if line.startswith("[2026-01-01 10:00]"))
    bot_head = next(line for line in text.splitlines() if "role=bot" in line)
    assert "role=manual_owner" in manual_head
    assert "you" not in manual_head
    assert bot_head.startswith("[2026-01-01 10:00] monoagens (628567770837) [role=bot")
    assert bot_head.endswith("[role=bot vis=L2, you]:")


def test_prompt_entry_never_calls_manual_owner_you() -> None:
    manual = prompt_entry(message("m1", "aku manual", author="manual_owner"), "monoagens")
    bot = prompt_entry(
        message("b1", "halo", sender_name="monoagens", sender_id="1", author="bot"), "monoagens"
    )

    assert manual.startswith("[role=manual_owner vis=L2] Budi (manual owner): aku manual")
    assert "you" not in manual
    assert bot.startswith("[role=bot vis=L2] you (monoagens): halo")


def test_payload_visibility_tag_follows_the_entry(tmp_path) -> None:
    config = build_config(tmp_path, soul=QUIET)
    entry = message("m1", "aib", author="manual_owner", visibility=3)

    text = asyncio.run(build_user_content(config, "chat", [entry], entry))

    assert "role=manual_owner vis=L3" in text


def test_working_memory_merge_upgrades_authorship_and_visibility() -> None:
    from monoagens.engine.turn.context import WorkingMemory

    old = message("a", "halo", sender_name="Owner", sender_id="62")
    manual = message("a", "", sender_name="Owner Manual", author="manual_owner", visibility=3)
    bot = message("a", "", sender_name="monoagens", sender_id="9", author="bot")

    first = WorkingMemory._merge(old, manual)
    second = WorkingMemory._merge(first, bot)
    downgrade = WorkingMemory._merge(second, message("a", ""))

    assert (first.author, first.visibility) == ("manual_owner", 3)
    assert (second.author, second.sender_name, second.sender_id) == ("bot", "monoagens", "9")
    assert downgrade.author == "bot"
    assert downgrade.sender_name == "monoagens"
    assert downgrade.visibility == 3
    assert downgrade.text == "halo"


def row(mid: str, text: str, **kwargs) -> Message:
    return Message(
        id=mid,
        sender_name=kwargs.pop("sender_name", "Budi"),
        sender_id=kwargs.pop("sender_id", "628222"),
        when=datetime(2026, 1, 1, 10),
        text=text,
        sender_address=kwargs.pop("sender_address", ""),
        sender_kind=kwargs.pop("sender_kind", ""),
        **kwargs,
    )


def head(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("[2026-01-01 10:00]"))


def test_deleted_row_hides_its_text_in_the_payload(tmp_path) -> None:
    config = build_config(tmp_path, soul=QUIET)
    entry = row(
        "m1", "rahasia banget", author="manual_owner", sender_address="628222@s.whatsapp.net"
    )
    assert entry.mark_deleted("628222@s.whatsapp.net") is True

    text = asyncio.run(build_user_content(config, "chat", [entry], entry))

    assert "(message deleted by the sender)" in text
    assert "rahasia banget" not in text


def test_deleted_by_admin_names_the_admin_and_bot_rows_are_never_admin(tmp_path) -> None:
    config = build_config(tmp_path, soul=QUIET)
    member = row("m1", "isi", sender_address="628222@s.whatsapp.net")
    member.mark_deleted("628999@s.whatsapp.net", "Ani")
    bot = row("b1", "halo", sender_name="monoagens", sender_id="628567770837", author="bot",
              sender_address="628567770837@s.whatsapp.net")
    bot.mark_deleted("628567770837@s.whatsapp.net", "Owner")

    text = asyncio.run(build_user_content(config, "chat", [member, bot], bot))

    assert "(message deleted by admin: Ani)" in text
    assert "(message deleted by Owner)" in text


def test_view_once_row_is_tagged(tmp_path) -> None:
    config = build_config(tmp_path, soul=QUIET)
    entry = row("m1", "foto sekali lihat", view_once=True)

    text = asyncio.run(build_user_content(config, "chat", [entry], entry))

    assert "role=unknown vis=L2, view-once" in text
    assert "foto sekali lihat" in text


def test_unreadable_row_renders_a_note_and_tag(tmp_path) -> None:
    config = build_config(tmp_path, soul=QUIET)
    entry = row("m1", "", unreadable=True)

    text = asyncio.run(build_user_content(config, "chat", [entry], entry))

    assert "role=unknown vis=L2, undecryptable" in text
    assert "(message undecryptable on this device)" in text


def test_prompt_entry_uses_the_deletion_note_not_the_retained_text() -> None:
    entry = row("m1", "rahasia")
    entry.mark_deleted("628999@s.whatsapp.net", "Ani")

    assert prompt_entry(entry, "MonoAgens") == (
        "[role=unknown vis=L2] Budi: (message deleted by admin: Ani)"
    )


def test_deletion_matches_the_canonical_address_not_its_digits() -> None:
    digits_only = row("m1", "isi", sender_address="628222@s.whatsapp.net")
    same = row("m2", "isi", sender_address="628222@s.whatsapp.net")

    assert digits_only.mark_deleted("628222") is True
    assert digits_only.deleted_admin is True
    assert same.mark_deleted("628222@s.whatsapp.net") is True
    assert same.deleted_admin is False


def test_normal_row_tag_is_unchanged_and_person_rows_expose_a_mention_handle(tmp_path) -> None:
    config = build_config(tmp_path, soul=QUIET)
    plain = row("m1", "halo")
    person = row("m2", "halo", sender_address="628222@example.net", sender_kind="person")

    text = asyncio.run(build_user_content(config, "chat", [plain, person], person))
    lines = [line for line in text.splitlines() if line.startswith("[2026-01-01 10:00]")]

    assert lines[0] == "[2026-01-01 10:00] Budi (628222) [role=unknown vis=L2]:"
    assert lines[1].endswith("[role=unknown vis=L2, mention=@628222]:")


def test_a_non_person_address_gets_no_mention_tag(tmp_path) -> None:
    config = build_config(tmp_path, soul=QUIET)
    entry = row("m1", "halo", sender_address="628222@example.net")

    text = asyncio.run(build_user_content(config, "chat", [entry], entry))
    line = head(text)

    assert "mention=@" not in line
    assert line.endswith("[role=unknown vis=L2]:")


def test_placeholder_row_clears_when_the_real_message_arrives() -> None:
    from monoagens.engine.turn.context import WorkingMemory

    windows = WorkingMemory()
    windows.append("chat", row("a", "", unreadable=True))
    windows.append("chat", row("a", "isi asli", view_once=True))

    item = windows.get("chat")[0]
    assert item.text == "isi asli"
    assert item.view_once is True
    assert item.unreadable is False


def test_mark_deleted_falls_back_to_the_archive(tmp_path) -> None:
    from support import database

    from monoagens.engine.stores.history import HistoryStore
    from monoagens.engine.turn.context import WorkingMemory

    store = HistoryStore(database())
    store.add("chat", row("a", "lama"))
    store.add("chat", row("b", "baru"))
    windows = WorkingMemory(limit=1, store=store)

    assert windows.mark_deleted("chat", "a", "628999@s.whatsapp.net", "Ani") is True
    item = store.find("chat", "a")
    assert item.deleted is True
    assert item.deleted_admin is True
    assert windows.mark_deleted("chat", "hilang", "628999@s.whatsapp.net", "Ani") is False


def sticker_line(tmp_path, *, animated: bool) -> str:
    from monoagens.engine.turn.context import Media

    config = build_config(tmp_path, soul=QUIET)
    media = Media(
        kind="sticker",
        mimetype="image/webp",
        filename="x.webp",
        size=1,
        animated=animated,
    )
    entry = row("m1", "kirim stiker dong", media=media)

    text = asyncio.run(build_user_content(config, "chat", [entry], entry))

    return next(line for line in text.splitlines() if "x.webp" in line)


def test_animated_sticker_is_labelled_sticker_and_animated(tmp_path) -> None:
    line = sticker_line(tmp_path, animated=True)

    assert "sticker" in line
    assert "animated" in line


def test_static_sticker_is_labelled_static_sticker(tmp_path) -> None:
    line = sticker_line(tmp_path, animated=False)

    assert "static sticker" in line
    assert "animated" not in line


def test_prompt_entry_marks_an_animated_sticker() -> None:
    from monoagens.engine.turn.context import Media

    media = Media(kind="sticker", mimetype="image/webp", filename="x.webp", size=1, animated=True)

    assert prompt_entry(row("m1", "", media=media), "monoagens") == (
        "[role=unknown vis=L2] Budi: [sticker, animated]"
    )
