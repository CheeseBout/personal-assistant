from fastapi import APIRouter, Request
from typing import Dict, Any, Optional
import time
import numpy as np

from ..services.rag import RAGEngine
from ..core.config import settings

router = APIRouter(prefix="/api/debug", tags=["debug"])
_rag_engine: Optional[RAGEngine] = None


def get_rag_engine() -> RAGEngine:
    global _rag_engine
    if _rag_engine is None:
        _rag_engine = RAGEngine()
    return _rag_engine


@router.get("/retrieve")
async def debug_retrieve(
    request: Request,
    q: str = "",
    n_results: int = 5,
    threshold: float = None
) -> Dict[str, Any]:
    """Debug endpoint: see retrieved chunks for a query."""
    if not q:
        return {"error": "Query parameter 'q' is required"}

    if threshold is None:
        threshold = settings.RAG_THRESHOLD

    start_time = time.time()
    results = get_rag_engine().vector_store.search(q, n_results)

    processed = []
    for r in results:
        distance = r.get('distance', 1.0)
        similarity = float(np.exp(-distance))

        if similarity >= threshold:
            r_copy = r.copy()
            r_copy['score'] = similarity
            processed.append(r_copy)

    processed.sort(key=lambda x: x['score'], reverse=True)
    retrieval_time = time.time() - start_time

    return {
        "query": q,
        "threshold": threshold,
        "total_raw_results": len(results),
        "total_filtered": len(processed),
        "retrieval_time_ms": round(retrieval_time * 1000, 2),
        "results": processed[:n_results]
    }
