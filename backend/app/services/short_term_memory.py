"""Short-Term Memory — session-scoped key-value store for agent state.

Persists across agent iterations within the same session but is separate from
episodic memory (which is append-only events). Used for:
- Current task
- Active files
- Browser state
- Temporary decisions
- Pending tool calls (for HITL resume)
"""

import uuid
from typing import Any, Dict, Optional, List
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import select, update

from ..models.database import get_sync_db, ShortTermMemory as STMModel, ShortTermMemoryHistory as STMHistory
from ..core.logging_config import logger


class ShortTermMemoryManager:
    """Singleton manager for short-term memory."""

    _instance: Optional["ShortTermMemoryManager"] = None

    @classmethod
    def get_instance(cls) -> "ShortTermMemoryManager":
        if cls._instance is None:
            cls._instance = ShortTermMemoryManager()
        return cls._instance

    def _record_history(self, session_id: str, key: str, old_value: Any,
                        existed: bool, operation: str, db: Session):
        """Capture the prior value of a key before it is mutated, enabling undo.

        Internal resume state (pending_agent_state) is intentionally not
        versioned — it is transient HITL plumbing, not user-facing memory.
        """
        if key == "pending_agent_state":
            return
        db.add(STMHistory(
            id=str(uuid.uuid4()),
            session_id=session_id,
            key=key,
            old_value_json=old_value,
            operation=operation,
            existed_before=existed,
            created_at=datetime.utcnow(),
        ))

    def set(self, session_id: str, key: str, value: Any, db: Optional[Session] = None) -> str:
        """Set a key-value pair. Returns memory ID."""
        close_db = False
        if db is None:
            db = next(get_sync_db())
            close_db = True

        try:
            # Upsert pattern: find existing, update or insert
            stmt = select(STMModel).where(
                STMModel.session_id == session_id,
                STMModel.key == key
            )
            existing = db.execute(stmt).scalar_one_or_none()

            if existing:
                self._record_history(session_id, key, existing.value_json, True, "set", db)
                existing.value_json = value
                existing.updated_at = datetime.utcnow()
                db.add(existing)
                memory_id = existing.id
            else:
                self._record_history(session_id, key, None, False, "set", db)
                memory = STMModel(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    key=key,
                    value_json=value,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                db.add(memory)
                memory_id = memory.id

            db.commit()
            return memory_id
        finally:
            if close_db:
                db.close()

    def get(self, session_id: str, key: str, db: Optional[Session] = None) -> Optional[Any]:
        """Retrieve value by key. Returns None if not found."""
        close_db = False
        if db is None:
            db = next(get_sync_db())
            close_db = True

        try:
            stmt = select(STMModel).where(
                STMModel.session_id == session_id,
                STMModel.key == key
            )
            result = db.execute(stmt).scalar_one_or_none()
            return result.value_json if result else None
        finally:
            if close_db:
                db.close()

    def delete(self, session_id: str, key: str, db: Optional[Session] = None) -> bool:
        """Delete a key. Returns True if deleted."""
        close_db = False
        if db is None:
            db = next(get_sync_db())
            close_db = True

        try:
            stmt = select(STMModel).where(
                STMModel.session_id == session_id,
                STMModel.key == key
            )
            existing = db.execute(stmt).scalar_one_or_none()
            if existing:
                self._record_history(session_id, key, existing.value_json, True, "delete", db)
                db.delete(existing)
                db.commit()
                return True
            return False
        finally:
            if close_db:
                db.close()

    def undo(self, session_id: str, key: Optional[str] = None,
             db: Optional[Session] = None) -> Dict[str, Any]:
        """Revert the most recent mutation, restoring the prior value.

        If ``key`` is given, undo the latest change to that key; otherwise undo
        the latest change in the session. Mirrors file_undo for memory.
        """
        close_db = False
        if db is None:
            db = next(get_sync_db())
            close_db = True

        try:
            stmt = select(STMHistory).where(STMHistory.session_id == session_id)
            if key is not None:
                stmt = stmt.where(STMHistory.key == key)
            stmt = stmt.order_by(STMHistory.created_at.desc()).limit(1)
            entry = db.execute(stmt).scalar_one_or_none()
            if not entry:
                return {"status": "error", "error": "No memory history to undo"}

            target_key = entry.key
            current = db.execute(select(STMModel).where(
                STMModel.session_id == session_id,
                STMModel.key == target_key
            )).scalar_one_or_none()

            if entry.existed_before:
                # Restore the prior value
                if current:
                    current.value_json = entry.old_value_json
                    current.updated_at = datetime.utcnow()
                    db.add(current)
                else:
                    db.add(STMModel(
                        id=str(uuid.uuid4()),
                        session_id=session_id,
                        key=target_key,
                        value_json=entry.old_value_json,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    ))
                restored = entry.old_value_json
            else:
                # Key did not exist before; undo means removing it
                if current:
                    db.delete(current)
                restored = None

            # Consume the history entry so repeated undo walks further back
            db.delete(entry)
            db.commit()
            return {
                "status": "success",
                "key": target_key,
                "operation_undone": entry.operation,
                "restored_value": restored,
            }
        finally:
            if close_db:
                db.close()

    def get_history(self, session_id: str, limit: int = 50,
                    db: Optional[Session] = None) -> List[Dict[str, Any]]:
        """List recent memory mutations for a session (newest first)."""
        close_db = False
        if db is None:
            db = next(get_sync_db())
            close_db = True

        try:
            stmt = select(STMHistory).where(
                STMHistory.session_id == session_id
            ).order_by(STMHistory.created_at.desc()).limit(limit)
            rows = db.execute(stmt).scalars().all()
            return [
                {
                    "id": r.id,
                    "key": r.key,
                    "operation": r.operation,
                    "existed_before": r.existed_before,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
        finally:
            if close_db:
                db.close()

    def get_all(self, session_id: str, db: Optional[Session] = None) -> Dict[str, Any]:
        """Get all key-value pairs for a session."""
        close_db = False
        if db is None:
            db = next(get_sync_db())
            close_db = True

        try:
            stmt = select(STMModel).where(STMModel.session_id == session_id)
            results = db.execute(stmt).scalars().all()
            return {r.key: r.value_json for r in results}
        finally:
            if close_db:
                db.close()

    def clear_session(self, session_id: str, db: Optional[Session] = None) -> int:
        """Clear all memory for a session. Returns count of deleted entries."""
        close_db = False
        if db is None:
            db = next(get_sync_db())
            close_db = True

        try:
            stmt = select(STMModel).where(STMModel.session_id == session_id)
            results = db.execute(stmt).scalars().all()
            count = 0
            for r in results:
                db.delete(r)
                count += 1
            db.commit()
            return count
        finally:
            if close_db:
                db.close()
