"""Memory tools (Phase 6) — let the agent persist and recall long-term memory.

Executors follow the standard signature: ``execute(arguments, session_id)``.
The session_id is used only for provenance (where the memory came from); the
store itself is cross-session.
"""

from typing import Any, Dict

from .long_term_memory import LongTermMemoryManager


def memory_save(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Save a long-term memory. Secret-shaped content is refused by the store."""
    content = arguments.get("content", "")
    mem_type = arguments.get("type", "semantic")
    tags = arguments.get("tags")
    return LongTermMemoryManager.get_instance().save(
        content=content,
        mem_type=mem_type,
        source=f"conversation:{session_id}",
        tags=tags,
    )


def memory_search(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Search long-term memory for entries relevant to a query."""
    query = arguments.get("query", "")
    mem_type = arguments.get("type")
    limit = arguments.get("limit", 10)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 10
    results = LongTermMemoryManager.get_instance().search(
        query=query, mem_type=mem_type, limit=limit,
    )
    return {"count": len(results), "results": results}
