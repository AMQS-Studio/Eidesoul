"""ADR-0004 §4.5: provider adapters, normalized hits, and the ordered fallback chain."""

import asyncio
import json
import logging
import sys
from types import SimpleNamespace
from typing import ClassVar

import httpx
import pytest
from support import build_config, role

from monoagens.engine.config import ConfigError, WebSearchConfig, load_host
from monoagens.engine.tools import ToolContext, web_search


def make_ctx(tmp_path, **settings) -> ToolContext:
    config = build_config(tmp_path, web_search=WebSearchConfig(**settings))
    return ToolContext(config, (role("member"),), ("web_search",))


def run_search(tmp_path, monkeypatch, handler, **settings):
    """Route every adapter through one MockTransport, so no test touches the network."""
    ctx = make_ctx(tmp_path, **settings)
    monkeypatch.setattr(
        web_search,
        "_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout),
    )
    return asyncio.run(web_search.search(ctx, "berita", 5))


class FakeDDGS:
    """The library seam: records the call instead of opening a socket."""

    calls: ClassVar[list[dict]] = []

    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    def text(self, query, *, max_results, region, backend):
        FakeDDGS.calls.append(
            {
                "query": query,
                "max_results": max_results,
                "region": region,
                "backend": backend,
            }
        )
        return [
            {
                "title": "Hasil",
                "href": "https://example.com/ddgs",
                "body": "Ringkasan ddgs",
                "published": "2024-08-01",
            }
        ]


def test_loader_reads_the_provider_block_and_normalizes_the_base_url(tmp_path) -> None:
    (tmp_path / "config.yaml").write_text(
        "web_search:\n"
        "  provider: SearXNG\n"
        "  fallbacks:\n"
        "    - ddgs\n"
        "  base_url: http://searx.local/\n"
        "  api_key_env: SEARCH_KEY\n"
        "  engine_id_env: SEARCH_ENGINE\n"
        "  ddgs_backend: yahoo\n"
        "  region: id-id\n"
        "  timeout_seconds: 7\n",
        encoding="utf-8",
    )

    host, _paths = load_host(tmp_path / "config.yaml")

    assert host.web_search == WebSearchConfig(
        provider="searxng",
        fallbacks=("ddgs",),
        base_url="http://searx.local",
        api_key_env="SEARCH_KEY",
        engine_id_env="SEARCH_ENGINE",
        ddgs_backend="yahoo",
        region="id-id",
        timeout_seconds=7.0,
    )


def test_loader_defaults_to_the_keyless_ddgs_provider(tmp_path) -> None:
    (tmp_path / "config.yaml").write_text("web_search: {}\n", encoding="utf-8")

    host, _paths = load_host(tmp_path / "config.yaml")

    assert host.web_search.provider == "ddgs"
    assert host.web_search.fallbacks == ()
    assert host.web_search.ddgs_backend == "auto"


def test_loader_keeps_a_legacy_base_url_on_searxng(tmp_path) -> None:
    (tmp_path / "config.yaml").write_text(
        "web_search:\n  base_url: http://searx.local/\n", encoding="utf-8"
    )

    host, _paths = load_host(tmp_path / "config.yaml")

    assert host.web_search.provider == "searxng"
    assert host.web_search.base_url == "http://searx.local"


@pytest.mark.parametrize(
    "body",
    [
        "web_search:\n  provider: bing\n",
        "web_search:\n  provider: ddgs\n  fallbacks:\n    - bing\n",
    ],
)
def test_loader_rejects_a_typo_in_the_provider_chain(tmp_path, body) -> None:
    (tmp_path / "config.yaml").write_text(body, encoding="utf-8")

    with pytest.raises(ConfigError, match="unknown provider"):
        load_host(tmp_path / "config.yaml")


def test_ddgs_passes_the_configured_backend_and_is_offloaded(tmp_path, monkeypatch) -> None:
    FakeDDGS.calls = []
    monkeypatch.setitem(sys.modules, "ddgs", SimpleNamespace(DDGS=FakeDDGS))

    hits = run_search(tmp_path, monkeypatch, handler=None, provider="ddgs", ddgs_backend="auto")

    assert FakeDDGS.calls == [
        {"query": "berita", "max_results": 5, "region": None, "backend": "auto"}
    ]
    assert hits == [
        {
            "title": "Hasil",
            "href": "https://example.com/ddgs",
            "body": "Ringkasan ddgs",
            "published_at": "2024-08-01",
        }
    ]


def test_searxng_payload_is_normalized(tmp_path, monkeypatch) -> None:
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/search"
        assert request.url.params["q"] == "berita"
        assert request.url.params["format"] == "json"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Judul",
                        "url": "https://example.com/searx",
                        "content": "Ringkasan searx",
                        "publishedDate": "2024-05-01T10:00:00",
                    }
                ]
            },
        )

    hits = run_search(
        tmp_path,
        monkeypatch,
        handler,
        provider="searxng",
        base_url="http://searx.local",
    )

    assert hits == [
        {
            "title": "Judul",
            "href": "https://example.com/searx",
            "body": "Ringkasan searx",
            "published_at": "2024-05-01T10:00:00",
        }
    ]


def test_tavily_payload_is_normalized(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_KEY", "t-key")

    def handler(request):
        assert request.method == "POST"
        assert request.url.host == "api.tavily.com"
        assert request.headers["authorization"] == "Bearer t-key"
        body = json.loads(request.content)
        assert body["query"] == "berita"
        assert body["max_results"] == 5
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Tavily",
                        "url": "https://example.com/tavily",
                        "content": "Ringkasan tavily",
                        "published_date": "2024-06-01",
                    }
                ]
            },
        )

    hits = run_search(
        tmp_path,
        monkeypatch,
        handler,
        provider="tavily",
        api_key_env="TAVILY_KEY",
    )

    assert hits[0]["title"] == "Tavily"
    assert hits[0]["href"] == "https://example.com/tavily"
    assert hits[0]["body"] == "Ringkasan tavily"
    assert hits[0]["published_at"] == "2024-06-01"


def test_brave_payload_is_normalized(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BRAVE_KEY", "b-key")

    def handler(request):
        assert request.method == "GET"
        assert request.url.host == "api.search.brave.com"
        assert request.url.params["q"] == "berita"
        assert request.headers["x-subscription-token"] == "b-key"
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Brave",
                            "url": "https://example.com/brave",
                            "description": "Ringkasan brave",
                            "page_age": "2024-04-02T00:00:00",
                        }
                    ]
                }
            },
        )

    hits = run_search(tmp_path, monkeypatch, handler, provider="brave", api_key_env="BRAVE_KEY")

    assert hits[0]["href"] == "https://example.com/brave"
    assert hits[0]["body"] == "Ringkasan brave"
    assert hits[0]["published_at"] == "2024-04-02T00:00:00"


def test_exa_payload_is_normalized(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EXA_KEY", "e-key")

    def handler(request):
        assert request.method == "POST"
        assert request.url.host == "api.exa.ai"
        assert request.headers["x-api-key"] == "e-key"
        assert json.loads(request.content)["numResults"] == 5
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Exa",
                        "url": "https://example.com/exa",
                        "text": "Ringkasan exa",
                        "publishedDate": "2024-03-03",
                    }
                ]
            },
        )

    hits = run_search(tmp_path, monkeypatch, handler, provider="exa", api_key_env="EXA_KEY")

    assert hits[0]["href"] == "https://example.com/exa"
    assert hits[0]["body"] == "Ringkasan exa"
    assert hits[0]["published_at"] == "2024-03-03"


def test_serper_payload_is_normalized(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SERPER_KEY", "s-key")

    def handler(request):
        assert request.method == "POST"
        assert request.url.host == "google.serper.dev"
        assert request.headers["x-api-key"] == "s-key"
        assert json.loads(request.content) == {"q": "berita", "num": 5}
        return httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "Serper",
                        "link": "https://example.com/serper",
                        "snippet": "Ringkasan serper",
                        "date": "2024-02-04",
                    }
                ]
            },
        )

    hits = run_search(tmp_path, monkeypatch, handler, provider="serper", api_key_env="SERPER_KEY")

    assert hits[0]["href"] == "https://example.com/serper"
    assert hits[0]["body"] == "Ringkasan serper"
    assert hits[0]["published_at"] == "2024-02-04"


def test_google_payload_is_normalized_with_the_engine_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_KEY", "g-key")
    monkeypatch.setenv("GOOGLE_CX", "engine-id")

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/customsearch/v1"
        assert request.url.params["key"] == "g-key"
        assert request.url.params["cx"] == "engine-id"
        assert request.url.params["q"] == "berita"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "title": "Google",
                        "link": "https://example.com/google",
                        "snippet": "Ringkasan google",
                        "pagemap": {"metatags": [{"article:published_time": "2024-01-05"}]},
                    }
                ]
            },
        )

    hits = run_search(
        tmp_path,
        monkeypatch,
        handler,
        provider="google",
        api_key_env="GOOGLE_KEY",
        engine_id_env="GOOGLE_CX",
    )

    assert hits[0]["href"] == "https://example.com/google"
    assert hits[0]["body"] == "Ringkasan google"
    assert hits[0]["published_at"] == "2024-01-05"


def test_a_429_on_the_primary_moves_to_the_fallback(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setenv("TAVILY_KEY", "t-key")
    monkeypatch.setenv("SERPER_KEY", "s-key")
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.host)
        if request.url.host == "api.tavily.com":
            return httpx.Response(429, json={"detail": "rate limited"})
        return httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "Fallback",
                        "link": "https://example.com/serper",
                        "snippet": "Ringkasan fallback",
                    }
                ]
            },
        )

    with caplog.at_level(logging.WARNING, logger="monoagens.engine.tools.web_search"):
        hits = run_search(
            tmp_path,
            monkeypatch,
            handler,
            provider="tavily",
            fallbacks=("serper",),
            api_key_env="TAVILY_KEY",
        )

    assert calls == ["api.tavily.com", "google.serper.dev"]
    assert hits[0]["href"] == "https://example.com/serper"
    assert "search provider=tavily status=failed reason=http_429" in caplog.text


def test_a_missing_key_fails_only_that_provider(tmp_path, monkeypatch) -> None:
    FakeDDGS.calls = []
    monkeypatch.setitem(sys.modules, "ddgs", SimpleNamespace(DDGS=FakeDDGS))
    monkeypatch.delenv("ABSENT_SEARCH_KEY", raising=False)
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(200, json={"results": []})

    hits = run_search(
        tmp_path,
        monkeypatch,
        handler,
        provider="tavily",
        fallbacks=("ddgs",),
        api_key_env="ABSENT_SEARCH_KEY",
    )

    assert calls == []
    assert hits[0]["href"] == "https://example.com/ddgs"


def test_all_providers_failing_yields_an_empty_card(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_KEY", "t-key")
    monkeypatch.setenv("SERPER_KEY", "s-key")

    def handler(request):
        return httpx.Response(500, json={"error": "upstream"})

    ctx = make_ctx(
        tmp_path,
        provider="tavily",
        fallbacks=("serper",),
        api_key_env="TAVILY_KEY",
    )
    monkeypatch.setattr(
        web_search,
        "_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout),
    )

    text = asyncio.run(web_search._web_search({"query": "berita", "max_results": 3}, ctx))

    assert ctx.research_cards[-1].status == "empty"
    assert "Status: empty" in text


def test_provider_off_makes_no_request(tmp_path, monkeypatch) -> None:
    def no_client(timeout):
        raise AssertionError("provider off must not build an HTTP client")

    monkeypatch.setattr(web_search, "_client", no_client)
    monkeypatch.setitem(
        sys.modules, "ddgs", SimpleNamespace(DDGS=lambda **kwargs: pytest.fail("no ddgs"))
    )
    ctx = make_ctx(tmp_path, provider="off", fallbacks=("ddgs",))

    assert asyncio.run(web_search.search(ctx, "berita", 5)) == []
    assert web_search.provider_chain(ctx.config.web_search) == ()
