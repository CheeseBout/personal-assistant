"""News + web search tools (Phase 8).

Executors follow the standard signature: ``execute(arguments, session_id)``.
- web.search: raw web search results (read-only, low risk).
- news.summarize: multi-source search + grounded summary with source links.
"""

from typing import Any, Dict

from .web_search import web_search as _web_search
from .news_service import generate_report
from ..core.config import settings


def web_search_tool(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Run a web search and return normalized results with links."""
    query = arguments.get("query", "")
    max_results = arguments.get("max_results")
    try:
        max_results = int(max_results) if max_results is not None else None
    except (TypeError, ValueError):
        max_results = None
    return _web_search(query, max_results=max_results)


def news_summarize_tool(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Search multiple sources and produce a grounded summary with citations."""
    query = arguments.get("query", "")
    max_sources = arguments.get("max_sources")
    try:
        max_sources = int(max_sources) if max_sources is not None else None
    except (TypeError, ValueError):
        max_sources = None
    return generate_report(query, max_sources=max_sources)
