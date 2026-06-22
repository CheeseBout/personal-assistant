from pydantic_settings import BaseSettings


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
    VECTOR_STORE_PATH: str = "../data/embeddings/chroma"

    # File Storage
    UPLOAD_DIR: str = "../data/uploads"

    # RAG Settings
    RAG_THRESHOLD: float = 0.5
    RAG_MIN_RESULTS: int = 2
    RAG_MAX_RESULTS: int = 5
    RAG_CHUNK_SIZE: int = 1000
    RAG_CHUNK_OVERLAP: int = 100

    # CORS
    CORS_ORIGINS: list = ["http://localhost:3000", "http://localhost:5173"]

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
