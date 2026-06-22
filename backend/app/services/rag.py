from typing import List, Dict, Any, Optional
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from .embedding import EmbeddingService, VectorStore
from .keyword_index import KeywordIndex
from .reranker import get_reranker
from .document import DocumentParser, Chunker
from ..core.logging_config import logger


class RAGEngine:
    def __init__(self):
        self.embedding_service = EmbeddingService()
        self.vector_store = VectorStore()
        self.keyword_index = KeywordIndex()
        self.chunker = Chunker()

    async def process_document(
        self,
        doc_id: str,
        file_path: str,
        filename: str,
        chunks_data: List[Dict] = None,
        version: int = 1,
    ) -> Dict:
        """Parse, chunk, embed and store document in both vector + keyword indexes."""
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

    def _vector_search(self, query: str, n: int) -> List[Dict]:
        results = self.vector_store.search(query, n_results=n)
        for r in results:
            distance = r.get('distance', 1.0)
            r['vector_score'] = float(np.exp(-distance)) if distance is not None else 0.0
        return results

    def _keyword_search(self, query: str, n: int) -> List[Dict]:
        return self.keyword_index.search(query, n_results=n)

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

    def hybrid_search(self, query: str, candidates: int = None) -> List[Dict]:
        """Vector + keyword retrieval fused with RRF, then cross-encoder rerank."""
        from ..core.config import settings
        if candidates is None:
            candidates = settings.HYBRID_CANDIDATES

        vector = self._vector_search(query, candidates)
        keyword = self._keyword_search(query, candidates)
        fused = self._rrf_fuse(vector, keyword, settings.RRF_K)

        if settings.USE_RERANK:
            fused = get_reranker().rerank(query, fused)
        else:
            for c in fused:
                c['rerank_score'] = c.get('fusion_score', 0.0)
        return fused

    def search(self, query: str, n_results: int = 10) -> List[Dict]:
        """Sync hybrid search (compat)."""
        return self.hybrid_search(query)[:n_results]

    async def search_async(self, query: str, n_results: int = 10) -> List[Dict]:
        return self.hybrid_search(query)[:n_results]

    def search_as_tool(self, query: str, n_results: int = 10) -> List[Dict]:
        """Tool-friendly search: returns simplified results for agent context."""
        results = self.hybrid_search(query)[:n_results]
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
            loc_parts.append(f"sheet {meta['sheet']}")
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
    ) -> Optional[Dict]:
        """Hybrid retrieve → rerank → threshold filter → format citations."""
        from ..core.config import settings

        if threshold is None:
            threshold = settings.RERANK_THRESHOLD
        if min_results is None:
            min_results = settings.RAG_MIN_RESULTS

        results = self.hybrid_search(query)
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

        top_results = filtered[:settings.RAG_MAX_RESULTS]

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
