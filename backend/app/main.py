from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .core.logging_config import setup_logging, logger

setup_logging()

from .api.upload import router as upload_router
from .api.chat import router as chat_router
from .api.debug import router as debug_router
from .api.agent import router as agent_router
from .api.google import router as google_router
from .api.settings import router as settings_router
from .core.config import settings

app = FastAPI(title="Local RAG Agent", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    from .models.database import init_db
    from .models.migration_agent_core import run_migration
    from .models.migration_browser import run_migration as run_browser_migration
    from .models.migration_google import run_migration as run_google_migration
    from .models.migration_google_workspace import run_migration as run_google_workspace_migration
    from .models.migration_sandbox import run_migration as run_sandbox_migration
    from .models.migration_memory import run_migration as run_memory_migration
    from .models.migration_news import run_migration as run_news_migration
    from .models.migration_desktop import run_migration as run_desktop_migration
    from .models.migration_desktop_control import run_migration as run_desktop_control_migration
    init_db()
    run_migration()
    run_browser_migration()
    run_google_migration()
    run_google_workspace_migration()
    run_sandbox_migration()
    run_memory_migration()
    run_news_migration()
    run_desktop_migration()
    run_desktop_control_migration()
    # Initialize agent core components after default tools are seeded.
    from .services.tool_registry import ToolRegistry
    ToolRegistry.get_instance().initialize()
    # Load any persisted RAG/runtime settings overrides from the DB.
    from .services.settings_manager import SettingsManager
    SettingsManager.get_instance().load()
    # Eagerly construct the RAG engine so the heavy embedding/rerank models
    # are loaded at startup rather than on the first user request. The
    # singleton lives in services.rag_singleton, so every later get_rag_engine
    # call reuses this instance.
    from .services.rag_singleton import get_rag_engine
    get_rag_engine()
    # Start the background scheduler (Phase 8). Degrades gracefully if APScheduler is absent.
    from .services.scheduler import SchedulerManager
    SchedulerManager.get_instance().start()
    logger.info("Database initialized, ToolRegistry loaded, RAG engine ready")
    from pathlib import Path
    logger.info(f"Vector store path: {Path(settings.VECTOR_STORE_PATH).resolve()}")


@app.on_event("shutdown")
async def shutdown_event():
    from .services.scheduler import SchedulerManager
    SchedulerManager.get_instance().shutdown()


@app.get("/")
async def root():
    return {"message": "Local RAG Agent API", "version": "0.1.0"}


app.include_router(upload_router)
app.include_router(chat_router)
app.include_router(debug_router)
app.include_router(agent_router)
app.include_router(google_router)
app.include_router(settings_router)


@app.get("/api/health")
async def health_check():
    from uuid import uuid4
    return {"status": "healthy", "timestamp": str(uuid4())}
