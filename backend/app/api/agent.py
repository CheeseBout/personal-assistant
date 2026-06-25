"""Agent and approval API endpoints."""

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.async_db import get_async_db
from ..models.database import ApprovalRequest, get_sync_db
from ..services.agent_loop import AgentLoop
from ..services.episodic_memory import EpisodicMemory
from ..services.intent_classifier import IntentClassifier
from ..services.permission_engine import PermissionEngine
from ..core.logging_config import logger

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
