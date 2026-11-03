"""`eidesoul models`: the provider lists its ids, the operator picks one."""


import json
from dataclasses import replace

import pytest
from support import build_config

from eidesoul.admin import models
from eidesoul.engine.config import ConfigError


class FakeResponse:
    def __init__(self, body: dict) -> None:
        self._raw = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc) -> None:
        return None


def patch_urlopen(monkeypatch, body: dict | None = None, *, error: Exception | None = None):
    seen: list[str] = []

    def fake(request, timeout=None):
        seen.append(request.full_url)
        if error is not None:
            raise error
        return FakeResponse(body or {})

    monkeypatch.setattr(models.urllib.request, "urlopen", fake)
    return seen


def test_list_models_reads_the_provider_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("K", "key")
    seen = patch_urlopen(
        monkeypatch,
        {"object": "list", "data": [{"id": "m-b"}, {"id": "m-a"}, {"object": "model"}]},
    )

    assert models.list_models(build_config(tmp_path)) == ["m-a", "m-b"]
    assert seen == ["http://x/models"]


def test_list_models_needs_the_key(tmp_path, monkeypatch):
    monkeypatch.delenv("K", raising=False)
    patch_urlopen(monkeypatch, {"data": []})

    with pytest.raises(ConfigError, match="env K is not set"):
        models.list_models(build_config(tmp_path))


def test_list_models_reports_an_unreachable_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("K", "key")
    patch_urlopen(monkeypatch, error=OSError("connection refused"))

    with pytest.raises(ConfigError, match="cannot list models at http://x/models"):
        models.list_models(build_config(tmp_path))


def test_list_models_hints_when_the_provider_exposes_no_list(tmp_path, monkeypatch):
    monkeypatch.setenv("K", "key")
    patch_urlopen(
        monkeypatch,
        error=models.urllib.error.HTTPError("http://x/models", 403, "Forbidden", None, None),
    )

    with pytest.raises(ConfigError, match="set engine.llm.model from the provider's docs"):
        models.list_models(build_config(tmp_path))


def test_list_models_prefers_the_provider_list_url(tmp_path, monkeypatch):
    monkeypatch.setenv("K", "key")
    config = build_config(tmp_path)
    config = replace(config, llm=replace(config.llm, models_url="http://list/v1/models"))
    seen = patch_urlopen(monkeypatch, {"data": [{"id": "m"}]})

    assert models.list_models(config) == ["m"]
    assert seen == ["http://list/v1/models"]
