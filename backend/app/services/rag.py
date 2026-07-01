from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor
import asyncio
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from .embedding import EmbeddingService, VectorStore
from .keyword_index import KeywordIndex
from .reranker import get_reranker
from .document import DocumentParser, Chunker
from ..core.logging_config import logger


# Two-worker pool dedicated to running vector and keyword search in parallel.
# Both call paths are blocking I/O / native code, so a thread pool gives a
# real speedup. Module-level so it lives for the process lifetime.
_search_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rag-search")


class RAGEngine:
    def __init__(self, persist_dir: str = None, keyword_db_path: str = None):
        """RAG engine over a vector store + keyword index.

        ``persist_dir`` / ``keyword_db_path`` default to the production paths;
        the eval harness passes temp paths to build an isolated index without
        touching real data.
        """
        self.embedding_service = EmbeddingService()
        self.vector_store = VectorStore(persist_dir=persist_dir, embedding_service=self.embedding_service)
        self.keyword_index = KeywordIndex(db_path=keyword_db_path)
        self.chunker = Chunker()

    async def process_document(
        self,
        doc_id: str,
        file_path: str,
        filename: str,
        chunks_data: List[Dict] = None,
        version: int = 1,
    ) -> Dict:
        """Parse, chunk, embed and store document in both vector + keyword indexes.

        The heavy work (parsing, embedding, vector/keyword insertion) runs in a
        background thread via ``asyncio.to_thread`` so the event loop stays
        responsive during large uploads.
        """
        return await asyncio.to_thread(
            self.process_document_sync, doc_id, file_path, filename, chunks_data, version
        )

    def process_document_sync(
        self,
        doc_id: str,
        file_path: str,
        filename: str,
        chunks_data: List[Dict] = None,
        version: int = 1,
    ) -> Dict:
        """Synchronous variant for callers that already run outside the event loop
        (e.g. the APScheduler-based auto-reindex service).
        """
        if chunks_data is None:
            segments = DocumentParser.parse_segments(file_path)
            chunks_data = Chunker.chunk_segments(segments)

        texts = [c['content'] for c in chunks_data]
        embeddings = self.embedding_service.embed_texts(texts)

        self.vector_store.add_documents(doc_id, chunks_data, embeddings, version=version)
        self.keyword_index.add_chunks(doc_id, chunks_data, version=version)

        return {
            "doc_id": doc_id,
            "filename": filename,
            "version": version,
            "chunk_count": len(chunks_data),
            "total_chars": sum(len(c['content']) for c in chunks_data),
        }

    # ---- Retrieval -------------------------------------------------------

    def _vector_search(self, query: str, n: int, doc_ids: list = None) -> List[Dict]:
        where = {"doc_id": {"$in": doc_ids}} if doc_ids else None
        results = self.vector_store.search(query, n_results=n, where=where)
        for r in results:
            distance = r.get('distance', 1.0)
            r['vector_score'] = float(np.exp(-distance)) if distance is not None else 0.0
        return results

    def _keyword_search(self, query: str, n: int, doc_ids: list = None) -> List[Dict]:
        return self.keyword_index.search(query, n_results=n, doc_ids=doc_ids)

    @staticmethod
    def _rrf_fuse(vector: List[Dict], keyword: List[Dict], k: int) -> List[Dict]:
        """Reciprocal Rank Fusion over two ranked lists keyed by chunk id."""
        scores: Dict[str, float] = {}
        store: Dict[str, Dict] = {}

        for rank, r in enumerate(vector):
            cid = r['id']
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            store.setdefault(cid, r)

        for rank, r in enumerate(keyword):
            cid = r['id']
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            # Prefer the vector entry (richer metadata) if present
            store.setdefault(cid, r)

        fused = []
        for cid, score in scores.items():
            item = dict(store[cid])
            item['fusion_score'] = score
            fused.append(item)
        fused.sort(key=lambda x: x['fusion_score'], reverse=True)
        return fused

    def hybrid_search(self, query: str, candidates: int = None, doc_ids: list = None) -> List[Dict]:
        """Vector + keyword retrieval fused with RRF, then cross-encoder rerank.

        ``doc_ids``: if provided, restrict results to these document IDs
        (single-file scope per §10.4).

        Vector and keyword searches run in parallel via a small thread pool —
        both are blocking calls (numpy / native sqlite) so threads give a real
        speedup. The reranker stays serial since it usually dominates wall
        time and benefits less from concurrency at this scale.
        """
        from ..core.config import settings
        from .settings_manager import SettingsManager
        rag_cfg = SettingsManager.get_instance().get_rag_settings()
        if candidates is None:
            candidates = rag_cfg.get("hybrid_candidates", settings.HYBRID_CANDIDATES)
        use_rerank = rag_cfg.get("use_rerank", settings.USE_RERANK)
        rrf_k = rag_cfg.get("rrf_k", settings.RRF_K)

        vec_future = _search_pool.submit(self._vector_search, query, candidates, doc_ids)
        kw_future = _search_pool.submit(self._keyword_search, query, candidates, doc_ids)
        try:
            vector = vec_future.result()
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            vector = []
        try:
            keyword = kw_future.result()
        except Exception as e:
            logger.error(f"Keyword search failed: {e}")
            keyword = []

        fused = self._rrf_fuse(vector, keyword, rrf_k)

        if use_rerank:
            fused = get_reranker().rerank(query, fused)
        else:
            for c in fused:
                c['rerank_score'] = c.get('fusion_score', 0.0)
        return fused

    def search(self, query: str, n_results: int = 10, doc_ids: list = None) -> List[Dict]:
        """Sync hybrid search (compat)."""
        return self.hybrid_search(query, doc_ids=doc_ids)[:n_results]

    async def search_async(self, query: str, n_results: int = 10, doc_ids: list = None) -> List[Dict]:
        return self.hybrid_search(query, doc_ids=doc_ids)[:n_results]

    def search_as_tool(self, query: str, n_results: int = 10, doc_ids: list = None) -> List[Dict]:
        """Tool-friendly search: returns simplified results for agent context."""
        results = self.hybrid_search(query, doc_ids=doc_ids)[:n_results]
        simplified = []
        for r in results:
            meta = r.get('metadata', {})
            score = r.get('rerank_score', r.get('fusion_score', 0.0))
            simplified.append({
                "content": r.get('content', '')[:500],  # Preview only
                "score": round(score, 3),
                "filename": meta.get('doc_id', 'unknown'),
                "page": meta.get('page'),
                "sheet": meta.get('sheet'),
                "heading": meta.get('heading'),
                "chunk_index": meta.get('chunk_index'),
            })
        return simplified

    def calculate_relevance_score(self, query_embedding: List[float], chunk_embedding: List[float]) -> float:
        q = np.array(query_embedding)
        c = np.array(chunk_embedding)
        norm_q = np.linalg.norm(q)
        norm_c = np.linalg.norm(c)
        if norm_q == 0 or norm_c == 0:
            return 0.0
        return float(np.dot(q, c) / (norm_q * norm_c))

    @staticmethod
    def _format_citation(filename: str, meta: Dict, version: int) -> str:
        loc_parts = []
        if meta.get('page') is not None:
            loc_parts.append(f"trang {meta['page']}")
        if meta.get('sheet'):
            sheet_part = f"sheet {meta['sheet']}"
            if meta.get('row_start') and meta.get('row_end'):
                sheet_part += f" (hàng {meta['row_start']}-{meta['row_end']})"
            loc_parts.append(sheet_part)
        if meta.get('heading'):
            loc_parts.append(f"mục \"{meta['heading']}\"")
        loc_parts.append(f"chunk {meta.get('chunk_index', '?')}")
        version_part = f", version {version}" if version and version > 1 else ""
        return f"{filename}{version_part} ({', '.join(loc_parts)})"

    async def retrieve_and_rerank(
        self,
        query: str,
        db: AsyncSession,
        threshold: float = None,
        min_results: int = None,
        doc_ids: list = None,
    ) -> Optional[Dict]:
        """Hybrid retrieve → rerank → threshold filter → format citations.

        ``doc_ids``: restrict retrieval to specific documents (single-file scope).
        """
        from ..core.config import settings
        from .settings_manager import SettingsManager
        rag_cfg = SettingsManager.get_instance().get_rag_settings()

        if threshold is None:
            threshold = rag_cfg.get("rerank_threshold", settings.RERANK_THRESHOLD)
        if min_results is None:
            min_results = rag_cfg.get("min_results", settings.RAG_MIN_RESULTS)
        max_results = rag_cfg.get("max_results", settings.RAG_MAX_RESULTS)

        results = self.hybrid_search(query, doc_ids=doc_ids)
        if not results:
            logger.debug(f"No retrieval results for query: {query[:50]}...")
            return None

        # Resolve filenames for documents in the candidate set
        doc_ids = list({r['metadata'].get('doc_id') for r in results if r.get('metadata')})
        from ..models.async_db import Document
        stmt = Document.__table__.select().where(Document.id.in_(doc_ids))
        result = await db.execute(stmt)
        docs = result.fetchall()
        doc_map = {doc.id: doc.filename for doc in docs}

        # Filter by rerank threshold
        filtered = []
        for r in results:
            score = r.get('rerank_score', r.get('fusion_score', 0.0))
            if score >= threshold:
                rc = dict(r)
                rc['score'] = score
                rc['filename'] = doc_map.get(r['metadata'].get('doc_id'), r['metadata'].get('doc_id'))
                filtered.append(rc)

        if len(filtered) < min_results:
            logger.debug(f"Insufficient results after threshold: {len(filtered)} < {min_results}")
            return None

        top_results = filtered[:max_results]

        context_parts = []
        sources = []
        for r in top_results:
            meta = r['metadata']
            version = meta.get('version', 1)
            citation = self._format_citation(r['filename'], meta, version)
            context_parts.append(f"[Nguồn: {citation}]\n{r['content']}")
            sources.append({
                "filename": r['filename'],
                "doc_id": meta.get('doc_id'),
                "chunk_index": meta.get('chunk_index'),
                "page": meta.get('page'),
                "sheet": meta.get('sheet'),
                "heading": meta.get('heading'),
                "version": version,
                "score": r['score'],
                "citation": citation,
            })

        logger.info(f"Retrieved {len(top_results)} chunks for query: {query[:50]}...")

        return {
            "context": "\n\n".join(context_parts),
            "sources": sources,
            "chunks": [{"content": r['content'], "citation": s["citation"]} for r, s in zip(top_results, sources)],
        }
