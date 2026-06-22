from fastapi import APIRouter
from typing import Dict, Any, Optional
import time

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
    q: str = "",
    n_results: int = 10,
) -> Dict[str, Any]:
    """Debug: expose vector / keyword / fusion / rerank stages for a query."""
    if not q:
        return {"error": "Query parameter 'q' is required"}

    engine = get_rag_engine()

    start = time.time()
    vector = engine._vector_search(q, settings.HYBRID_CANDIDATES)
    keyword = engine._keyword_search(q, settings.HYBRID_CANDIDATES)
    fused = engine._rrf_fuse(vector, keyword, settings.RRF_K)

    from ..services.reranker import get_reranker
    reranked = get_reranker().rerank(q, [dict(f) for f in fused]) if settings.USE_RERANK else fused
    elapsed = time.time() - start

    def trim(items, keys):
        out = []
        for it in items:
            out.append({k: it.get(k) for k in keys if k in it} | {
                "doc_id": (it.get("metadata") or {}).get("doc_id"),
                "preview": (it.get("content") or "")[:120],
            })
        return out

    return {
        "query": q,
        "timing_ms": round(elapsed * 1000, 2),
        "use_rerank": settings.USE_RERANK,
        "rerank_available": get_reranker().available,
        "counts": {
            "vector": len(vector),
            "keyword": len(keyword),
            "fused": len(fused),
        },
        "vector_top": trim(vector[:n_results], ["id", "vector_score", "distance"]),
        "keyword_top": trim(keyword[:n_results], ["id", "bm25"]),
        "reranked_top": trim(reranked[:n_results], ["id", "fusion_score", "rerank_score"]),
    }


@router.get("/settings")
async def debug_settings() -> Dict[str, Any]:
    """Expose effective Phase 2 RAG settings."""
    return {
        "hybrid_candidates": settings.HYBRID_CANDIDATES,
        "rrf_k": settings.RRF_K,
        "rerank_model": settings.RERANK_MODEL,
        "rerank_threshold": settings.RERANK_THRESHOLD,
        "use_rerank": settings.USE_RERANK,
        "rag_min_results": settings.RAG_MIN_RESULTS,
        "rag_max_results": settings.RAG_MAX_RESULTS,
        "citation_coverage_min": settings.CITATION_COVERAGE_MIN,
        "vector_store_path": settings.VECTOR_STORE_PATH,
    }
