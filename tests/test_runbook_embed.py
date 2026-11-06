"""Offline startup: a cached embedder must not make a network call (runbook I.1)."""

import huggingface_hub

from monoagens.engine import embed


def test_cached_true_for_a_snapshot_on_disk(monkeypatch) -> None:
    monkeypatch.setattr(
        huggingface_hub, "try_to_load_from_cache", lambda *args, **kwargs: "/cache/modules.json"
    )
    assert embed._cached(embed.MODEL) is True


def test_cached_false_without_a_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *args, **kwargs: None)
    assert embed._cached(embed.MODEL) is False


def test_cache_lookup_failure_is_not_fatal(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("no network")

    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", boom)
    assert embed._cached(embed.MODEL) is False
