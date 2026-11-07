"""Shutdown and boot validation (ADR-0001 §4.4, ADR-0003 §9)."""


import asyncio

import pytest
from sqlalchemy import text
from support import make_agent, make_runtime

from monoagens.engine.config import ConfigError
from monoagens.engine.jobs.supervisor import Supervisor


class FakeLease:
    """Lease surface the supervisor needs; the database is real, the CAS is not."""

    def __init__(self, db) -> None:
        self.db = db
        self.generation = 0
        self.instance_id = "test"
        self.released = False

    def release(self) -> None:
        self.released = True


def _shutting_down(env) -> bool:
    row = env.db.fetch_one(text("SELECT shutting_down FROM engine_control WHERE id = 1"))
    return bool(row["shutting_down"])


def test_the_shutdown_phases_set_and_clear_the_flag(tmp_path) -> None:
    env = make_agent(tmp_path)
    lease = FakeLease(env.db)
    supervisor = Supervisor(env.agent, lease, [env.gateway])

    supervisor._set_shutting_down(True)
    assert _shutting_down(env) is True

    asyncio.run(supervisor.shutdown())

    assert lease.released is True
    assert _shutting_down(env) is False
    assert env.agent.mode == "standby"
    assert env.agent.runtime.phase == "stopped"
    assert env.agent.draining is True


def test_shutdown_notices_are_per_epoch_and_never_deliver(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.begin_drain()
    thread = "628111@s.whatsapp.net"
    env.agent._shutdown_gateways[thread] = env.gateway
    first = asyncio.run(env.agent.shutdown_notices({thread: "t-1"}, 1.0))
    second = asyncio.run(env.agent.shutdown_notices({thread: "t-1"}, 1.0))

    assert first == 1
    assert second == 0


def test_config_rejects_grace_not_above_drain_plus_notice(tmp_path, monkeypatch) -> None:
    extra = (
        "  turns:\n"
        "    shutdown:\n"
        "      grace_seconds: 10\n"
        "      drain_seconds: 15\n"
        "      notice_seconds: 5\n"
    )
    with pytest.raises(ConfigError, match="grace_seconds"):
        make_runtime(tmp_path, monkeypatch, engine_extra=extra)


def test_config_rejects_grace_above_the_orchestrator_budget(tmp_path, monkeypatch) -> None:
    extra = (
        "  turns:\n"
        "    shutdown:\n"
        "      grace_seconds: 40\n"
        "      drain_seconds: 15\n"
        "      notice_seconds: 5\n"
        "      stop_grace_period: 30\n"
    )
    with pytest.raises(ConfigError, match="stop_grace_period"):
        make_runtime(tmp_path, monkeypatch, engine_extra=extra)
