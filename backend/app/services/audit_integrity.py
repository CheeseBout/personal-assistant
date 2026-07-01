"""Audit log integrity — hash chain for tamper detection."""

import hashlib
import json
import uuid
from datetime import datetime

from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from ..models.database import AuditLog


def _compute_hash(prev_hash: str, action: str, details: dict) -> str:
    payload = f"{prev_hash or ''}|{action}|{json.dumps(details or {}, sort_keys=True, default=str)}"
    return hashlib.sha256(payload.encode()).hexdigest()


def create_audit_entry(session_id: str, actor: str, action: str, details: dict, db: Session) -> AuditLog:
    """Create an AuditLog entry with hash chain integrity."""
    last = db.execute(
        select(AuditLog.prev_hash).order_by(desc(AuditLog.timestamp)).limit(1)
    ).scalar()

    new_hash = _compute_hash(last, action, details)
    entry = AuditLog(
        id=str(uuid.uuid4()),
        session_id=session_id,
        actor=actor,
        action=action,
        details=details,
        prev_hash=new_hash,
    )
    db.add(entry)
    return entry
