"""Agent and approval API endpoints."""

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.async_db import get_async_db
from ..models.database import ApprovalRequest, AuditLog, get_sync_db
from ..services.agent_loop import AgentLoop
from ..services.episodic_memory import EpisodicMemory
from ..services.intent_classifier import IntentClassifier
from ..services.permission_engine import PermissionEngine
from ..services.short_term_memory import ShortTermMemoryManager
from ..core.config import settings
from ..core.logging_config import logger
from ..core.redaction import redact_value

router = APIRouter(prefix="/api", tags=["agent"])

_agent_loop: Optional[AgentLoop] = None
_intent_classifier: Optional[IntentClassifier] = None


def get_agent_loop() -> AgentLoop:
    global _agent_loop
    if _agent_loop is None:
        _agent_loop = AgentLoop()
    return _agent_loop


def get_intent_classifier() -> IntentClassifier:
    global _intent_classifier
    if _intent_classifier is None:
        _intent_classifier = IntentClassifier()
    return _intent_classifier


@router.post("/agent")
async def agent_chat(
    request: dict,
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    """Route the user message either to RAG chat or the agent runtime."""
    session_id = request.get("session_id", str(uuid.uuid4()))
    message = request.get("message", "").strip()
    intent_confirmed = bool(request.get("intent_confirmed", False))
    suggested_route = request.get("suggested_route")

    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    classifier = get_intent_classifier()
    agent = get_agent_loop()

    try:
        sync_db = next(get_sync_db())
        try:
            intent = classifier.classify(message, session_id, db=sync_db)
            EpisodicMemory.get_instance().log_event(
                session_id=session_id,
                actor="intent_classifier",
                action="intent_classified",
                details={"message": message, **intent},
                db=sync_db,
            )

            if not intent_confirmed and intent["needs_confirmation"]:
                return {
                    "status": "intent_confirmation",
                    "session_id": session_id,
                    "intent": intent["intent"],
                    "confidence": intent["confidence"],
                    "suggested_route": intent["suggested_route"],
                    "response": _confirmation_prompt(intent["intent"]),
                }

            if intent_confirmed and (suggested_route or intent["suggested_route"]) == "chat":
                from .chat import chat as rag_chat
                return await rag_chat({"message": message, "session_id": session_id}, db=db)

            if intent["suggested_route"] == "chat" and not intent_confirmed:
                from .chat import chat as rag_chat
                return await rag_chat({"message": message, "session_id": session_id}, db=db)

            result = agent.run(session_id, message, db=sync_db)
            result["session_id"] = session_id
            return result
        finally:
            sync_db.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Agent chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _confirmation_prompt(intent: str) -> str:
    if intent == "agent_action":
        return (
            "Toi hieu ban muon thuc hien mot hanh dong qua cong cu "
            "(tao/sua/xoa file hoac thao tac workspace). Ban xac nhan tiep tuc?"
        )
    return "Toi chua chac chan ve y dinh cua ban. Ban xac nhan tiep tuc xu ly yeu cau nay?"


@router.get("/approvals")
async def list_approvals(
    session_id: str,
    db: AsyncSession = Depends(get_async_db),
) -> List[Dict[str, Any]]:
    """List pending approval requests for a session."""
    perm = PermissionEngine()
    sync_db = next(get_sync_db())
    try:
        return perm.get_pending_approvals(session_id, db=sync_db)
    finally:
        sync_db.close()


@router.post("/approvals/{approval_id}/decide")
async def decide_approval(
    approval_id: str,
    request: dict,
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    """Record a user approval decision."""
    decision = request.get("decision")
    if decision not in ("approve", "deny"):
        raise HTTPException(status_code=400, detail="Invalid decision")

    perm = PermissionEngine()
    sync_db = next(get_sync_db())
    try:
        success = perm.record_approval_decision(approval_id, decision, db=sync_db)
        if not success:
            raise HTTPException(status_code=404, detail="Approval not found or not pending")
        return {"success": True, "decision": decision}
    finally:
        sync_db.close()


@router.post("/agent/continue")
async def continue_after_approval(
    request: dict,
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    """Resume the saved agent loop after an approval decision."""
    session_id = request.get("session_id")
    approval_id = request.get("approval_id")
    approved = bool(request.get("approved", True))
    if not session_id or not approval_id:
        raise HTTPException(status_code=400, detail="session_id and approval_id required")

    agent = get_agent_loop()
    sync_db = next(get_sync_db())
    try:
        state = agent.stm.get(session_id, "pending_agent_state", db=sync_db)
        if not state:
            raise HTTPException(status_code=404, detail="No pending agent state for this session")
        if state.get("approval_id") != approval_id:
            raise HTTPException(status_code=409, detail="Approval does not match pending agent state")

        approval = sync_db.execute(
            select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
        ).scalar_one_or_none()
        expected_status = "approved" if approved else "denied"
        if not approval or approval.status != expected_status:
            raise HTTPException(status_code=409, detail=f"Approval must be {expected_status} before continuing")

        result = agent.run_after_approval(session_id, sync_db, approved=approved)
        result["session_id"] = session_id
        result["approval_id"] = approval_id
        return result
    finally:
        sync_db.close()


@router.get("/events")
async def list_events(
    session_id: str,
    limit: int = 50,
    action: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Episodic event timeline for a session (tool activity, approvals, errors).

    Events are already redacted at write time.
    """
    sync_db = next(get_sync_db())
    try:
        return EpisodicMemory.get_instance().get_events(
            session_id, limit=limit, action=action, db=sync_db
        )
    finally:
        sync_db.close()


@router.get("/audit")
async def list_audit(
    session_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Audit log viewer. Optionally scoped to a session."""
    sync_db = next(get_sync_db())
    try:
        stmt = select(AuditLog)
        if session_id:
            stmt = stmt.where(AuditLog.session_id == session_id)
        stmt = stmt.order_by(AuditLog.timestamp.desc()).limit(limit)
        rows = sync_db.execute(stmt).scalars().all()
        return [
            {
                "id": r.id,
                "session_id": r.session_id,
                "actor": r.actor,
                "action": r.action,
                "details": redact_value(r.details or {}),
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for r in rows
        ]
    finally:
        sync_db.close()


@router.get("/memory")
async def list_memory(session_id: str) -> Dict[str, Any]:
    """View short-term memory entries and their mutation history for a session."""
    stm = ShortTermMemoryManager.get_instance()
    sync_db = next(get_sync_db())
    try:
        entries = stm.get_all(session_id, db=sync_db)
        visible = {k: v for k, v in entries.items() if k != "pending_agent_state"}
        return {
            "entries": redact_value(visible),
            "history": stm.get_history(session_id, db=sync_db),
        }
    finally:
        sync_db.close()


@router.post("/memory/undo")
async def undo_memory(request: dict) -> Dict[str, Any]:
    """Revert the most recent memory mutation (optionally for a specific key)."""
    session_id = request.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    key = request.get("key")
    stm = ShortTermMemoryManager.get_instance()
    sync_db = next(get_sync_db())
    try:
        result = stm.undo(session_id, key=key, db=sync_db)
        if result.get("status") != "success":
            raise HTTPException(status_code=404, detail=result.get("error", "Nothing to undo"))
        return result
    finally:
        sync_db.close()


@router.delete("/memory")
async def delete_memory(session_id: str, key: str) -> Dict[str, Any]:
    """Delete a single short-term memory key (history captured for undo)."""
    stm = ShortTermMemoryManager.get_instance()
    sync_db = next(get_sync_db())
    try:
        deleted = stm.delete(session_id, key, db=sync_db)
        if not deleted:
            raise HTTPException(status_code=404, detail="Key not found")
        return {"success": True, "key": key}
    finally:
        sync_db.close()


@router.get("/settings")
async def agent_settings() -> Dict[str, Any]:
    """Expose effective agent/provider settings (no secrets)."""
    provider = "openrouter" if "openrouter" in (settings.OPENAI_BASE_URL or "").lower() else "openai"
    return {
        "provider": provider,
        "base_url": settings.OPENAI_BASE_URL,
        "model": settings.MODEL,
        "default_model": settings.DEFAULT_MODEL,
        "api_key_configured": bool(settings.OPENAI_API_KEY),
        "embedding_model": settings.EMBEDDING_MODEL,
        "use_local_embeddings": settings.USE_LOCAL_EMBEDDINGS,
        "rag_threshold": settings.RAG_THRESHOLD,
        "use_rerank": settings.USE_RERANK,
        "citation_coverage_min": settings.CITATION_COVERAGE_MIN,
    }


@router.get("/tools")
async def list_registered_tools() -> List[Dict[str, Any]]:
    """List registered tools with risk metadata (for the UI tool catalog)."""
    from ..services.tool_registry import ToolRegistry
    tools = ToolRegistry.get_instance().list_tools()
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "risk_level": t["risk_level"],
            "requires_approval": t["requires_approval"],
            "rollback_supported": t["rollback_supported"],
            "enabled": t["enabled"],
        }
        for t in tools
    ]


@router.get("/browser/state")
async def browser_state(session_id: str, limit: int = 30) -> Dict[str, Any]:
    """Live browser state for a session + recent action log.

    Live URL/title/screenshot come from the in-memory BrowserManager; the action
    history comes from the browser_actions table (already redacted at write time).
    """
    from ..services.browser_manager import BrowserManager
    from ..models.database import BrowserAction

    state = {"current_url": None, "title": None, "is_active": False}
    screenshot = None
    try:
        mgr = BrowserManager.get_instance()
        state = mgr.state(session_id)
        if state.get("is_active"):
            shot = mgr.screenshot(session_id)
            if "error" not in shot:
                screenshot = shot.get("image_b64")
    except Exception as e:
        logger.error(f"browser_state error: {e}")

    sync_db = next(get_sync_db())
    try:
        stmt = (
            select(BrowserAction)
            .where(BrowserAction.session_id == session_id)
            .order_by(BrowserAction.created_at.desc())
            .limit(limit)
        )
        rows = sync_db.execute(stmt).scalars().all()
        actions = [
            {
                "id": r.id,
                "action": r.action,
                "target": r.target,
                "status": r.status,
                "timestamp": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        sync_db.close()

    return {
        "session_id": session_id,
        "current_url": state.get("current_url"),
        "title": state.get("title"),
        "is_active": state.get("is_active", False),
        "screenshot": screenshot,
        "actions": actions,
    }


@router.post("/browser/close")
async def browser_close_session(request: dict) -> Dict[str, Any]:
    """Close the browser tab for a session."""
    session_id = request.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    from ..services.browser_manager import BrowserManager
    try:
        result = BrowserManager.get_instance().close(session_id)
        return {"success": "error" not in result, **result}
    except Exception as e:
        logger.error(f"browser_close error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sandbox/runs")
async def sandbox_runs(session_id: str, limit: int = 30) -> Dict[str, Any]:
    """Recent sandbox executions for a session (code/command + stdout/stderr/artifacts)."""
    from ..models.database import SandboxRun

    sync_db = next(get_sync_db())
    try:
        stmt = (
            select(SandboxRun)
            .where(SandboxRun.session_id == session_id)
            .order_by(SandboxRun.created_at.desc())
            .limit(limit)
        )
        rows = sync_db.execute(stmt).scalars().all()
        runs = [
            {
                "id": r.id,
                "kind": r.kind,
                "mode": r.mode,
                "code": r.code,
                "status": r.status,
                "exit_code": r.exit_code,
                "killed_reason": r.killed_reason,
                "stdout": r.stdout_preview,
                "stderr": r.stderr_preview,
                "artifacts": r.artifacts_json or [],
                "duration_ms": r.duration_ms,
                "timestamp": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        sync_db.close()

    return {"session_id": session_id, "runs": runs}


@router.get("/sandbox/artifact")
async def sandbox_artifact(session_id: str, name: str) -> Dict[str, Any]:
    """Read one artifact file from a session's sandbox directory."""
    from ..services.sandbox_runner import SandboxRunner
    try:
        result = SandboxRunner.get_instance().read_artifact(session_id, name)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"sandbox_artifact error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
