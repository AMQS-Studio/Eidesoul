"""Lease contract: CAS, fencing generation, database clock, freeze, and takeover."""


import inspect
import threading
from pathlib import Path

import pytest
from sqlalchemy import select, text
from support import database

from eidesoul.engine.jobs import lease as lease_module
from eidesoul.engine.jobs.lease import Lease, Superseded
from eidesoul.engine.schema import actions


def lease(db, name: str, host: str = "host-a", pid: int = 1) -> Lease:
    return Lease(db, instance_id=name, host=host, pid=pid)


def test_acquire_renew_release(tmp_path) -> None:
    db = database()
    first = lease(db, "a")
    holder = first.acquire()
    assert holder is not None
    assert holder.instance_id == "a"
    assert holder.generation == 1
    assert first.acquire() is not None
    assert first.generation == 2  # every acquisition raises the fencing token
    assert first.renew() is True
    assert first.holder().pid == 1
    first.release()
    assert first.holder() is None
    assert first.renew() is False


def test_second_instance_stands_by_and_takeover(tmp_path) -> None:
    db = database()
    first = lease(db, "a")
    second = lease(db, "b", host="host-b", pid=2)
    assert first.acquire() is not None
    assert second.acquire() is None
    assert second.holder().instance_id == "a"

    taken = second.acquire(takeover=True)
    assert taken is not None
    assert taken.instance_id == "b"
    assert taken.generation == 2
    assert first.renew() is False
    first.release()  # only the generation owner may release: this is a no-op
    assert second.holder().instance_id == "b"


def test_expired_heartbeat_allows_acquire(tmp_path) -> None:
    db = database()
    first = lease(db, "a")
    first.acquire()
    with db.connect() as conn:
        conn.execute(
            text(
                "UPDATE engine_lease "
                "SET heartbeat_at = EXTRACT(EPOCH FROM clock_timestamp()) - :age"
            ),
            {"age": first.ttl + 1},
        )
    second = lease(db, "b")
    assert second.acquire() is not None


def test_expiry_ignores_the_application_clock(tmp_path) -> None:
    db = database()
    first = lease(db, "a")
    first.acquire()
    with db.connect() as conn:
        # A host whose clock runs ahead wrote this heartbeat: the database still says alive.
        conn.execute(
            text(
                "UPDATE engine_lease "
                "SET heartbeat_at = EXTRACT(EPOCH FROM clock_timestamp()) + 3600"
            )
        )
    assert lease(db, "b").acquire() is None
    source = Path(inspect.getsourcefile(lease_module)).read_text(encoding="utf-8")
    assert "time.time(" not in source


def test_concurrent_acquire_has_exactly_one_winner(tmp_path) -> None:
    db = database()
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def race(name: str) -> None:
        contender = lease(db, name, host=f"host-{name}", pid=0)
        barrier.wait()
        results[name] = contender.acquire()

    threads = [threading.Thread(target=race, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [name for name, holder in results.items() if holder is not None]
    assert len(winners) == 1
    holder = lease(db, "observer").holder()
    assert holder is not None
    assert holder.instance_id == winners[0]
    assert holder.generation == 1


def test_two_standbys_on_an_expired_row_write_one_generation(tmp_path) -> None:
    db = database()
    first = lease(db, "a")
    assert first.acquire() is not None
    with db.connect() as conn:
        conn.execute(
            text(
                "UPDATE engine_lease "
                "SET heartbeat_at = EXTRACT(EPOCH FROM clock_timestamp()) - :age"
            ),
            {"age": first.ttl + 1},
        )

    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def race(name: str) -> None:
        contender = lease(db, name, host=f"host-{name}", pid=0)
        barrier.wait()
        results[name] = contender.acquire()

    threads = [threading.Thread(target=race, args=(name,)) for name in ("b", "c")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [name for name, holder in results.items() if holder is not None]
    assert len(winners) == 1
    holder = lease(db, "observer").holder()
    assert holder is not None
    assert holder.instance_id == winners[0]
    assert holder.generation == 2


def test_a_db_error_is_a_lease_failure_not_a_crash(tmp_path, monkeypatch) -> None:
    db = database()
    item = lease(db, "a")
    assert item.acquire() is not None

    def boom():
        raise ConnectionError("database is gone")

    monkeypatch.setattr(db, "connect", boom)
    assert item.renew() is False
    assert item.holder() is None
    assert item.frozen() is False
    assert item.acquire() is None


def test_stale_generation_is_rejected_and_recorded(tmp_path) -> None:
    db = database()
    first = lease(db, "a")
    assert first.acquire() is not None
    second = lease(db, "b", host="host-b", pid=2)
    assert second.acquire(takeover=True) is not None

    assert second.guard.require("send") == second.generation
    with pytest.raises(Superseded):
        first.guard.require("send")
    records = db.fetch_all(select(actions).where(actions.c.kind == "lease"))
    summaries = " ".join(str(row["summary"]) for row in records)
    assert "takeover from" in summaries
    assert "superseded send" in summaries


def test_freeze_blocks_acquire_until_cleared(tmp_path) -> None:
    db = database()
    first = lease(db, "a")
    second = lease(db, "b")
    first.acquire()
    first.release()
    first.request_freeze("a")
    assert first.frozen() is True
    assert second.acquire() is None
    assert second.acquire(takeover=True) is not None
    assert second.frozen() is False


def test_ensure_does_not_clear_freeze(tmp_path) -> None:
    db = database()
    lease(db, "a").request_freeze("a")
    db.ensure()
    db.ensure()
    assert lease(db, "b").frozen() is True
