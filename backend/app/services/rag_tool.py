"""RAG Tool executor — wraps RAGEngine.search_as_tool for the tool registry."""

from typing import Dict, Any

from .rag_singleton import get_rag_engine
from ..core.logging_config import logger


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

    # Optional single/multi-file scope (§10.4)
    doc_ids = arguments.get("doc_ids")
    if doc_ids is None and arguments.get("doc_id"):
        doc_ids = [arguments["doc_id"]]
    if doc_ids is not None and not isinstance(doc_ids, list):
        doc_ids = [doc_ids]

    try:
        results = get_rag_engine().search_as_tool(query, n_results=n_results, doc_ids=doc_ids)
        return {"results": results, "count": len(results), "query": query}
    except Exception as e:
        logger.error(f"rag.search tool error: {e}")
        return {"error": str(e)}
