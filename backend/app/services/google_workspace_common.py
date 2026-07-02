"""Shared helpers for Google Workspace connectors (Drive / Docs / Sheets).

These three services reuse the same OAuth foundation (GoogleAuth) and the same
action-log table (google_workspace_actions), so the build + logging boilerplate
lives here instead of being duplicated in each *_tools.py module.
"""

import time
import uuid
from typing import Dict, Any, Optional

from .google_auth import GoogleAuth
from ..core.config import settings
from ..core.logging_config import logger
from ..core.redaction import redact_text, redact_value
from ..models.database import SessionLocal, GoogleWorkspaceAction

NOT_CONNECTED = {"error": "Chưa kết nối Google. Hãy kết nối ở Google panel trước."}

# HTTP statuses worth retrying (rate limit + transient backend errors).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3


def service_or_none(api_name: str, version: str):
    """Build a Google API service client, or return None if not connected."""
    try:
        return GoogleAuth.get_instance().build_service(api_name, version)
    except RuntimeError:
        return None  # not_connected
    except Exception as e:
        logger.error(f"{api_name} service build failed: {e}")
        return None


def execute_with_retry(request):
    """Execute a googleapiclient request with retry on transient errors.

    Retries 429/5xx with exponential backoff. Non-retryable errors and the
    final attempt re-raise so the caller's handler builds the error dict.
    """
    from googleapiclient.errors import HttpError
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            return request.execute()
        except HttpError as e:
            status = getattr(getattr(e, "resp", None), "status", None)
            if status not in _RETRYABLE_STATUS or attempt == _MAX_RETRIES - 1:
                raise
            last_exc = e
            wait = 2 ** attempt
            logger.warning(f"Google API transient error {status} (attempt {attempt + 1}), retrying in {wait}s")
            time.sleep(wait)
    if last_exc:
        raise last_exc


def safe_error(prefix: str, exc: Exception) -> Dict[str, Any]:
    """Build an error dict with the exception text redacted before it reaches the LLM.

    Google exceptions can embed request URLs/params; redact_text strips secret
    shapes so nothing sensitive is fed back to the model via the fenced result.
    """
    return {"error": f"{prefix}: {redact_text(str(exc))}"}


def max_download_bytes() -> int:
    return settings.GOOGLE_MAX_DOWNLOAD_MB * 1024 * 1024


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
