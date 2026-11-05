"""Tool web_search: one configured provider plus an ordered fallback chain.

ADR-0004 §4.5: every adapter normalizes its payload into `SearchHit`; the tool, the forced
research path, and the card builder only ever see that uniform shape.
"""

import importlib.util
import logging
import os
import time
from dataclasses import dataclass

import httpx

from .. import io
from ..config import WebSearchConfig
from ..registry import Handler
from ..turn.research import build_card, render
from . import ToolContext, _int
from . import fetch as fetch_tool

log = logging.getLogger(__name__)

USER_AGENT = "monoagens"
SEARCH_ATTEMPTS = 4
SEARCH_BACKOFF = 1.5
SNIPPET_FLOOR = 200
MAX_FETCHES = 3
FETCH_CHARS = 4_000
DDGS_TIMEOUT_SECONDS = 10.0
HTTP_PROVIDERS = frozenset({"searxng", "tavily", "brave", "exa", "serper", "google"})
TAVILY_URL = "https://api.tavily.com/search"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
EXA_URL = "https://api.exa.ai/search"
SERPER_URL = "https://google.serper.dev/search"
GOOGLE_URL = "https://www.googleapis.com/customsearch/v1"


@dataclass(frozen=True)
class SearchHit:
    """One normalized result; a provider's JSON never leaves its adapter unnormalized."""

    title: str
    url: str
    snippet: str = ""
    published_at: str = ""

    def as_dict(self) -> dict:
        """The shape `build_card` and `enrich` already consume (title/href/body)."""
        return {
            "title": self.title,
            "href": self.url,
            "body": self.snippet,
            "published_at": self.published_at,
        }


class ProviderError(RuntimeError):
    """A provider cannot run or answered unusably; the message is a structured reason token."""


@dataclass(frozen=True)
class SearchBackend:
    name: str
    label: str
    detail: str
    needs_url: bool


def backends() -> list[SearchBackend]:
    """Backends this install can run, indexed by whether their dependency is importable."""
    found: list[SearchBackend] = []
    if importlib.util.find_spec("ddgs") is not None:
        found.append(
            SearchBackend("ddgs", "ddgs", "public result pages; no key; can rate-limit", False)
        )
    found.append(SearchBackend("searxng", "SearXNG", "operator-owned JSON endpoint", True))
    return found


def _text(value: object) -> str:
    return " ".join(str(value or "").split())


def _stamp(value: object) -> str:
    return str(value or "").strip()


def _first(*values: object) -> str:
    for value in values:
        stamp = _stamp(value)
        if stamp:
            return stamp
    return ""


def searx_hits(payload: dict, limit: int) -> list[SearchHit]:
    """SearXNG JSON -> `SearchHit`; entries without a URL are dropped."""
    hits: list[SearchHit] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = _stamp(item.get("url"))
        if not url:
            continue
        hits.append(
            SearchHit(
                title=_text(item.get("title")),
                url=url,
                snippet=_text(item.get("content")),
                published_at=_first(item.get("publishedDate"), item.get("published_date")),
            )
        )
        if len(hits) >= limit:
            break
    return hits


def tavily_hits(payload: dict, limit: int) -> list[SearchHit]:
    """Tavily JSON -> `SearchHit`."""
    hits: list[SearchHit] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = _stamp(item.get("url"))
        if not url:
            continue
        hits.append(
            SearchHit(
                title=_text(item.get("title")),
                url=url,
                snippet=_text(item.get("content")),
                published_at=_first(item.get("published_date"), item.get("publishedDate")),
            )
        )
        if len(hits) >= limit:
            break
    return hits


def brave_hits(payload: dict, limit: int) -> list[SearchHit]:
    """Brave Web Search JSON -> `SearchHit`."""
    web = payload.get("web")
    results = web.get("results") if isinstance(web, dict) else None
    hits: list[SearchHit] = []
    for item in results or []:
        if not isinstance(item, dict):
            continue
        url = _stamp(item.get("url"))
        if not url:
            continue
        hits.append(
            SearchHit(
                title=_text(item.get("title")),
                url=url,
                snippet=_text(item.get("description")),
                published_at=_first(item.get("page_age"), item.get("age")),
            )
        )
        if len(hits) >= limit:
            break
    return hits


def exa_hits(payload: dict, limit: int) -> list[SearchHit]:
    """Exa search JSON -> `SearchHit`."""
    hits: list[SearchHit] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = _stamp(item.get("url"))
        if not url:
            continue
        hits.append(
            SearchHit(
                title=_text(item.get("title")),
                url=url,
                snippet=_text(item.get("text") or item.get("summary")),
                published_at=_first(item.get("publishedDate"), item.get("published_date")),
            )
        )
        if len(hits) >= limit:
            break
    return hits


def serper_hits(payload: dict, limit: int) -> list[SearchHit]:
    """Serper (Google) JSON -> `SearchHit`."""
    hits: list[SearchHit] = []
    for item in payload.get("organic") or []:
        if not isinstance(item, dict):
            continue
        url = _stamp(item.get("link"))
        if not url:
            continue
        hits.append(
            SearchHit(
                title=_text(item.get("title")),
                url=url,
                snippet=_text(item.get("snippet")),
                published_at=_stamp(item.get("date")),
            )
        )
        if len(hits) >= limit:
            break
    return hits


def _google_stamp(item: dict) -> str:
    """Programmable Search reports dates only through the result's metatags."""
    pagemap = item.get("pagemap")
    if not isinstance(pagemap, dict):
        return ""
    metatags = pagemap.get("metatags")
    if not isinstance(metatags, list) or not metatags:
        return ""
    first = metatags[0]
    if not isinstance(first, dict):
        return ""
    return _first(
        first.get("article:published_time"),
        first.get("date"),
        first.get("og:updated_time"),
    )


def google_hits(payload: dict, limit: int) -> list[SearchHit]:
    """Programmable Search JSON -> `SearchHit`."""
    hits: list[SearchHit] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        url = _stamp(item.get("link"))
        if not url:
            continue
        hits.append(
            SearchHit(
                title=_text(item.get("title")),
                url=url,
                snippet=_text(item.get("snippet")),
                published_at=_google_stamp(item),
            )
        )
        if len(hits) >= limit:
            break
    return hits


def _secret(env_name: str) -> str:
    name = _stamp(env_name)
    return os.environ.get(name, "").strip() if name else ""


def _required_secret(env_name: str, reason: str) -> str:
    value = _secret(env_name)
    if not value:
        raise ProviderError(reason)
    return value


def _payload(response: httpx.Response) -> dict:
    response.raise_for_status()
    try:
        body = response.json()
    except ValueError as exc:
        raise ProviderError("invalid_response") from exc
    if not isinstance(body, dict):
        raise ProviderError("invalid_response")
    return body


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
) -> dict:
    response = await client.get(url, params=params, headers=headers)
    return _payload(response)


async def _post_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    body: dict,
    headers: dict | None = None,
) -> dict:
    response = await client.post(url, json=body, headers=headers)
    return _payload(response)


async def _searxng(
    client: httpx.AsyncClient, config: WebSearchConfig, query: str, limit: int
) -> list[SearchHit]:
    base_url = config.base_url
    if not base_url:
        raise ProviderError("base_url_missing")
    payload = await _get_json(
        client,
        f"{base_url}/search",
        params={"q": query, "format": "json"},
        headers={"Accept": "application/json"},
    )
    return searx_hits(payload, limit)


async def _tavily(
    client: httpx.AsyncClient, config: WebSearchConfig, query: str, limit: int
) -> list[SearchHit]:
    key = _required_secret(config.api_key_env, "missing_api_key")
    payload = await _post_json(
        client,
        TAVILY_URL,
        body={"query": query, "max_results": limit},
        headers={"Authorization": f"Bearer {key}"},
    )
    return tavily_hits(payload, limit)


async def _brave(
    client: httpx.AsyncClient, config: WebSearchConfig, query: str, limit: int
) -> list[SearchHit]:
    key = _required_secret(config.api_key_env, "missing_api_key")
    payload = await _get_json(
        client,
        BRAVE_URL,
        params={"q": query, "count": limit},
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
    )
    return brave_hits(payload, limit)


async def _exa(
    client: httpx.AsyncClient, config: WebSearchConfig, query: str, limit: int
) -> list[SearchHit]:
    key = _required_secret(config.api_key_env, "missing_api_key")
    payload = await _post_json(
        client,
        EXA_URL,
        body={
            "query": query,
            "numResults": limit,
            "contents": {"text": {"maxCharacters": 500}},
        },
        headers={"x-api-key": key},
    )
    return exa_hits(payload, limit)


async def _serper(
    client: httpx.AsyncClient, config: WebSearchConfig, query: str, limit: int
) -> list[SearchHit]:
    key = _required_secret(config.api_key_env, "missing_api_key")
    payload = await _post_json(
        client,
        SERPER_URL,
        body={"q": query, "num": limit},
        headers={"X-API-KEY": key},
    )
    return serper_hits(payload, limit)


async def _google(
    client: httpx.AsyncClient, config: WebSearchConfig, query: str, limit: int
) -> list[SearchHit]:
    key = _required_secret(config.api_key_env, "missing_api_key")
    engine_id = _required_secret(config.engine_id_env, "missing_engine_id")
    payload = await _get_json(
        client,
        GOOGLE_URL,
        params={"key": key, "cx": engine_id, "q": query, "num": limit},
    )
    return google_hits(payload, limit)


ADAPTERS = {
    "searxng": _searxng,
    "tavily": _tavily,
    "brave": _brave,
    "exa": _exa,
    "serper": _serper,
    "google": _google,
}


def _ddgs_sync(query: str, limit: int, region: str = "", backend: str = "auto") -> list[SearchHit]:
    """The blocking ddgs call; only ever reached through `io.run` from the loop."""
    from ddgs import DDGS

    engine = DDGS(timeout=DDGS_TIMEOUT_SECONDS)
    rows: list[dict] = []
    for attempt in range(SEARCH_ATTEMPTS):
        try:
            rows = engine.text(
                query,
                max_results=limit,
                region=region or None,
                backend=backend,
            )
        except Exception:  # a blocked or rate-limited engine must not break the search
            rows = []
        if rows or attempt == SEARCH_ATTEMPTS - 1:
            break
        time.sleep(SEARCH_BACKOFF)
    found: list[SearchHit] = []
    seen: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        url = _stamp(row.get("href") or row.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        found.append(
            SearchHit(
                title=_text(row.get("title")),
                url=url,
                snippet=_text(row.get("body") or row.get("snippet")),
                published_at=_first(row.get("published"), row.get("published_at")),
            )
        )
        if len(found) >= limit:
            break
    return found


async def _ddgs(config: WebSearchConfig, query: str, limit: int) -> list[SearchHit]:
    try:
        return await io.run(_ddgs_sync, query, limit, config.region, config.ddgs_backend)
    except ImportError as exc:
        raise ProviderError("ddgs_unavailable") from exc


def _reason(error: BaseException) -> str:
    """A parseable reason token; never the provider's message (it may echo the key)."""
    if isinstance(error, ProviderError):
        return str(error) or "provider_error"
    if isinstance(error, httpx.TimeoutException):
        return "timeout"
    if isinstance(error, httpx.HTTPStatusError):
        return f"http_{error.response.status_code}"
    if isinstance(error, httpx.HTTPError):
        return "transport_error"
    name = type(error).__name__.lower()
    return name or "error"


def provider_chain(config: WebSearchConfig) -> tuple[str, ...]:
    """`provider` first, then each configured fallback once; `off` stops the chain."""
    if config.provider == "off":
        return ()
    chain: list[str] = []
    for name in (config.provider, *config.fallbacks):
        if name == "off" or name in chain:
            continue
        chain.append(name)
    return tuple(chain)


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


async def _attempt(
    name: str,
    client: httpx.AsyncClient | None,
    config: WebSearchConfig,
    query: str,
    limit: int,
) -> list[SearchHit]:
    if name == "ddgs":
        return await _ddgs(config, query, limit)
    adapter = ADAPTERS.get(name)
    if adapter is None:
        raise ProviderError("unknown_provider")
    if client is None:
        raise ProviderError("client_missing")
    return await adapter(client, config, query, limit)


async def _search_chain(
    config: WebSearchConfig,
    client: httpx.AsyncClient | None,
    query: str,
    limit: int,
) -> list[SearchHit]:
    for name in provider_chain(config):
        try:
            hits = await _attempt(name, client, config, query, limit)
        except Exception as exc:  # one provider's failure moves to the next, never aborts
            log.warning("search provider=%s status=failed reason=%s", name, _reason(exc))
            continue
        log.info("search provider=%s status=ok reason=results_%d", name, len(hits))
        return hits
    return []


async def search(ctx: ToolContext, query: str, limit: int) -> list[dict]:
    """Raw search hits, shared by the tool and the forced research path."""
    text = str(query or "").strip()
    if not text:
        return []
    config = ctx.config.web_search
    chain = provider_chain(config)
    if not chain:
        return []
    count = min(10, max(1, int(limit)))
    if any(name in HTTP_PROVIDERS for name in chain):
        async with _client(config.timeout_seconds) as client:
            hits = await _search_chain(config, client, text, count)
    else:
        hits = await _search_chain(config, None, text, count)
    return [hit.as_dict() for hit in hits]


def _quote(hit: dict) -> str:
    return " ".join(str(hit.get("body") or hit.get("snippet") or "").split())


async def enrich(ctx: ToolContext, hits: list[dict]) -> tuple[list[dict], bool]:
    """Replace short snippets with fetched page text; a failed required fetch fails the card."""
    config = ctx.config.web_search.fetch
    if not config.enabled:
        return list(hits), False
    results: list[dict] = []
    fetches = 0
    failures = 0
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        url = str(hit.get("href") or hit.get("url") or "").strip()
        quote = _quote(hit)
        if url and fetches < MAX_FETCHES and len(quote) < SNIPPET_FLOOR:
            fetches += 1
            try:
                page = await fetch_tool.read_public(
                    url,
                    config,
                    max_chars=FETCH_CHARS,
                    language=ctx.config.soul.language,
                )
            except Exception as exc:  # optional enrichment: keep the snippet and continue
                failures += 1
                log.warning(
                    "research fetch failed url=%s (%s: %s)",
                    url,
                    type(exc).__name__,
                    exc,
                )
            else:
                if page.text and not page.flagged:
                    hit = {**hit, "href": page.url, "body": page.text}
        results.append(hit)
    usable = any(_quote(hit) for hit in results)
    return results, bool(fetches and failures and not usable)


async def collect(ctx: ToolContext, query: str, limit: int) -> tuple[list[dict], bool]:
    """Search plus optional page enrichment, shared by the tool and the forced research path."""
    hits = await search(ctx, query, limit)
    return await enrich(ctx, hits)


async def _web_search(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "error: empty query"
    for card in reversed(ctx.research_cards):
        if card.query == query and card.status == "ok":
            return render(card)
    limit = min(
        10,
        max(1, _int(args, "max_results", ctx.config.research.max_results)),
    )
    results, failed = await collect(ctx, query, limit)
    for hit in results:
        if isinstance(hit, dict) and hit.get("published"):
            hit["published_at"] = hit["published"]
    card = build_card(
        query,
        results,
        timezone=ctx.config.soul.timezone,
        reason="tool",
        status="failed" if failed else None,
    )
    ctx.research_cards.append(card)
    return render(card)


TOOL = Handler(
    name="web_search",
    target="",
    approval_class="none",
    family="web_search",
    description="Search the web and return titles, URLs, and snippets.",
    fn=_web_search,
    aliases=("search",),
    max_output_chars=4_000,
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search keywords."},
            "max_results": {
                "type": "integer",
                "description": "Result count 1-10 (default from engine.research.max_results).",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
