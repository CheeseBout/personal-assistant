from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # OpenAI/LLM Configuration
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    DEFAULT_MODEL: str = "gpt-4o"
    MODEL: str = "gpt-4o"

    # Embedding Configuration
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    USE_LOCAL_EMBEDDINGS: bool = True

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///../data/db/agent.db"

    # Vector Store
    VECTOR_STORE_PATH: str = "../data/embeddings"

    # File Storage
    UPLOAD_DIR: str = "../data/uploads"
    MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50MB

    # RAG Settings
    RAG_THRESHOLD: float = 0.5
    RAG_MIN_RESULTS: int = 2
    RAG_MAX_RESULTS: int = 5
    RAG_CHUNK_SIZE: int = 1000
    RAG_CHUNK_OVERLAP: int = 100

    # Phase 2 - Hybrid search + Rerank
    HYBRID_CANDIDATES: int = 20          # top-N candidates from each retriever
    RRF_K: int = 60                      # Reciprocal Rank Fusion constant
    RERANK_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    RERANK_THRESHOLD: float = 0.0        # cross-encoder logit threshold
    USE_RERANK: bool = True

    # Phase 2 - Grounding / citation
    CITATION_COVERAGE_MIN: int = 1       # min cited sources required in answer

    # CORS - local-first allowlist (avoid "*" with credentials)
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "null",  # file:// origin used when opening frontend/index.html directly
    ]

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
