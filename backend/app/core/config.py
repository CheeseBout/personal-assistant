import os
from typing import List, get_args, get_origin

try:
    from pydantic_settings import BaseSettings
    from pydantic_settings.sources import DotEnvSettingsSource, EnvSettingsSource

    def _csv_fallback(value):
        """Split a plain CSV string into a list (for non-JSON list env values)."""
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        raise ValueError("not a CSV string")

    class _CsvDotEnvSource(DotEnvSettingsSource):
        """.env source that accepts plain CSV for List fields (not just JSON)."""

        def decode_complex_value(self, field_name, field, value):
            try:
                return super().decode_complex_value(field_name, field, value)
            except Exception:
                return _csv_fallback(value)

    class _CsvEnvSource(EnvSettingsSource):
        """os.environ source that accepts plain CSV for List fields."""

        def decode_complex_value(self, field_name, field, value):
            try:
                return super().decode_complex_value(field_name, field, value)
            except Exception:
                return _csv_fallback(value)

    _USING_PYDANTIC = True
except ModuleNotFoundError:
    _USING_PYDANTIC = False
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
    # min_results=1: a single strongly-relevant chunk is enough to answer.
    # Tuned via app/eval/rag_eval.py --sweep: min_results=2 structurally refused
    # queries backed by just one relevant chunk (e.g. a fact in one document),
    # because the 2nd-best chunk scored deeply negative under the cross-encoder.
    RAG_MIN_RESULTS: int = 1
    RAG_MAX_RESULTS: int = 5
    # Char-based chunk size kept well under the embedding model's token cap.
    # all-MiniLM-L6-v2 truncates at ~256 word-pieces; ~512 chars (~120-180
    # tokens for VN/EN prose) leaves headroom so the tail isn't silently dropped.
    RAG_CHUNK_SIZE: int = 512
    RAG_CHUNK_OVERLAP: int = 80

    # Phase 2 - Hybrid search + Rerank
    HYBRID_CANDIDATES: int = 20
    RRF_K: int = 60
    RERANK_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    # Cross-encoder logits are unbounded; tuned via app/eval/rag_eval.py --sweep
    # against the 512-char chunking. 1.0 sits above the junk cluster
    # (unanswerable queries scored < 0) and below every relevant query, giving
    # 0/7 false refusals with 2/3 junk refused. The remaining junk case shares
    # heavy vocabulary with a real doc and is caught by the grounding verifier
    # (verify_answer), the second line of defense.
    RERANK_THRESHOLD: float = 1.0
    USE_RERANK: bool = True

    # Phase 2 - Grounding / citation
    CITATION_COVERAGE_MIN: int = 1

    # Agent Core - Intent classifier
    INTENT_CONFIDENCE_MIN: float = 0.6
    INTENT_USE_LLM_FALLBACK: bool = True

    # Phase 4 - Browser automation (Playwright)
    BROWSER_HEADLESS: bool = False               # headed by default for observability + manual 2FA/CAPTCHA
    BROWSER_PROFILE_DIR: str = "../data/browser/profile"
    BROWSER_DOWNLOAD_DIR: str = "../data/browser/downloads"
    BROWSER_SCREENSHOT_DIR: str = "../data/browser/screenshots"
    BROWSER_DOMAIN_ALLOWLIST: str = ""          # comma-separated; "" = allow all
    BROWSER_DOMAIN_BLOCKLIST: str = ""          # comma-separated
    BROWSER_NAV_TIMEOUT_MS: int = 30000
    BROWSER_OP_TIMEOUT_S: int = 45               # sync-bridge wait ceiling per browser op
    BROWSER_DOWNLOAD_TIMEOUT_MS: int = 60000     # how long to wait for a download to start/finish

    # Phase 5 - Google integrations (Gmail first). OAuth Desktop/installed-app flow.
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_TOKEN_PATH: str = "../data/google/token.json"     # local token cache, never sent to LLM/log
    GOOGLE_ATTACHMENT_DIR: str = "../data/google/attachments"
    GOOGLE_SCOPES: List[str] = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/documents",
        "https://www.googleapis.com/auth/spreadsheets",
    ]

    # Phase 7 - Sandbox execution (process-based isolation)
    SANDBOX_DIR: str = "../data/sandbox"
    SANDBOX_DEFAULT_TIMEOUT_S: int = 15          # Mode A short timeout
    SANDBOX_MAX_TIMEOUT_S: int = 120
    SANDBOX_MAX_MEMORY_MB: int = 512
    SANDBOX_MAX_OUTPUT_KB: int = 64
    SANDBOX_ALLOW_NETWORK_DEFAULT: bool = False
    SANDBOX_PIP_CACHE_DIR: str = "../data/sandbox/.pip-cache"

    # Phase 8 - Web search + News + Scheduler
    WEB_SEARCH_PROVIDER: str = "duckduckgo"       # duckduckgo (default, no key) | tavily | serpapi
    WEB_SEARCH_MAX_RESULTS: int = 8
    WEB_SEARCH_TIMEOUT_S: int = 15
    TAVILY_API_KEY: str = ""                       # used only if provider=tavily
    SERPAPI_API_KEY: str = ""                      # used only if provider=serpapi
    SCHEDULER_ENABLED: bool = True
    SCHEDULER_MIN_INTERVAL_S: int = 300            # floor for interval jobs (avoid runaway)
    NEWS_DEFAULT_MAX_SOURCES: int = 5

    # Phase 9 - Desktop perception (read-only: see/read/summarize, never control)
    DESKTOP_CAPTURE_DIR: str = "../data/desktop/captures"
    DESKTOP_ENABLE_OCR: bool = True                # requires pytesseract + Tesseract binary
    DESKTOP_ENABLE_VISION: bool = False            # send screenshot to vision model (privacy: opt-in)
    DESKTOP_MASK_SENSITIVE: bool = True            # redact secret-shaped text from OCR output
    DESKTOP_OCR_MAX_CHARS: int = 8000              # cap OCR text fed to model/stored
    DESKTOP_A11Y_MAX_ELEMENTS: int = 80            # max UI elements from accessibility tree
    DESKTOP_MONITOR_INTERVAL_S: int = 60           # periodic monitor interval (min 30)

    # Phase 10 - Desktop control (click/type/keyboard/mouse). High risk: opt-in + HITL.
    DESKTOP_ENABLE_CONTROL: bool = False           # opt-in: bật điều khiển chuột/phím
    DESKTOP_CONTROL_WINDOW_ALLOWLIST: str = ""     # "" = mọi cửa sổ; CSV tiêu đề nếu giới hạn
    DESKTOP_CONTROL_MOVE_DURATION_S: float = 0.2   # smooth mouse-move duration (pyautogui)
    DESKTOP_CONTROL_VERIFY_DEFAULT: bool = True    # re-observe sau hành động để xác nhận

    # CORS - local-first allowlist (avoid "*" with credentials)
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "null",
    ]

    # Misc / safety
    DEBUG_ENDPOINTS_ENABLED: bool = True   # disable to hide /api/debug/* in prod-like deploys

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"

    if _USING_PYDANTIC:
        @classmethod
        def settings_customise_sources(cls, settings_cls, init_settings,
                                       env_settings, dotenv_settings, file_secret_settings):
            # Swap in CSV-tolerant env sources so List fields accept "a,b,c".
            return (
                init_settings,
                _CsvEnvSource(settings_cls),
                _CsvDotEnvSource(settings_cls),
                file_secret_settings,
            )


settings = Settings()
