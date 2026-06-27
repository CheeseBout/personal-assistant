"""Episodic Memory — append-only event log for agent actions.

Stores chronological events that happened during agent sessions for later recall
and reasoning. Events include tool calls, approvals, results, and errors.
"""

import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import select

from ..models.database import get_sync_db, EpisodicEvent
from ..core.logging_config import logger
from ..core.redaction import redact_value


class EpisodicMemory:
    """Singleton manager for episodic event logging."""

    _instance: Optional["EpisodicMemory"] = None

    @classmethod
    def get_instance(cls) -> "EpisodicMemory":
        if cls._instance is None:
            cls._instance = EpisodicMemory()
        return cls._instance

    def log_event(self, session_id: str, actor: str, action: str, details: Dict[str, Any],
                  metadata: Optional[Dict[str, Any]] = None, db: Optional[Session] = None):
        """Log an event to episodic memory.

        Args:
            session_id: Session identifier
            actor: "user", "agent", "system", "permission_engine", etc.
            action: Event type (e.g., "tool_call", "tool_result", "approval_granted")
            details: Event-specific data
            metadata: Optional metadata (e.g., risk_level, tool_name)
            db: Optional database session (if None, creates one)
        """
        close_db = False
        if db is None:
            db = next(get_sync_db())
            close_db = True

        try:
            event = EpisodicEvent(
                id=str(uuid.uuid4()),
                session_id=session_id,
                actor=actor,
                action=action,
                details_json=redact_value(details),
                metadata_json=redact_value(metadata or {}),
                created_at=datetime.utcnow()
            )
            db.add(event)
            db.commit()
            logger.debug(f"Episodic event: {actor}/{action} in session {session_id[:8]}")
        except Exception as e:
            logger.error(f"Failed to log episodic event: {e}")
            if db:
                db.rollback()
        finally:
            if close_db:
                db.close()

    def get_events(self, session_id: str, limit: int = 50, action: Optional[str] = None,
                   db: Optional[Session] = None) -> List[Dict[str, Any]]:
        """Retrieve recent events for a session."""
        close_db = False
        if db is None:
            db = next(get_sync_db())
            close_db = True

        try:
            stmt = select(EpisodicEvent).where(
                EpisodicEvent.session_id == session_id
            ).order_by(EpisodicEvent.created_at.desc()).limit(limit)

            if action:
                stmt = stmt.where(EpisodicEvent.action == action)

            result = db.execute(stmt)
            events = result.scalars().all()

            return [
                {
                    "id": e.id,
                    "actor": e.actor,
                    "action": e.action,
                    "details": e.details_json,
                    "metadata": e.metadata_json,
                    "created_at": e.created_at.isoformat() if e.created_at else None
                }
                for e in events
            ]
        finally:
            if close_db:
                db.close()

    def search_events(self, session_id: str, query: str, limit: int = 20,
                      db: Optional[Session] = None) -> List[Dict[str, Any]]:
        """Simple text search in event details (basic implementation)."""
        # For now, just filter by action matching
        events = self.get_events(session_id, limit=limit, db=db)
        return [e for e in events if query.lower() in e["action"].lower()]
