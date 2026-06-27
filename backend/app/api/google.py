"""Google integration API endpoints (Phase 5 — Gmail first).

Connection management for the shared Google OAuth (installed-app/Desktop flow)
plus a redacted action log for the Google panel. Tokens are never returned.
"""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from ..models.database import GmailAction, GoogleWorkspaceAction, get_sync_db
from ..services.google_auth import GoogleAuth
from ..core.logging_config import logger

router = APIRouter(prefix="/api", tags=["google"])


@router.get("/google/status")
async def google_status() -> Dict[str, Any]:
    """Whether Google is connected (no token material is returned)."""
    try:
        return GoogleAuth.get_instance().status()
    except Exception as e:
        logger.error(f"google_status error: {e}")
        return {"connected": False, "email": None}


@router.post("/google/connect")
async def google_connect() -> Dict[str, Any]:
    """Run the interactive OAuth flow (opens the user's browser). Blocking call,
    offloaded to a worker thread so the event loop stays responsive."""
    try:
        result = await run_in_threadpool(GoogleAuth.get_instance().start_auth_flow)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"google_connect error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/google/disconnect")
async def google_disconnect() -> Dict[str, Any]:
    """Forget the local token."""
    try:
        return GoogleAuth.get_instance().revoke()
    except Exception as e:
        logger.error(f"google_disconnect error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/google/actions")
async def google_actions(session_id: str, limit: int = 30) -> Dict[str, Any]:
    """Recent Gmail + Drive/Docs/Sheets action log for a session (already redacted).

    Merges the gmail_actions and google_workspace_actions tables into one
    timeline, each item tagged with its service.
    """
    sync_db = next(get_sync_db())
    try:
        gmail_rows = sync_db.execute(
            select(GmailAction)
            .where(GmailAction.session_id == session_id)
            .order_by(GmailAction.created_at.desc())
            .limit(limit)
        ).scalars().all()
        ws_rows = sync_db.execute(
            select(GoogleWorkspaceAction)
            .where(GoogleWorkspaceAction.session_id == session_id)
            .order_by(GoogleWorkspaceAction.created_at.desc())
            .limit(limit)
        ).scalars().all()
    finally:
        sync_db.close()

    actions = [
        {
            "id": r.id, "service": "gmail", "action": r.action, "target": r.target,
            "status": r.status,
            "timestamp": r.created_at.isoformat() if r.created_at else None,
        }
        for r in gmail_rows
    ] + [
        {
            "id": r.id, "service": r.service, "action": r.action, "target": r.target,
            "status": r.status,
            "timestamp": r.created_at.isoformat() if r.created_at else None,
        }
        for r in ws_rows
    ]
    # Most-recent first across both sources.
    actions.sort(key=lambda a: a["timestamp"] or "", reverse=True)
    return {"session_id": session_id, "actions": actions[:limit]}
