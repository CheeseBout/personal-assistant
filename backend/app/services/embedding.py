import os
from typing import List, Dict, Optional

from ..core.logging_config import logger


class EmbeddingService:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        try:
            from sentence_transformers import SentenceTransformer
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "sentence-transformers is required for document embedding. "
                "Install backend/requirements.txt before uploading documents."
            ) from exc

        logger.info(f"Loading embedding model: {model_name}...")
        self.model = SentenceTransformer(model_name)
        self.embedding_dim = self.model.get_sentence_embedding_dimension()
        logger.info(f"Embedding model loaded. Dimension: {self.embedding_dim}")

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for list of texts."""
        if not texts:
            return []
        embeddings = self.model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        return embeddings.tolist()

    def embed_single(self, text: str) -> List[float]:
        """Generate embedding for single text."""
        return self.model.encode(text, convert_to_numpy=True).tolist()


class VectorStore:
    def __init__(self, persist_dir: str = None, embedding_service: Optional[EmbeddingService] = None):
        """Vector store backed by ChromaDB.

        ``embedding_service`` MUST be the same instance used for indexing.
        Otherwise query-time and index-time embeddings come from different
        models and retrieval quality collapses silently.
        """
        try:
            import chromadb
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "chromadb is required for vector search. Install backend/requirements.txt before uploading documents."
            ) from exc

        if persist_dir is None:
            from ..core.config import settings
            persist_dir = settings.VECTOR_STORE_PATH
        os.makedirs(persist_dir, exist_ok=True)
        self.persist_dir = persist_dir
        self.embedding_service = embedding_service
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection("documents")
        logger.info(f"Vector store initialized at: {persist_dir}")

    def add_documents(self, doc_id: str, chunks: List[Dict], embeddings: List[List[float]], version: int = 1):
        """Add document chunks to vector store."""
        if not chunks or not embeddings:
            return

        ids = [f"{doc_id}_v{version}_{c['index']}" for c in chunks]
        texts = [c['content'] for c in chunks]
        metadatas = []
        for c in chunks:
            md = {
                "doc_id": doc_id,
                "version": version,
                "chunk_index": c['index'],
                "start": c.get('start_char', 0),
                "end": c.get('end_char', 0),
            }
            for k, v in (c.get('meta') or {}).items():
                if v is not None:
                    md[k] = v
            metadatas.append(md)

        self.collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas
        )
        logger.info(f"Added {len(chunks)} chunks to vector store for doc {doc_id} v{version}")

    def search(self, query: str, n_results: int = 5, where: Dict = None) -> List[Dict]:
        """Search similar chunks, optionally filtered by metadata.

        Embeds the query with the SAME EmbeddingService used at index time, so
        index- and query-side embeddings come from the same model.
        """
        if self.collection.count() == 0:
            return []

        if self.embedding_service is None:
            raise RuntimeError(
                "VectorStore.search requires an embedding_service; "
                "construct VectorStore(embedding_service=...) so query and index "
                "embeddings come from the same model."
            )
        query_embedding = self.embedding_service.embed_single(query)

        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=min(n_results, self.collection.count()),
            where=where or None,
        )

        formatted = []
        for i in range(len(results['ids'][0])):
            formatted.append({
                "id": results['ids'][0][i],
                "content": results['documents'][0][i],
                "metadata": results['metadatas'][0][i],
                "distance": results['distances'][0][i] if 'distances' in results else None
            })

        return formatted

    def count_by_doc_id(self, doc_id: str) -> int:
        """Return number of stored vectors for a document."""
        results = self.collection.get(where={"doc_id": doc_id})
        return len(results['ids']) if results and results.get('ids') else 0

    def delete_by_doc_id(self, doc_id: str):
        """Delete all chunks for a document."""
        results = self.collection.get(where={"doc_id": doc_id})
        if results['ids']:
            self.collection.delete(ids=results['ids'])
            logger.info(f"Deleted {len(results['ids'])} chunks for doc {doc_id}")

    def clear_all(self):
        """Clear all documents from collection."""
        self.collection.delete(where={})
        logger.info("Cleared all documents from vector store")
