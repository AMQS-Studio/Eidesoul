"""ADR-0005 §4.6 intent hints: deterministic prototypes, the remember hint, and "forget that …"."""


import asyncio
import math
from types import SimpleNamespace

from support import build_config, database

from monoagens.engine import embed
from monoagens.engine.config import IntentConfig, RetentionConfig
from monoagens.engine.memory import intent
from monoagens.engine.memory.store import MemoryStore

PERSON = "628111"
THREAD = "628111@s.whatsapp.net"
DIAGONAL = 1 / math.sqrt(2)


def bank(monkeypatch, *, encoded: dict | None = None) -> None:
    """Replace the prototype vectors and the query encoder; no model, no pack files."""
    monkeypatch.setattr(
        intent,
        "_encoded",
        lambda language: encoded
        if encoded is not None
        else {
            "remember": ((1.0, 0.0),),
            "forget": ((0.0, 1.0),),
            "negative": ((DIAGONAL, DIAGONAL),),
        },
    )


def query(monkeypatch, vector: tuple[float, float]) -> None:
    monkeypatch.setattr(embed, "encode", lambda texts, **kwargs: [list(vector)])


def test_a_prototype_match_fires_and_a_question_does_not(monkeypatch) -> None:
    settings = IntentConfig()
    bank(monkeypatch)

    query(monkeypatch, (1.0, 0.0))
    assert intent.classify("ingat ya aku suka kopi", "id", settings) == "remember"

    query(monkeypatch, (0.0, 1.0))
    assert intent.classify("lupakan soal pekerjaan lamaku", "id", settings) == "forget"

    # A question scores both banks alike, so the negative always wins the margin.
    query(monkeypatch, (DIAGONAL, DIAGONAL))
    assert intent.classify("kamu ingat apa soal aku", "id", settings) == ""


def test_a_disabled_intent_never_fires(monkeypatch) -> None:
    bank(monkeypatch)
    query(monkeypatch, (1.0, 0.0))
    assert intent.classify("ingat ya", "id", IntentConfig(remember_enabled=False)) == ""
    assert intent.classify("ingat ya", "id", None) == ""


def test_a_missing_custom_pack_falls_back_to_the_builtin(tmp_path) -> None:
    assert intent.load("id", tmp_path) == intent.DEFAULT_BANK


def test_the_builtin_bank_serves_every_language() -> None:
    for code in ("en", "id", "ja"):
        pack = intent.load(code)
        assert len(pack["remember"]) >= 6
        assert len(pack["forget"]) >= 6
        assert len(pack["negative"]) >= 6


def store(tmp_path) -> MemoryStore:
    return MemoryStore(database(), build_config(tmp_path))


def test_the_remember_hint_forces_core_without_pinning_a_plain_category(
    tmp_path, monkeypatch
) -> None:
    from monoagens.engine.jobs.cognition import CognitionWorker

    memory = store(tmp_path)
    worker = CognitionWorker(database(), build_config(tmp_path), memory)
    verdict = SimpleNamespace(
        category="general", durability="normal", text="ingat ya aku suka kopi"
    )

    bank(monkeypatch)
    query(monkeypatch, (1.0, 0.0))
    category, durability = asyncio.run(worker._remember_hint({"text": "ingat ya"}, verdict))
    assert (category, durability) == ("general", "core")

    with memory.db.connect() as conn:
        candidate = memory.intake(
            conn, thread_id="t", person_id=PERSON, message_id="m1",
            text="ingat ya aku suka kopi dan teh setiap pagi",
        language="id",
        )
    assert memory.label_candidate(
        candidate.memory_id, level=2, confirmed=True, category=category, durability=durability
    )
    found = memory._base_row(candidate.memory_id)
    assert found["durability"] == "core"
    assert found["pinned"] is False

    query(monkeypatch, (DIAGONAL, DIAGONAL))
    assert asyncio.run(worker._remember_hint({"text": "halo"}, verdict)) == (
        "general",
        "normal",
    )


def test_forget_removes_the_one_entry_it_can_mean(tmp_path) -> None:
    memory = store(tmp_path)
    first = memory.write(
        "aku tinggal di Bandung sejak lama", scope="person", person_id=PERSON,
        thread_id=THREAD, owner_id=PERSON, subject_id=PERSON, level=2,
    )
    memory.write(
        "aku suka kopi pahit", scope="person", person_id=PERSON,
        thread_id=THREAD, owner_id=PERSON, subject_id=PERSON, level=2,
    )

    removed = asyncio.run(
        memory.forget_by_intent(
            "lupakan kalau aku tinggal di Bandung", person_id=PERSON, thread_id=THREAD
        )
    )

    assert removed == [first]
    assert memory.get(first) is None
    assert len(memory.readable()) == 1


def test_forget_stays_put_when_it_is_ambiguous(tmp_path) -> None:
    memory = store(tmp_path)
    memory.write(
        "aku tinggal di Bandung", scope="person", person_id=PERSON,
        thread_id=THREAD, owner_id=PERSON, subject_id=PERSON, level=2,
    )
    memory.write(
        "keluargaku tinggal di Bandung", scope="person", person_id=PERSON,
        thread_id=THREAD, owner_id=PERSON, subject_id=PERSON, level=2,
    )

    assert (
        asyncio.run(
            memory.forget_by_intent(
                "lupakan soal Bandung", person_id=PERSON, thread_id=THREAD
            )
        )
        == []
    )
    assert len(memory.readable()) == 2


def test_forget_matches_a_possessive_form_of_the_topic(tmp_path) -> None:
    """ADR-0009 §4.4: "gajiku" is still about the "gaji" memory when no token is shared."""
    memory = store(tmp_path)
    target = memory.write(
        "aku dapat gaji baru bulan ini", scope="person", person_id=PERSON,
        thread_id=THREAD, owner_id=PERSON, subject_id=PERSON, level=2,
    )
    memory.write(
        "aku suka kopi pahit", scope="person", person_id=PERSON,
        thread_id=THREAD, owner_id=PERSON, subject_id=PERSON, level=2,
    )

    removed = asyncio.run(
        memory.forget_by_intent("lupakan gajiku", person_id=PERSON, thread_id=THREAD)
    )

    assert removed == [target]
    assert memory.get(target) is None


def _vec(first: float, second: float = 0.0) -> list[float]:
    """A 384-dim vector, because the store only keeps vectors of the native dimension."""
    return [first, second] + [0.0] * 382


def test_forget_uses_cosine_for_a_paraphrase(tmp_path, monkeypatch) -> None:
    memory = store(tmp_path)
    vectors = {
        "aku bekerja di Acme Corp": _vec(1.0),
        "aku suka kopi pahit": _vec(0.0, 1.0),
        "lupakan tempat kerjaku": _vec(1.0),
    }
    monkeypatch.setattr(
        embed, "encode", lambda texts, **kwargs: [vectors.get(texts[0], _vec(0.0))]
    )
    target = memory.write(
        "aku bekerja di Acme Corp", scope="person", person_id=PERSON,
        thread_id=THREAD, owner_id=PERSON, subject_id=PERSON, level=2,
    )
    memory.write(
        "aku suka kopi pahit", scope="person", person_id=PERSON,
        thread_id=THREAD, owner_id=PERSON, subject_id=PERSON, level=2,
    )

    removed = asyncio.run(
        memory.forget_by_intent("lupakan tempat kerjaku", person_id=PERSON, thread_id=THREAD)
    )

    assert removed == [target]
    assert memory.get(target) is None


def test_semantic_forget_stays_put_when_two_rows_are_close(tmp_path, monkeypatch) -> None:
    memory = store(tmp_path)
    vectors = {
        "aku bekerja di Acme Corp": _vec(1.0),
        "aku bekerja di Beta Labs": _vec(0.99, 0.14),
        "lupakan tempat kerjaku": _vec(1.0),
    }
    monkeypatch.setattr(
        embed, "encode", lambda texts, **kwargs: [vectors.get(texts[0], _vec(0.0))]
    )
    memory.write(
        "aku bekerja di Acme Corp", scope="person", person_id=PERSON,
        thread_id=THREAD, owner_id=PERSON, subject_id=PERSON, level=2,
    )
    memory.write(
        "aku bekerja di Beta Labs", scope="person", person_id=PERSON,
        thread_id=THREAD, owner_id=PERSON, subject_id=PERSON, level=2,
    )

    removed = asyncio.run(
        memory.forget_by_intent("lupakan tempat kerjaku", person_id=PERSON, thread_id=THREAD)
    )

    assert removed == []
    assert len(memory.readable()) == 2


def test_the_audit_job_purges_with_the_configured_grace_and_batch(tmp_path) -> None:
    from monoagens.engine.jobs.cognition import CognitionWorker, Job

    class Recorder:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def purge(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(purged=0, selected=0, dry_run=False)

        @staticmethod
        def stale_disputes(**kwargs) -> int:
            return 0

    config = build_config(tmp_path)
    config = SimpleNamespace(
        **{
            **config.__dict__,
            "memory": SimpleNamespace(
                **{
                    **config.memory.__dict__,
                    "retention": RetentionConfig(purge_grace_days=2.5, purge_batch=7),
                }
            ),
        }
    )
    memory = Recorder()
    worker = CognitionWorker(database(), config, memory)

    asyncio.run(
        worker._audit(
            Job(
                id="j1",
                kind="audit",
                scope="global",
                payload_ref="",
                attempts=1,
                idempotency_key="audit:test",
            )
        )
    )

    assert memory.calls == [{"grace_days": 2.5, "batch": 7}]


def test_forget_ignores_the_request_words_and_targets_the_topic(tmp_path) -> None:
    memory = store(tmp_path)
    target = memory.write(
        "aku tinggal di Bandung sejak lama", scope="person", person_id=PERSON,
        thread_id=THREAD, owner_id=PERSON, subject_id=PERSON, level=2,
    )
    memory.write(
        "temanku cerita tentang kopi arabika", scope="person", person_id=PERSON,
        thread_id=THREAD, owner_id=PERSON, subject_id=PERSON, level=2,
    )

    removed = asyncio.run(
        memory.forget_by_intent(
            "tolong lupakan tentang Bandung", person_id=PERSON, thread_id=THREAD
        )
    )

    assert removed == [target]
    assert memory.get(target) is None
