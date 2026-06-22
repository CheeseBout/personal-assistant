from sentence_transformers import SentenceTransformer
import chromadb
from chromadb.config import Settings
import os
from typing import List, Dict, Any
import numpy as np
from pathlib import Path

class EmbeddingService:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        print(f"Loading embedding model: {model_name}...")
        self.model = SentenceTransformer(model_name)
        self.embedding_dim = self.model.get_sentence_embedding_dimension()
        print(f"Model loaded. Embedding dimension: {self.embedding_dim}")

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for list of texts"""
        if not texts:
            return []
        embeddings = self.model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        return embeddings.tolist()

    def embed_single(self, text: str) -> List[float]:
        """Generate embedding for single text"""
        return self.model.encode(text, convert_to_numpy=True).tolist()

class VectorStore:
    def __init__(self, persist_dir: str = None):
        if persist_dir is None:
            from ..core.config import settings
            persist_dir = settings.VECTOR_STORE_PATH
        os.makedirs(persist_dir, exist_ok=True)
        self.persist_dir = persist_dir
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection("documents")
        print(f"Vector store initialized at: {persist_dir}")

    def add_documents(self, doc_id: str, chunks: List[Dict], embeddings: List[List[float]], version: int = 1):
        """Add document chunks to vector store"""
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
            # Flatten structural metadata (page / sheet / heading / section)
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
        print(f"Added {len(chunks)} chunks to vector store for doc {doc_id} v{version}")

    def search(self, query: str, n_results: int = 5, where: Dict = None) -> List[Dict]:
        """Search similar chunks, optionally filtered by metadata (e.g. current version)."""
        if self.collection.count() == 0:
            return []

        results = self.collection.query(
            query_texts=[query],
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
        """Return number of stored vectors for a document (used to verify deletion)."""
        results = self.collection.get(where={"doc_id": doc_id})
        return len(results['ids']) if results and results.get('ids') else 0

    def delete_by_doc_id(self, doc_id: str):
        """Delete all chunks for a document"""
        results = self.collection.get(where={"doc_id": doc_id})
        if results['ids']:
            self.collection.delete(ids=results['ids'])
            print(f"Deleted {len(results['ids'])} chunks for doc {doc_id}")

    def clear_all(self):
        """Clear all documents from collection"""
        self.collection.delete(where={})
        print("Cleared all documents from vector store")
