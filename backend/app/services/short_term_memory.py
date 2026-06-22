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
from typing import Any, Dict, Optional
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import select, update

from ..models.database import get_sync_db, ShortTermMemory as STMModel
from ..core.logging_config import logger


class ShortTermMemoryManager:
    """Singleton manager for short-term memory."""

    _instance: Optional["ShortTermMemoryManager"] = None

    @classmethod
    def get_instance(cls) -> "ShortTermMemoryManager":
        if cls._instance is None:
            cls._instance = ShortTermMemoryManager()
        return cls._instance

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
                existing.value_json = value
                existing.updated_at = datetime.utcnow()
                db.add(existing)
                memory_id = existing.id
            else:
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
                db.delete(existing)
                db.commit()
                return True
            return False
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
