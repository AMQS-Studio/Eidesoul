"""Tool web_search: SearXNG when `web_search.base_url` is set, otherwise the ddgs backend."""


import asyncio
import importlib.util
import json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from ..config import Config
from ..registry import Handler
from ..turn.research import build_card, render
from . import ToolContext, _int
from . import fetch as fetch_tool

log = logging.getLogger(__name__)

USER_AGENT = "monoagens"
# The library's auto engine set includes adapters that currently return `None`; yahoo is the
# adapter that answers in this environment, and it is still an ordinary public result page.
DDGS_BACKENDS = "yahoo"
SEARCH_ATTEMPTS = 4
SEARCH_BACKOFF = 1.5
SNIPPET_FLOOR = 200
MAX_FETCHES = 3
FETCH_CHARS = 4_000


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
    found.append(
        SearchBackend("searxng", "SearXNG", "operator-owned JSON endpoint", True)
    )
    return found


def searx_hits(payload: dict, limit: int) -> list[dict]:
    """SearXNG JSON -> uniform hits (title/href/body); entries without a URL are dropped."""
    hits: list[dict] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not url:
            continue
        hits.append(
            {
                "title": str(item.get("title") or ""),
                "href": url,
                "body": str(item.get("content") or ""),
            }
        )
        if len(hits) >= limit:
            break
    return hits


def _searx_sync(base_url: str, query: str, limit: int, timeout: float) -> list[dict]:
    url = f"{base_url}/search?" + urllib.parse.urlencode({"q": query, "format": "json"})
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return searx_hits(json.load(response), limit)


async def _searx(config: Config, query: str, limit: int) -> list[dict]:
    base_url = config.web_search.base_url
    if not base_url:
        return []
    try:
        return await asyncio.to_thread(
            _searx_sync, base_url, query, limit, config.web_search.timeout_seconds
        )
    except Exception as exc:  # dead instance/broken JSON: ddgs is the fallback, search continues
        log.warning(
            "searxng %s failed (%s: %s); falling back to ddgs", base_url, type(exc).__name__, exc
        )
        return []


def _search_sync(query: str, limit: int, region: str = "") -> list[dict]:
    from ddgs import DDGS

    engine = DDGS(timeout=10)
    hits: list[dict] = []
    for attempt in range(SEARCH_ATTEMPTS):
        try:
            hits = engine.text(
                query,
                max_results=limit,
                region=region or None,
                backend=DDGS_BACKENDS,
            )
        except Exception:  # a blocked or rate-limited engine must not break the search
            hits = []
        if hits or attempt == SEARCH_ATTEMPTS - 1:
            break
        time.sleep(SEARCH_BACKOFF)
    found: list[dict] = []
    seen: set[str] = set()
    for hit in hits:
        href = str(hit.get("href") or hit.get("url") or "")
        if href and href in seen:
            continue
        seen.add(href)
        found.append(hit)
        if len(found) >= limit:
            break
    return found[:limit]


async def search(ctx: ToolContext, query: str, limit: int) -> list[dict]:
    """Raw search hits, shared by the tool and the forced research path."""
    text = str(query or "").strip()
    if not text:
        return []
    count = min(10, max(1, int(limit)))
    results = await _searx(ctx.config, text, count)
    if not results:
        results = await asyncio.to_thread(
            _search_sync, text, count, ctx.config.web_search.region
        )
    return results


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
