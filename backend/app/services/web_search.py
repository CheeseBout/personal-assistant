"""Web search — provider abstraction (Phase 8).

Default provider is DuckDuckGo via the ``ddgs`` package: no API key, suitable
for local-first use. Tavily/SerpAPI can be plugged in via settings without
changing callers. All providers return a normalized list of results:

    [{"title": str, "url": str, "snippet": str, "published": str|None}, ...]

Network failures and a missing provider library degrade gracefully to an empty
result set with an ``error`` note rather than raising — callers decide how to
surface "no results".
"""

from typing import Any, Dict, List

from ..core.config import settings
from ..core.logging_config import logger


def _search_duckduckgo(query: str, max_results: int) -> List[Dict[str, Any]]:
    """Search via the ddgs package (no API key needed)."""
    try:
        from ddgs import DDGS
    except ModuleNotFoundError:
        try:
            # Older package name.
            from duckduckgo_search import DDGS  # type: ignore
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "ddgs is required for web search. Install backend/requirements.txt."
            ) from exc

    results: List[Dict[str, Any]] = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title": r.get("title") or "",
                "url": r.get("href") or r.get("url") or "",
                "snippet": r.get("body") or r.get("snippet") or "",
                "published": r.get("date") or None,
            })
    return results


def _search_tavily(query: str, max_results: int) -> List[Dict[str, Any]]:
    """Search via Tavily (requires TAVILY_API_KEY)."""
    if not settings.TAVILY_API_KEY:
        raise RuntimeError("TAVILY_API_KEY not configured")
    import httpx

    resp = httpx.post(
        "https://api.tavily.com/search",
        json={
            "api_key": settings.TAVILY_API_KEY,
            "query": query,
            "max_results": max_results,
        },
        timeout=settings.WEB_SEARCH_TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()
    return [
        {
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "snippet": r.get("content") or "",
            "published": r.get("published_date") or None,
        }
        for r in data.get("results", [])
    ]


def _search_serpapi(query: str, max_results: int) -> List[Dict[str, Any]]:
    """Search via SerpAPI (requires SERPAPI_API_KEY)."""
    if not settings.SERPAPI_API_KEY:
        raise RuntimeError("SERPAPI_API_KEY not configured")
    import httpx

    resp = httpx.get(
        "https://serpapi.com/search",
        params={"q": query, "api_key": settings.SERPAPI_API_KEY, "num": max_results},
        timeout=settings.WEB_SEARCH_TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()
    out = []
    for r in data.get("organic_results", [])[:max_results]:
        out.append({
            "title": r.get("title") or "",
            "url": r.get("link") or "",
            "snippet": r.get("snippet") or "",
            "published": r.get("date") or None,
        })
    return out


_PROVIDERS = {
    "duckduckgo": _search_duckduckgo,
    "tavily": _search_tavily,
    "serpapi": _search_serpapi,
}


def web_search(query: str, max_results: int = None) -> Dict[str, Any]:
    """Run a web search using the configured provider.

    Returns {"query", "provider", "results": [...], "error": str|None}.
    Never raises — failures are reported in the ``error`` field.
    """
    query = (query or "").strip()
    if not query:
        return {"query": query, "provider": None, "results": [], "error": "Empty query"}

    provider = (settings.WEB_SEARCH_PROVIDER or "duckduckgo").lower()
    fn = _PROVIDERS.get(provider, _search_duckduckgo)
    n = max_results or settings.WEB_SEARCH_MAX_RESULTS

    try:
        results = fn(query, n)
        return {"query": query, "provider": provider, "results": results, "error": None}
    except Exception as e:
        logger.error(f"Web search failed ({provider}): {e}")
        return {"query": query, "provider": provider, "results": [], "error": str(e)}
