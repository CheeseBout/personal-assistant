"""Long-Term Memory — durable cross-session memory (Phase 6).

The umbrella store for memory that outlives a single session: semantic
(facts/preferences), procedural (workflows), and kept episodic summaries.
Unlike short-term memory this is NOT session-scoped.

Safety (REQUIREMENTS §9.7): refuses to persist content that looks like a
secret (password/token/api key/private key). Every record carries provenance
(``source``) so the user can see where it came from.
"""

import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import select, or_

from ..models.database import get_sync_db, LongTermMemory as LTMModel
from ..core.logging_config import logger
from ..core.redaction import contains_secret

VALID_TYPES = {"semantic", "procedural", "episodic"}


class SecretInMemoryError(ValueError):
    """Raised when an attempt is made to store secret-shaped content."""


class LongTermMemoryManager:
    """Singleton manager for long-term (cross-session) memory."""

    _instance: Optional["LongTermMemoryManager"] = None

    @classmethod
    def get_instance(cls) -> "LongTermMemoryManager":
        if cls._instance is None:
            cls._instance = LongTermMemoryManager()
        return cls._instance

    @staticmethod
    def _serialize(m: LTMModel) -> Dict[str, Any]:
        return {
            "id": m.id,
            "type": m.type,
            "content": m.content,
            "source": m.source,
            "confidence": m.confidence,
            "tags": m.tags_json or [],
            "enabled": bool(m.enabled),
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
            "last_used_at": m.last_used_at.isoformat() if m.last_used_at else None,
        }

    def save(self, content: str, mem_type: str = "semantic", source: Optional[str] = None,
             confidence: Optional[int] = None, tags: Optional[List[str]] = None,
             db: Optional[Session] = None) -> Dict[str, Any]:
        """Persist a memory. Refuses secrets and de-duplicates exact content."""
        content = (content or "").strip()
        if not content:
            return {"status": "error", "error": "Nội dung ghi nhớ rỗng"}
        if contains_secret(content):
            logger.warning("Refused to store secret-shaped content in long-term memory")
            return {
                "status": "error",
                "error": "Từ chối lưu: nội dung có vẻ chứa secret/credential.",
            }
        if mem_type not in VALID_TYPES:
            mem_type = "semantic"

        close_db = False
        if db is None:
            db = next(get_sync_db())
            close_db = True
        try:
            # De-dup: same type + identical content already stored -> return it.
            existing = db.execute(
                select(LTMModel).where(
                    LTMModel.type == mem_type,
                    LTMModel.content == content,
                )
            ).scalar_one_or_none()
            if existing:
                if not existing.enabled:
                    existing.enabled = True
                    existing.updated_at = datetime.utcnow()
                    db.add(existing)
                    db.commit()
                return {"status": "success", "deduplicated": True, "memory": self._serialize(existing)}

            mem = LTMModel(
                id=str(uuid.uuid4()),
                type=mem_type,
                content=content,
                source=source,
                confidence=confidence,
                tags_json=tags or [],
                enabled=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(mem)
            db.commit()
            return {"status": "success", "memory": self._serialize(mem)}
        finally:
            if close_db:
                db.close()

    def search(self, query: str, mem_type: Optional[str] = None, limit: int = 10,
               include_disabled: bool = False, touch: bool = True,
               db: Optional[Session] = None) -> List[Dict[str, Any]]:
        """Substring search over content + tags. Bumps last_used_at on hits.

        Token-based: a record matches if it contains any whitespace-split token
        of the query (case-insensitive), ranked by number of tokens matched.
        """
        close_db = False
        if db is None:
            db = next(get_sync_db())
            close_db = True
        try:
            stmt = select(LTMModel)
            if not include_disabled:
                stmt = stmt.where(LTMModel.enabled == True)  # noqa: E712
            if mem_type in VALID_TYPES:
                stmt = stmt.where(LTMModel.type == mem_type)
            rows = db.execute(stmt).scalars().all()

            tokens = [t for t in (query or "").lower().split() if t]
            scored = []
            for r in rows:
                haystack = (r.content or "").lower()
                if r.tags_json:
                    haystack += " " + " ".join(str(t).lower() for t in r.tags_json)
                if not tokens:
                    score = 0
                else:
                    score = sum(1 for t in tokens if t in haystack)
                    if score == 0:
                        continue
                scored.append((score, r))

            scored.sort(key=lambda x: (x[0], x[1].updated_at or datetime.min), reverse=True)
            hits = [r for _, r in scored[:limit]]

            if touch and hits:
                now = datetime.utcnow()
                for r in hits:
                    r.last_used_at = now
                    db.add(r)
                db.commit()

            return [self._serialize(r) for r in hits]
        finally:
            if close_db:
                db.close()

    def list_all(self, mem_type: Optional[str] = None, include_disabled: bool = True,
                 limit: int = 200, db: Optional[Session] = None) -> List[Dict[str, Any]]:
        """List memories (newest first), optionally filtered by type."""
        close_db = False
        if db is None:
            db = next(get_sync_db())
            close_db = True
        try:
            stmt = select(LTMModel)
            if mem_type in VALID_TYPES:
                stmt = stmt.where(LTMModel.type == mem_type)
            if not include_disabled:
                stmt = stmt.where(LTMModel.enabled == True)  # noqa: E712
            stmt = stmt.order_by(LTMModel.updated_at.desc()).limit(limit)
            rows = db.execute(stmt).scalars().all()
            return [self._serialize(r) for r in rows]
        finally:
            if close_db:
                db.close()

    def update(self, memory_id: str, content: Optional[str] = None,
               mem_type: Optional[str] = None, tags: Optional[List[str]] = None,
               db: Optional[Session] = None) -> Dict[str, Any]:
        """Edit an existing memory's content/type/tags (secret guard re-applied)."""
        close_db = False
        if db is None:
            db = next(get_sync_db())
            close_db = True
        try:
            mem = db.get(LTMModel, memory_id)
            if not mem:
                return {"status": "error", "error": "Không tìm thấy ghi nhớ"}
            if content is not None:
                content = content.strip()
                if not content:
                    return {"status": "error", "error": "Nội dung ghi nhớ rỗng"}
                if contains_secret(content):
                    return {"status": "error", "error": "Từ chối lưu: nội dung có vẻ chứa secret/credential."}
                mem.content = content
            if mem_type is not None and mem_type in VALID_TYPES:
                mem.type = mem_type
            if tags is not None:
                mem.tags_json = tags
            mem.updated_at = datetime.utcnow()
            db.add(mem)
            db.commit()
            return {"status": "success", "memory": self._serialize(mem)}
        finally:
            if close_db:
                db.close()

    def set_enabled(self, memory_id: str, enabled: bool,
                    db: Optional[Session] = None) -> Dict[str, Any]:
        """Enable/disable a memory (disabled memories are excluded from retrieval)."""
        close_db = False
        if db is None:
            db = next(get_sync_db())
            close_db = True
        try:
            mem = db.get(LTMModel, memory_id)
            if not mem:
                return {"status": "error", "error": "Không tìm thấy ghi nhớ"}
            mem.enabled = enabled
            mem.updated_at = datetime.utcnow()
            db.add(mem)
            db.commit()
            return {"status": "success", "memory": self._serialize(mem)}
        finally:
            if close_db:
                db.close()

    def delete(self, memory_id: str, db: Optional[Session] = None) -> Dict[str, Any]:
        """Permanently delete a memory."""
        close_db = False
        if db is None:
            db = next(get_sync_db())
            close_db = True
        try:
            mem = db.get(LTMModel, memory_id)
            if not mem:
                return {"status": "error", "error": "Không tìm thấy ghi nhớ"}
            db.delete(mem)
            db.commit()
            return {"status": "success", "id": memory_id}
        finally:
            if close_db:
                db.close()

    def export_all(self, db: Optional[Session] = None) -> List[Dict[str, Any]]:
        """Export every memory (including disabled) for backup/portability."""
        return self.list_all(include_disabled=True, limit=100000, db=db)
