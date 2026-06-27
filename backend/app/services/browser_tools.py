"""Browser Tools — sync tool handlers for the browser.* tool category.

Each handler matches the executor contract used by file_tools/rag_tool:
    def browser_xxx(arguments: dict, session_id: str) -> dict
Returns a plain dict; failures are signalled with an ``"error"`` key (handlers
do not raise for expected failures). The actual browser work is delegated to the
shared BrowserManager singleton (which bridges async Playwright to sync).

Every action is also recorded into the ``browser_actions`` table so the Browser
panel / timeline can show what the agent did.
"""

import json
import uuid
from typing import Dict, Any, Optional

from .browser_manager import BrowserManager
from .file_tools import _resolve_path, WORKSPACE_ROOT
from ..core.logging_config import logger
from ..core.redaction import redact_value
from ..models.database import SessionLocal, BrowserAction


def _record(session_id: str, action: str, target: Optional[str], result: Dict[str, Any]):
    """Append a browser_actions row. Never raises into the handler path."""
    try:
        status = "error" if "error" in result else "success"
        # Redact details; never persist screenshot bytes or typed secrets.
        details = {k: v for k, v in result.items() if k != "image_b64"}
        db = SessionLocal()
        try:
            db.add(BrowserAction(
                id=str(uuid.uuid4()),
                session_id=session_id,
                action=action,
                target=(target or "")[:300],
                status=status,
                details_json=redact_value(details),
            ))
            db.commit()
        finally:
            db.close()
    except Exception as e:  # logging must not break tool execution
        logger.error(f"browser_action log failed ({action}): {e}")


def _mgr() -> BrowserManager:
    return BrowserManager.get_instance()


def browser_open(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    url = arguments.get("url", "")
    if not url or not isinstance(url, str):
        return {"error": "Missing or invalid 'url'"}
    result = _mgr().open(session_id, url.strip())
    _record(session_id, "open", url, result)
    return result


def browser_observe(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    max_chars = arguments.get("max_chars", 4000)
    try:
        max_chars = int(max_chars)
    except (ValueError, TypeError):
        max_chars = 4000
    a11y = bool(arguments.get("accessibility", False))
    result = _mgr().observe(session_id, max_chars=max(500, min(max_chars, 20000)), a11y=a11y)
    _record(session_id, "observe", result.get("url"), result)
    return result


def browser_extract(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    selector = arguments.get("selector", "")
    if not selector or not isinstance(selector, str):
        return {"error": "Missing or invalid 'selector'"}
    limit = arguments.get("limit", 50)
    try:
        limit = int(limit)
    except (ValueError, TypeError):
        limit = 50
    result = _mgr().extract(session_id, selector, limit=max(1, min(limit, 200)))
    _record(session_id, "extract", selector, result)
    return result


def browser_click(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    target = arguments.get("target", "")
    if not target or not isinstance(target, str):
        return {"error": "Missing or invalid 'target'"}
    result = _mgr().click(session_id, target)
    _record(session_id, "click", target, result)
    return result


def browser_type(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    target = arguments.get("target", "")
    value = arguments.get("value", "")
    if not target or not isinstance(target, str):
        return {"error": "Missing or invalid 'target'"}
    if not isinstance(value, str):
        return {"error": "Invalid 'value'"}
    submit = bool(arguments.get("submit", False))
    result = _mgr().type_text(session_id, target, value, submit=submit)
    # target only — value is never recorded
    _record(session_id, "type", target, result)
    return result


def browser_screenshot(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    result = _mgr().screenshot(session_id)
    _record(session_id, "screenshot", result.get("url"), result)
    return result


def browser_wait(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    selector = arguments.get("selector")
    ms = arguments.get("ms")
    if ms is not None:
        try:
            ms = int(ms)
        except (ValueError, TypeError):
            ms = 1000
    result = _mgr().wait(session_id, selector=selector, ms=ms)
    _record(session_id, "wait", selector or (f"{ms}ms" if ms else None), result)
    return result


def browser_close(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    result = _mgr().close(session_id)
    _record(session_id, "close", None, result)
    return result


def browser_download(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    target = arguments.get("target", "")
    if not target or not isinstance(target, str):
        return {"error": "Missing or invalid 'target'"}
    result = _mgr().download(session_id, target)
    _record(session_id, "download", target, result)
    return result


def browser_upload(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    selector = arguments.get("selector", "")
    rel_path = arguments.get("path", "")
    if not selector or not isinstance(selector, str):
        return {"error": "Missing or invalid 'selector'"}
    if not rel_path or not isinstance(rel_path, str):
        return {"error": "Missing or invalid 'path'"}
    # Upload only from the agent workspace (REQUIREMENTS 11.5: upload from
    # authorized files only). Reuse the file-tools traversal guard.
    try:
        abs_path = _resolve_path(rel_path)
    except ValueError as e:
        return {"error": str(e)}
    if not abs_path.is_file():
        return {"error": f"File not found in workspace: {rel_path}"}
    result = _mgr().upload(session_id, selector, str(abs_path), abs_path.name)
    _record(session_id, "upload", f"{selector} <- {rel_path}", result)
    return result
