"""Permission Engine — enforces HITL and deny-first safety model.

Workflow:
1. Receive tool call request.
2. Load tool metadata from registry.
3. Run risk classifier → risk level + suggested decision.
4. Apply policy rules (allow/ask/deny patterns).
5. If decision is 'ask' or 'ask_strong' → create ApprovalRequest, return pending.
6. If decision is 'allow' and risk is low → execute immediately.
7. If decision is 'deny' → reject.

Policy precedence: deny > ask_strong > ask > allow
"""

import uuid
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import select

from .tool_registry import ToolRegistry
from .risk_classifier import RiskClassifier
from .episodic_memory import EpisodicMemory
from .short_term_memory import ShortTermMemoryManager
from ..core.logging_config import logger
from ..core.redaction import redact_value
from ..models.database import get_sync_db, ApprovalRequest
from ..services.audit_integrity import create_audit_entry


class PermissionEngine:
    """Decides whether a tool call can proceed or needs approval."""

    def __init__(self):
        self.registry = ToolRegistry.get_instance()
        self.classifier = RiskClassifier()
        self.episodic = EpisodicMemory.get_instance()
        self.stm = ShortTermMemoryManager.get_instance()

    def check_and_log(self, session_id: str, tool_name: str, arguments: Dict[str, Any],
                      reason: str = "", db: Optional[Session] = None) -> Dict[str, Any]:
        """Main entry: evaluate permission for a tool call.

        Returns:
        {
            "decision": "allow" | "ask" | "ask_strong" | "deny",
            "risk_level": int,
            "approval_id": str (if pending),
            "explanation": str,
            "matched_rules": List[str],
            "tool_metadata": dict
        }
        """
        close_db = False
        if db is None:
            db = next(get_sync_db())
            close_db = True

        try:
            tool_meta = self.registry.get_tool(tool_name)
            if not tool_meta:
                return {
                    "decision": "deny",
                    "risk_level": 99,
                    "explanation": f"Unknown tool: {tool_name}",
                    "matched_rules": ["unknown_tool"],
                }

            # Run risk classifier
            classification = self.classifier.classify(tool_name, arguments, tool_meta)
            risk_level = classification["risk_level"]
            denied = classification["denied"]
            matched_rules = classification["matched_rules"]
            explanation = classification["explanation"]

            # Apply policy precedence: deny first
            if denied:
                self.episodic.log_event(
                    session_id=session_id,
                    actor="permission_engine",
                    action="tool_denied",
                    details={"tool": tool_name, "arguments": arguments, "reason": explanation, "rules": matched_rules},
                    db=db
                )
                create_audit_entry(
                    session_id=session_id,
                    actor="permission_engine",
                    action="tool_denied",
                    details={"tool": tool_name, "risk_level": risk_level, "rules": matched_rules},
                    db=db,
                )
                db.commit()
                return {
                    "decision": "deny",
                    "risk_level": risk_level,
                    "explanation": explanation,
                    "matched_rules": matched_rules,
                    "tool_metadata": tool_meta,
                }

            # Determine final decision based on risk level and requires_approval
            decision = self._decide_action(risk_level, tool_meta["requires_approval"], classification["requires_approval"])

            if decision in ("ask", "ask_strong"):
                # Expire stale approvals before creating a new one
                self.cleanup_expired_approvals(db)

                # Create approval request
                approval_id = str(uuid.uuid4())
                approval = ApprovalRequest(
                    id=approval_id,
                    session_id=session_id,
                    tool_name=tool_name,
                    arguments_json=arguments,
                    risk_level=risk_level,
                    reason=reason or f"Risk: {explanation}",
                    status="pending",
                    requested_at=datetime.utcnow(),
                )
                db.add(approval)

                self.episodic.log_event(
                    session_id=session_id,
                    actor="permission_engine",
                    action="tool_pending_approval",
                    details={"tool": tool_name, "approval_id": approval_id, "risk_level": risk_level},
                    db=db
                )
                create_audit_entry(
                    session_id=session_id,
                    actor="permission_engine",
                    action="tool_pending_approval",
                    details={"tool": tool_name, "approval_id": approval_id, "risk_level": risk_level},
                    db=db,
                )
                db.commit()

                return {
                    "decision": decision,
                    "risk_level": risk_level,
                    "approval_id": approval_id,
                    "explanation": explanation,
                    "matched_rules": matched_rules,
                    "tool_metadata": tool_meta,
                }
            else:
                # Allow — log it
                self.episodic.log_event(
                    session_id=session_id,
                    actor="permission_engine",
                    action="tool_approved_auto",
                    details={"tool": tool_name, "risk_level": risk_level},
                    db=db
                )
                create_audit_entry(
                    session_id=session_id,
                    actor="permission_engine",
                    action="tool_approved_auto",
                    details={"tool": tool_name, "risk_level": risk_level},
                    db=db,
                )
                db.commit()
                return {
                    "decision": "allow",
                    "risk_level": risk_level,
                    "explanation": explanation,
                    "matched_rules": matched_rules,
                    "tool_metadata": tool_meta,
                }
        finally:
            if close_db:
                db.close()

    def _decide_action(self, risk_level: int, tool_requires_approval: bool, classifier_suggests: bool) -> str:
        """Determine final permission decision based on risk and classifier.

        Policy precedence: deny-first is already handled by classifier.
        Now decide between allow/ask/ask_strong.
        """
        # If classifier says require approval (based on risk), respect that
        if classifier_suggests or tool_requires_approval:
            if risk_level >= 2:
                return "ask_strong"  # High risk needs strong confirmation
            else:
                return "ask"  # Medium risk needs normal approval
        return "allow"

    def record_approval_decision(self, approval_id: str, decision: str, db: Session) -> bool:
        """Record user's approval/deny decision."""
        stmt = select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
        approval = db.execute(stmt).scalar_one_or_none()
        if not approval or approval.status != "pending":
            return False

        approval.status = "approved" if decision == "approve" else "denied"
        approval.decided_at = datetime.utcnow()
        approval.decided_by = "user"
        db.add(approval)

        self.episodic.log_event(
            session_id=approval.session_id,
            actor="user",
            action=f"approval_{decision}",
            details={"approval_id": approval_id, "tool": approval.tool_name},
            db=db
        )
        db.commit()
        return True

    def get_pending_approvals(self, session_id: str, db: Session) -> list:
        """List pending approval requests for a session."""
        stmt = select(ApprovalRequest).where(
            ApprovalRequest.session_id == session_id,
            ApprovalRequest.status == "pending"
        ).order_by(ApprovalRequest.requested_at)
        result = db.execute(stmt)
        approvals = result.scalars().all()
        out = []
        for a in approvals:
            out.append({
                "id": a.id,
                "tool_name": a.tool_name,
                "arguments": redact_value(a.arguments_json),
                "reason": a.reason,
                "risk_level": a.risk_level,
                "requested_at": a.requested_at.isoformat() if a.requested_at else None,
            })
        return out

    def cleanup_expired_approvals(self, db: Session) -> int:
        """Expire stale pending approvals older than APPROVAL_TIMEOUT_MINUTES."""
        from ..core.config import settings
        cutoff = datetime.utcnow() - timedelta(minutes=settings.APPROVAL_TIMEOUT_MINUTES)
        stmt = select(ApprovalRequest).where(
            ApprovalRequest.status == "pending",
            ApprovalRequest.requested_at < cutoff,
        )
        expired = db.execute(stmt).scalars().all()
        for approval in expired:
            approval.status = "timeout"
            approval.decided_at = datetime.utcnow()
        if expired:
            db.commit()
        return len(expired)
