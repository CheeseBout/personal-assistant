"""RAG settings API — get and patch runtime-mutable retrieval settings."""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from ..services.settings_manager import SettingsManager
from ..core.logging_config import logger

router = APIRouter(prefix="/api/rag", tags=["settings"])


@router.get("/settings")
async def get_rag_settings() -> Dict[str, Any]:
    """Return the effective RAG settings (env defaults overlaid with DB overrides)."""
    return SettingsManager.get_instance().get_rag_settings()


@router.patch("/settings")
async def update_rag_settings(request: dict) -> Dict[str, Any]:
    """Update one or more RAG settings; unknown keys are ignored."""
    if not isinstance(request, dict) or not request:
        raise HTTPException(status_code=400, detail="Request body must be a non-empty object")
    try:
        result = SettingsManager.get_instance().update_rag_settings(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to update RAG settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return result
