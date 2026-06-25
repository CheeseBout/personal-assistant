import os
from typing import List, get_args, get_origin

try:
    from pydantic_settings import BaseSettings
except ModuleNotFoundError:
    class BaseSettings:
        """Small env-based fallback when pydantic-settings is not installed."""

        def __init__(self):
            annotations = getattr(self.__class__, "__annotations__", {})
            for name, annotation in annotations.items():
                default = getattr(self.__class__, name, None)
                value = os.getenv(name)
                if value is None:
                    setattr(self, name, default)
                    continue
                setattr(self, name, self._coerce(value, annotation, default))

        @staticmethod
        def _coerce(value, annotation, default):
            if annotation is bool:
                return value.lower() in ("1", "true", "yes", "on")
            if annotation is int:
                return int(value)
            if annotation is float:
                return float(value)
            if get_origin(annotation) is list or annotation is List[str]:
                return [item.strip() for item in value.split(",") if item.strip()]
            return value


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
    MAX_FILE_SIZE: int = 50 * 1024 * 1024

    # RAG Settings
    RAG_THRESHOLD: float = 0.5
    RAG_MIN_RESULTS: int = 2
    RAG_MAX_RESULTS: int = 5
    RAG_CHUNK_SIZE: int = 1000
    RAG_CHUNK_OVERLAP: int = 100

    # Phase 2 - Hybrid search + Rerank
    HYBRID_CANDIDATES: int = 20
    RRF_K: int = 60
    RERANK_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    RERANK_THRESHOLD: float = 0.0
    USE_RERANK: bool = True

    # Phase 2 - Grounding / citation
    CITATION_COVERAGE_MIN: int = 1

    # Agent Core - Intent classifier
    INTENT_CONFIDENCE_MIN: float = 0.6
    INTENT_USE_LLM_FALLBACK: bool = True

    # CORS - local-first allowlist (avoid "*" with credentials)
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "null",
    ]

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
