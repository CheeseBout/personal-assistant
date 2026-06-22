"""RAG Tool executor — wraps RAGEngine.search_as_tool for the tool registry."""

from typing import Dict, Any

from .rag import RAGEngine
from ..core.logging_config import logger

_rag_engine = None


def get_rag_engine() -> RAGEngine:
    global _rag_engine
    if _rag_engine is None:
        _rag_engine = RAGEngine()
    return _rag_engine


def execute_rag_search(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Tool executor for rag.search.

    Arguments:
        query (required): search query
        n_results (optional): max results, default 10

    Returns:
        {"results": [...], "count": int}
    """
    query = arguments.get("query")
    if not query or not isinstance(query, str) or not query.strip():
        return {"error": "Missing or invalid 'query' parameter"}

    n_results = arguments.get("n_results", 10)
    try:
        n_results = int(n_results)
        if n_results <= 0:
            n_results = 10
    except (ValueError, TypeError):
        n_results = 10

    try:
        results = get_rag_engine().search_as_tool(query, n_results=n_results)
        return {"results": results, "count": len(results), "query": query}
    except Exception as e:
        logger.error(f"rag.search tool error: {e}")
        return {"error": str(e)}
