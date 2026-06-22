from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .core.logging_config import setup_logging

setup_logging()

from .api.upload import router as upload_router
from .api.chat import router as chat_router
from .api.debug import router as debug_router

app = FastAPI(title="Local RAG Agent", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    from .models.database import init_db
    init_db()
    print("Database initialized")


@app.get("/")
async def root():
    return {"message": "Local RAG Agent API", "version": "0.1.0"}


app.include_router(upload_router)
app.include_router(chat_router)
app.include_router(debug_router)


@app.get("/api/health")
async def health_check():
    from uuid import uuid4
    return {"status": "healthy", "timestamp": str(uuid4())}
