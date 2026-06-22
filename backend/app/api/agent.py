"""Agent and Approval API endpoints for Phase 3."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List, Dict, Any
import uuid

from ..models.async_db import get_async_db, Message as MessageModel
from ..services.agent_loop import AgentLoop
from ..services.permission_engine import PermissionEngine
from ..services.short_term_memory import ShortTermMemoryManager
from ..services.episodic_memory import EpisodicMemory
from ..core.logging_config import logger

router = APIRouter(prefix="/api", tags=["agent"])

_agent_loop: Optional[AgentLoop] = None


def get_agent_loop() -> AgentLoop:
    global _agent_loop
    if _agent_loop is None:
        _agent_loop = AgentLoop()
    return _agent_loop


@router.post("/agent")
async def agent_chat(
    request: dict,
    db: AsyncSession = Depends(get_async_db)
) -> Dict[str, Any]:
    """Main agent chat endpoint. Supports both direct tool execution and HITL approvals.

    Body: { "message": "...", "session_id": "..." }
    Returns: response or pending approval request.
    """
    session_id = request.get("session_id", str(uuid.uuid4()))
    message = request.get("message", "").strip()

    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    agent = get_agent_loop()
    try:
        # The agent loop uses sync DB session; we need to adapt
        # For now, get a sync session from the same DB connection
        from ..models.database import get_sync_db
        sync_db = next(get_sync_db())
        try:
            result = agent.run(session_id, message, db=sync_db)
            return result
        finally:
            sync_db.close()
    except Exception as e:
        logger.error(f"Agent chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/approvals")
async def list_approvals(
    session_id: str,
    db: AsyncSession = Depends(get_async_db)
) -> List[Dict[str, Any]]:
    """List pending approval requests for a session."""
    from ..services.permission_engine import PermissionEngine
    from ..models.database import get_sync_db
    perm = PermissionEngine()
    sync_db = next(get_sync_db())
    try:
        pending = perm.get_pending_approvals(session_id, db=sync_db)
        return pending
    finally:
        sync_db.close()


@router.post("/approvals/{approval_id}/decide")
async def decide_approval(
    approval_id: str,
    request: dict,
    db: AsyncSession = Depends(get_async_db)
) -> Dict[str, Any]:
    """Record user decision on an approval request."""
    decision = request.get("decision")  # "approve" or "deny"
    if decision not in ("approve", "deny"):
        raise HTTPException(status_code=400, detail="Invalid decision")

    from ..services.permission_engine import PermissionEngine
    from ..models.database import get_sync_db
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
    db: AsyncSession = Depends(get_async_db)
) -> Dict[str, Any]:
    """Resume agent loop after user approves a pending tool call."""
    session_id = request.get("session_id")
    approval_id = request.get("approval_id")
    if not session_id or not approval_id:
        raise HTTPException(status_code=400, detail="session_id and approval_id required")

    # Fetch the pending tool call from STM or reconstruct from approval
    from ..services.short_term_memory import ShortTermMemoryManager
    from ..services.agent_loop import AgentLoop
    stm = ShortTermMemoryManager.get_instance()
    agent = get_agent_loop()
    from ..models.database import get_sync_db
    sync_db = next(get_sync_db())
    try:
        # For now, retrieve stored tool call from approval details
        # In a more robust implementation, we'd store the pending tool call in STM when creating approval
        pending = _get_pending_tool_from_approval(approval_id, sync_db)
        if not pending:
            raise HTTPException(status_code=404, detail="No pending tool call for this approval")

        tool_name = pending["tool_name"]
        arguments = pending["arguments"]

        # Execute the tool after approval
        exec_result = agent.executor.dispatch_after_approval(tool_name, arguments, session_id, db=sync_db)

        # Build next LLM call to get final response
        # Simplified: just return the tool result; full loop would continue
        return {
            "response": f"Tool {tool_name} executed after approval. Result: {exec_result.get('result', exec_result)}",
            "status": "completed",
            "tool_result": exec_result,
        }
    finally:
        sync_db.close()


def _get_pending_tool_from_approval(approval_id: str, db) -> Optional[Dict]:
    """Extract tool call details from an approval request."""
    from sqlalchemy import select
    from ..models.database import ApprovalRequest as ApprovalModel
    stmt = select(ApprovalModel).where(ApprovalModel.id == approval_id)
    approval = db.execute(stmt).scalar_one_or_none()
    if not approval or approval.status != "approved":
        return None
    return {
        "tool_name": approval.tool_name,
        "arguments": approval.arguments_json,
    }
