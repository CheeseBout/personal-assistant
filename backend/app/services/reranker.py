"""Cross-encoder reranker (lazy-loaded, singleton)."""

from typing import List, Dict
from ..core.logging_config import logger

_reranker = None


def get_reranker():
    global _reranker
    if _reranker is None:
        _reranker = Reranker()
    return _reranker


class Reranker:
    def __init__(self, model_name: str = None):
        from ..core.config import settings
        model_name = model_name or settings.RERANK_MODEL
        self.model = None
        self.model_name = model_name
        try:
            from sentence_transformers import CrossEncoder
            logger.info(f"Loading cross-encoder reranker: {model_name}...")
            self.model = CrossEncoder(model_name)
            logger.info("Reranker loaded.")
        except Exception as e:
            logger.error(f"Failed to load reranker, falling back to fusion order: {e}")
            self.model = None

    @property
    def available(self) -> bool:
        return self.model is not None

    def rerank(self, query: str, candidates: List[Dict], top_k: int = None) -> List[Dict]:
        """Score candidates with the cross-encoder and sort desc.

        Each candidate must have 'content'. Adds 'rerank_score'. Falls back to
        existing order if the model is unavailable.
        """
        if not candidates:
            return []
        if not self.available:
            for c in candidates:
                c.setdefault("rerank_score", c.get("fusion_score", 0.0))
            return candidates[:top_k] if top_k else candidates

        pairs = [(query, c["content"]) for c in candidates]
        scores = self.model.predict(pairs)
        for c, s in zip(candidates, scores):
            c["rerank_score"] = float(s)
        candidates.sort(key=lambda x: x["rerank_score"], reverse=True)
        return candidates[:top_k] if top_k else candidates
