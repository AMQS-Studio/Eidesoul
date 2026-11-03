"""Capsule eras: a drifting digest archives the old topic instead of wiping it."""


import asyncio
from datetime import datetime, timedelta

from support import FakeLLM, build_config, database

from eidesoul.engine.config import AffectConfig
from eidesoul.engine.jobs.ambient import Ambient, drift
from eidesoul.engine.stores.affect import AffectStore
from eidesoul.engine.stores.history import HistoryStore
from eidesoul.engine.turn.context import Message

CHAT = "chat"


class DigestLLM(FakeLLM):
    def __init__(self, digest: dict) -> None:
        super().__init__()
        self.script = digest

    async def digest(self, chat_name, transcript, topic, people):
        return dict(self.script)


def build(tmp_path, digest: dict):
    db = database()
    config = build_config(tmp_path, affect=AffectConfig(enabled=True))
    affect = AffectStore(db, config.affect, soul=config.soul.persona)
    history = HistoryStore(db, config.workspace)
    for index in range(3):
        history.add(
            CHAT,
            Message(
                id=f"m{index}",
                sender_name="Budi",
                sender_id="628111",
                when=datetime(2026, 1, 1, 10) + timedelta(minutes=index),
                text=f"kabar {index}",
            ),
        )
    ambient = Ambient(config, DigestLLM(digest), affect, None, None, history, None, None)
    return db, affect, ambient


def test_drift_needs_an_overlap_and_a_new_topic() -> None:
    assert drift("rapat logistik gudang", "jadwal puasa ramadan") is True
    assert drift("rapat logistik gudang", "rapat logistik gudang") is False
    assert drift("rapat logistik", "") is False


def test_a_drifting_digest_archives_the_previous_topic(tmp_path) -> None:
    digest = {"topic": "jadwal puasa", "keywords": ["jadwal", "puasa"]}
    _db, affect, ambient = build(tmp_path, digest)
    affect.set_topic(CHAT, "rapat logistik gudang", ["rapat", "logistik"], ["siapa yang kirim?"])
    before = affect.topic(CHAT)

    asyncio.run(ambient._digest(CHAT))

    eras = affect.eras(CHAT)
    assert len(eras) == 1
    assert eras[0]["summary"] == "rapat logistik gudang"
    assert eras[0]["keywords"] == ["rapat", "logistik"]
    assert eras[0]["started_at"] == before["last_message"]
    assert "Sebelumnya: rapat logistik gudang" in affect.topic_block(CHAT)
    assert affect.topic(CHAT)["summary"] == "jadwal puasa"


def test_the_same_topic_keeps_the_era_count(tmp_path) -> None:
    _db, affect, ambient = build(
        tmp_path, {"topic": "rapat logistik", "keywords": ["rapat", "logistik"]}
    )
    affect.set_topic(CHAT, "rapat logistik", ["rapat", "logistik"], [])

    asyncio.run(ambient._digest(CHAT))

    assert affect.eras(CHAT) == []
    assert "Sebelumnya" not in affect.topic_block(CHAT)
