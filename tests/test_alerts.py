"""3.3: failure alerting counts streaks, dedupes, and never breaks the caller."""

import asyncio
from dataclasses import replace

from support import build_config

from monoagens.engine.config.load import _alerts_config
from monoagens.engine.config.models import AlertsConfig, AlertSourcesConfig
from monoagens.engine.observability import AlertManager


def _manager(tmp_path, **changes) -> AlertManager:
    config = build_config(tmp_path)
    wanted = replace(
        AlertsConfig(enabled=True, threshold=3, cooldown_seconds=900.0), **changes
    )
    return AlertManager(replace(config, alerts=wanted))


def _notifier(seen: list[str]):
    async def notify(text: str) -> None:
        seen.append(text)

    return notify


def test_alerts_are_off_until_the_operator_enables_them(tmp_path) -> None:
    manager = AlertManager(build_config(tmp_path))
    assert asyncio.run(manager.emit("llm", "boom")) is False


def test_three_consecutive_failures_fire_one_alert(tmp_path) -> None:
    manager = _manager(tmp_path)
    assert asyncio.run(manager.emit("llm", "boom")) is False
    assert asyncio.run(manager.emit("llm", "boom")) is False
    assert asyncio.run(manager.emit("llm", "boom")) is True
    assert asyncio.run(manager.emit("llm", "boom")) is False  # cooldown


def test_a_success_breaks_the_streak(tmp_path) -> None:
    manager = _manager(tmp_path)
    asyncio.run(manager.emit("llm", "boom"))
    asyncio.run(manager.emit("llm", "boom"))
    manager.clear("llm")

    assert manager.failures("llm") == 0
    assert asyncio.run(manager.emit("llm", "boom")) is False


def test_a_disabled_source_never_fires(tmp_path) -> None:
    manager = _manager(tmp_path, sources=AlertSourcesConfig(llm=False))

    for _ in range(3):
        assert asyncio.run(manager.emit("llm", "boom")) is False


def test_the_admin_notifier_gets_the_translated_text(tmp_path) -> None:
    seen: list[str] = []
    config = build_config(tmp_path)
    manager = AlertManager(
        replace(config, alerts=AlertsConfig(enabled=True, threshold=1)),
        notify=_notifier(seen),
    )

    assert asyncio.run(manager.emit("gateway", "crashed")) is True
    assert seen
    assert "gateway" in seen[0]
    assert "crashed" in seen[0]


def test_a_configured_webhook_receives_the_structured_payload(tmp_path, monkeypatch) -> None:
    from monoagens.engine.observability import alerts as module

    posted: list[tuple[str, dict]] = []

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> bool:
            return False

        async def post(self, url: str, json: dict) -> None:
            posted.append((url, json))

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("ALERT_HOOK", "https://hooks.example/alert")
    manager = _manager(tmp_path, webhook_url_env="ALERT_HOOK")

    for _ in range(3):
        asyncio.run(manager.emit("database", "pool exhausted", key="db"))

    assert posted[0][0] == "https://hooks.example/alert"
    assert posted[0][1]["source"] == "database"
    assert posted[0][1]["message"] == "pool exhausted"
    assert posted[0][1]["key"] == "db"


def test_engine_alerts_block_is_parsed() -> None:
    parsed = _alerts_config({"enabled": True, "threshold": 5, "sources": {"mcp": False}})

    assert parsed.enabled is True
    assert parsed.threshold == 5
    assert parsed.sources.mcp is False
    assert parsed.sources.database is True
