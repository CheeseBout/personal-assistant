from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import time

from ..services.rag_singleton import get_rag_engine
from ..services.reranker import get_reranker
from ..services.settings_manager import SettingsManager
from ..core.config import settings

router = APIRouter(prefix="/api/debug", tags=["debug"])


def _require_debug_enabled() -> None:
    if not settings.DEBUG_ENDPOINTS_ENABLED:
        raise HTTPException(status_code=404, detail="Debug endpoints are disabled")


@router.get("/retrieve")
async def debug_retrieve(
    q: str = "",
    n_results: int = 10,
) -> Dict[str, Any]:
    """Debug: expose vector / keyword / fusion / rerank stages for a query."""
    _require_debug_enabled()
    if not q:
        return {"error": "Query parameter 'q' is required"}

    engine = get_rag_engine()
    rag_cfg = SettingsManager.get_instance().get_rag_settings()
    candidates = rag_cfg.get("hybrid_candidates", settings.HYBRID_CANDIDATES)
    rrf_k = rag_cfg.get("rrf_k", settings.RRF_K)
    use_rerank = rag_cfg.get("use_rerank", settings.USE_RERANK)

    start = time.time()
    vector = engine._vector_search(q, candidates)
    keyword = engine._keyword_search(q, candidates)
    fused = engine._rrf_fuse(vector, keyword, rrf_k)

    from ..services.reranker import get_reranker
    reranked = get_reranker().rerank(q, [dict(f) for f in fused]) if use_rerank else fused
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
        "use_rerank": use_rerank,
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
    """Expose effective Phase 2 RAG settings (with runtime overrides applied)."""
    _require_debug_enabled()
    rag_cfg = SettingsManager.get_instance().get_rag_settings()
    return {
        "hybrid_candidates": rag_cfg.get("hybrid_candidates", settings.HYBRID_CANDIDATES),
        "rrf_k": rag_cfg.get("rrf_k", settings.RRF_K),
        "rerank_model": settings.RERANK_MODEL,
        "rerank_threshold": rag_cfg.get("rerank_threshold", settings.RERANK_THRESHOLD),
        "use_rerank": rag_cfg.get("use_rerank", settings.USE_RERANK),
        "rag_min_results": rag_cfg.get("min_results", settings.RAG_MIN_RESULTS),
        "rag_max_results": rag_cfg.get("max_results", settings.RAG_MAX_RESULTS),
        "citation_coverage_min": rag_cfg.get("citation_coverage_min", settings.CITATION_COVERAGE_MIN),
        "vector_store_path": settings.VECTOR_STORE_PATH,
        "reranker_available": get_reranker().available,
    }
