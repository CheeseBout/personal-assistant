"""Shared helpers for Google Workspace connectors (Drive / Docs / Sheets).

These three services reuse the same OAuth foundation (GoogleAuth) and the same
action-log table (google_workspace_actions), so the build + logging boilerplate
lives here instead of being duplicated in each *_tools.py module.
"""

import uuid
from typing import Dict, Any, Optional

from .google_auth import GoogleAuth
from ..core.logging_config import logger
from ..core.redaction import redact_value
from ..models.database import SessionLocal, GoogleWorkspaceAction

NOT_CONNECTED = {"error": "Chưa kết nối Google. Hãy kết nối ở Google panel trước."}


def service_or_none(api_name: str, version: str):
    """Build a Google API service client, or return None if not connected."""
    try:
        return GoogleAuth.get_instance().build_service(api_name, version)
    except RuntimeError:
        return None  # not_connected
    except Exception as e:
        logger.error(f"{api_name} service build failed: {e}")
        return None


def record_action(session_id: str, service: str, action: str,
                  target: Optional[str], result: Dict[str, Any]):
    """Append a google_workspace_actions row. Never raises into the handler path."""
    try:
        status = "error" if "error" in result else "success"
        # Redact details; never persist raw bytes or large content blobs.
        details = {k: v for k, v in result.items() if k not in ("content", "values", "raw", "data")}
        db = SessionLocal()
        try:
            db.add(GoogleWorkspaceAction(
                id=str(uuid.uuid4()),
                session_id=session_id,
                service=service,
                action=action,
                target=(target or "")[:300],
                status=status,
                details_json=redact_value(details),
            ))
            db.commit()
        finally:
            db.close()
    except Exception as e:  # logging must not break tool execution
        logger.error(f"google_workspace_action log failed ({service}.{action}): {e}")
