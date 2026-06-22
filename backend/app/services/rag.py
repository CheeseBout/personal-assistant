from typing import List, Dict, Any, Optional
import numpy as np
from sqlalchemy.orm import Session
from .embedding import EmbeddingService, VectorStore
from .document import DocumentParser, Chunker
from ..models.database import Document, get_db

class RAGEngine:
    def __init__(self):
        self.embedding_service = EmbeddingService()
        self.vector_store = VectorStore()
        self.chunker = Chunker()

    async def process_document(self, doc_id: str, file_path: str, filename: str) -> Dict:
        """Parse, chunk, embed and store document"""
        # Parse
        text = DocumentParser.parse_file(file_path)

        # Chunk
        chunks = Chunker.chunk_text(text)

        # Embed
        texts = [c['content'] for c in chunks]
        embeddings = self.embedding_service.embed_texts(texts)

        # Store in vector DB
        self.vector_store.add_documents(doc_id, chunks, embeddings)

        return {
            "doc_id": doc_id,
            "filename": filename,
            "chunk_count": len(chunks),
            "total_chars": len(text)
        }

    def search(self, query: str, n_results: int = 10) -> List[Dict]:
        """Search for relevant chunks"""
        results = self.vector_store.search(query, n_results)
        return results

    def calculate_relevance_score(self, query_embedding: List[float], chunk_embedding: List[float]) -> float:
        """Calculate cosine similarity"""
        q = np.array(query_embedding)
        c = np.array(chunk_embedding)
        norm_q = np.linalg.norm(q)
        norm_c = np.linalg.norm(c)
        if norm_q == 0 or norm_c == 0:
            return 0.0
        return float(np.dot(q, c) / (norm_q * norm_c))

    async def retrieve_and_rerank(
        self,
        query: str,
        db: Session,
        threshold: float = 0.5,
        min_results: int = 2
    ) -> Optional[Dict]:
        """Retrieve, filter by threshold, and format context with citations"""
        results = self.search(query, n_results=10)

        if not results:
            return None

        # Get document info from DB
        doc_map = {}
        docs = db.query(Document).filter(Document.id.in_([r['metadata']['doc_id'] for r in results])).all()
        for doc in docs:
            doc_map[doc.id] = doc.filename

        # For ChromaDB, distance is already a similarity-like score (lower = more similar)
        # Chroma uses L2 distance by default. We'll use the distance directly with an inverted threshold
        filtered = []

        for r in results:
            doc_id = r['metadata']['doc_id']
            filename = doc_map.get(doc_id, doc_id)

            # Use distance from Chroma (lower is better)
            # For L2 distance, typical range is 0 to ~2 for cosine-like embeddings
            distance = r.get('distance', 1.0)  # Default to max distance if not present

            # Convert distance to similarity score (0-1, higher is better)
            # L2 distance can be > 1, so we use exponential decay
            similarity = float(np.exp(-distance))

            if similarity >= threshold:
                r_copy = r.copy()
                r_copy['score'] = similarity
                r_copy['filename'] = filename
                filtered.append(r_copy)

        # Check minimum evidence
        if len(filtered) < min_results:
            return None

        # Sort by score (descending)
        filtered.sort(key=lambda x: x['score'], reverse=True)

        # Take top results (max 5 as per config)
        top_results = filtered[:5]

        # Format context for LLM
        context_parts = []
        for r in top_results:
            citation = f"{r['filename']} (chunk {r['metadata']['chunk_index']})"
            context_parts.append(f"[Source: {citation}]\n{r['content']}")

        return {
            "context": "\n\n".join(context_parts),
            "sources": [
                {
                    "filename": r['filename'],
                    "doc_id": r['metadata']['doc_id'],
                    "chunk_index": r['metadata']['chunk_index'],
                    "score": r['score']
                }
                for r in top_results
            ]
        }

