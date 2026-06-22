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
    def __init__(self, persist_dir: str = "../data/embeddings"):
        os.makedirs(persist_dir, exist_ok=True)
        self.persist_dir = persist_dir
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection("documents")
        print(f"Vector store initialized at: {persist_dir}")

    def add_documents(self, doc_id: str, chunks: List[Dict], embeddings: List[List[float]]):
        """Add document chunks to vector store"""
        if not chunks or not embeddings:
            return

        ids = [f"{doc_id}_{c['index']}" for c in chunks]
        texts = [c['content'] for c in chunks]
        metadatas = [
            {"doc_id": doc_id, "chunk_index": c['index'], "start": c['start_char'], "end": c['end_char']}
            for c in chunks
        ]

        self.collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas
        )
        print(f"Added {len(chunks)} chunks to vector store for doc {doc_id}")

    def search(self, query: str, n_results: int = 5) -> List[Dict]:
        """Search similar chunks"""
        if self.collection.count() == 0:
            return []

        results = self.collection.query(
            query_texts=[query],
            n_results=min(n_results, self.collection.count())
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
