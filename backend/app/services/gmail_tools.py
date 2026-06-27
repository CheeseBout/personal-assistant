"""Gmail Tools — sync tool handlers for the gmail.* tool category.

Each handler matches the executor contract used by browser_tools/file_tools:
    def gmail_xxx(arguments: dict, session_id: str) -> dict
Returns a plain dict; expected failures are signalled with an ``"error"`` key
(handlers do not raise for expected failures). Google API work is delegated to
the shared GoogleAuth singleton (which builds the service client).

Security:
- Read tools (search/read/thread_summary) auto-allow; write tools (draft/send/
  label/trash) are seeded requires_approval=1 so they flow through HITL.
- Email content is untrusted data; the agent loop fences tool output already.
- gmail.send/draft args are redacted in logs (logs_sensitive_args=1).
"""

import base64
import uuid
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, Any, Optional, List

from .google_auth import GoogleAuth
from ..core.config import settings
from ..core.logging_config import logger
from ..core.redaction import redact_value
from ..models.database import SessionLocal, GmailAction

NOT_CONNECTED = {"error": "Chưa kết nối Google. Hãy kết nối ở Google panel trước."}


def _record(session_id: str, action: str, target: Optional[str], result: Dict[str, Any]):
    """Append a gmail_actions row. Never raises into the handler path."""
    try:
        status = "error" if "error" in result else "success"
        # Redact details; never persist raw bodies/recipients verbatim.
        details = {k: v for k, v in result.items() if k not in ("body", "raw")}
        db = SessionLocal()
        try:
            db.add(GmailAction(
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
        logger.error(f"gmail_action log failed ({action}): {e}")


def _svc():
    """Build the Gmail service or return None if not connected."""
    try:
        return GoogleAuth.get_instance().build_service("gmail", "v1")
    except RuntimeError:
        return None
    except Exception as e:
        logger.error(f"gmail service build failed: {e}")
        return None


def _header(payload: dict, name: str) -> str:
    for h in (payload.get("headers") or []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _extract_text(payload: dict) -> str:
    """Walk MIME parts and return concatenated text/plain (fallback to any text)."""
    def decode(data: str) -> str:
        try:
            return base64.urlsafe_b64decode(data.encode("ascii")).decode("utf-8", "replace")
        except Exception:
            return ""

    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    if mime == "text/plain" and body.get("data"):
        return decode(body["data"])
    parts = payload.get("parts") or []
    texts: List[str] = []
    for p in parts:
        texts.append(_extract_text(p))
    joined = "\n".join(t for t in texts if t)
    if joined:
        return joined
    # last resort: html or any body data
    if body.get("data"):
        return decode(body["data"])
    return ""


# --- read tools (auto-allow) --------------------------------------------------------

def gmail_search(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _svc()
    if svc is None:
        return NOT_CONNECTED
    query = arguments.get("query", "")
    if not isinstance(query, str):
        return {"error": "Invalid 'query'"}
    max_results = arguments.get("max_results", 10)
    try:
        max_results = max(1, min(int(max_results), 50))
    except (ValueError, TypeError):
        max_results = 10
    try:
        resp = svc.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()
        out = []
        for m in (resp.get("messages") or []):
            full = svc.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            p = full.get("payload", {})
            out.append({
                "id": m["id"],
                "thread_id": full.get("threadId"),
                "from": _header(p, "From"),
                "subject": _header(p, "Subject"),
                "date": _header(p, "Date"),
                "snippet": full.get("snippet", ""),
            })
        result = {"query": query, "count": len(out), "messages": out}
    except Exception as e:
        result = {"error": f"Gmail search lỗi: {e}"}
    _record(session_id, "search", query, result)
    return result


def gmail_read(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _svc()
    if svc is None:
        return NOT_CONNECTED
    msg_id = arguments.get("message_id", "")
    if not msg_id or not isinstance(msg_id, str):
        return {"error": "Missing or invalid 'message_id'"}
    max_chars = arguments.get("max_chars", 8000)
    try:
        max_chars = max(500, min(int(max_chars), 50000))
    except (ValueError, TypeError):
        max_chars = 8000
    try:
        full = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
        p = full.get("payload", {})
        result = {
            "id": msg_id,
            "thread_id": full.get("threadId"),
            "from": _header(p, "From"),
            "to": _header(p, "To"),
            "subject": _header(p, "Subject"),
            "date": _header(p, "Date"),
            "body": _extract_text(p)[:max_chars],
        }
    except Exception as e:
        result = {"error": f"Gmail read lỗi: {e}"}
    _record(session_id, "read", msg_id, result)
    return result


def gmail_thread_summary(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _svc()
    if svc is None:
        return NOT_CONNECTED
    thread_id = arguments.get("thread_id", "")
    if not thread_id or not isinstance(thread_id, str):
        return {"error": "Missing or invalid 'thread_id'"}
    max_chars = arguments.get("max_chars", 12000)
    try:
        max_chars = max(1000, min(int(max_chars), 60000))
    except (ValueError, TypeError):
        max_chars = 12000
    try:
        thread = svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
        msgs = []
        for m in (thread.get("messages") or []):
            p = m.get("payload", {})
            msgs.append({
                "from": _header(p, "From"),
                "date": _header(p, "Date"),
                "subject": _header(p, "Subject"),
                "text": _extract_text(p),
            })
        # Concatenate; the LLM in the loop does the actual summarization.
        combined = "\n\n---\n\n".join(
            f"From: {x['from']}\nDate: {x['date']}\n{x['text']}" for x in msgs
        )[:max_chars]
        result = {"thread_id": thread_id, "message_count": len(msgs), "combined_text": combined}
    except Exception as e:
        result = {"error": f"Gmail thread lỗi: {e}"}
    _record(session_id, "thread_summary", thread_id, result)
    return result


def gmail_list_attachments(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _svc()
    if svc is None:
        return NOT_CONNECTED
    msg_id = arguments.get("message_id", "")
    if not msg_id or not isinstance(msg_id, str):
        return {"error": "Missing or invalid 'message_id'"}
    try:
        full = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
        atts = []

        def walk(part):
            filename = part.get("filename") or ""
            body = part.get("body", {})
            if filename and body.get("attachmentId"):
                atts.append({
                    "filename": filename,
                    "attachment_id": body["attachmentId"],
                    "size": body.get("size", 0),
                    "mime_type": part.get("mimeType", ""),
                })
            for sub in (part.get("parts") or []):
                walk(sub)

        walk(full.get("payload", {}))
        result = {"message_id": msg_id, "count": len(atts), "attachments": atts}
    except Exception as e:
        result = {"error": f"Gmail list attachments lỗi: {e}"}
    _record(session_id, "list_attachments", msg_id, result)
    return result


def gmail_get_attachment(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _svc()
    if svc is None:
        return NOT_CONNECTED
    msg_id = arguments.get("message_id", "")
    att_id = arguments.get("attachment_id", "")
    filename = arguments.get("filename", "attachment.bin")
    if not msg_id or not att_id:
        return {"error": "Missing 'message_id' or 'attachment_id'"}
    try:
        att = svc.users().messages().attachments().get(
            userId="me", messageId=msg_id, id=att_id
        ).execute()
        data = base64.urlsafe_b64decode(att.get("data", "").encode("ascii"))
        out_dir = Path(settings.GOOGLE_ATTACHMENT_DIR)
        if not out_dir.is_absolute():
            out_dir = (Path(__file__).parent.parent.parent / settings.GOOGLE_ATTACHMENT_DIR).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(str(filename)).name  # strip any path components
        dest = out_dir / safe_name
        dest.write_bytes(data)
        result = {"status": "success", "filename": safe_name,
                  "saved_path": str(dest), "size_bytes": len(data)}
    except Exception as e:
        result = {"error": f"Gmail get attachment lỗi: {e}"}
    _record(session_id, "get_attachment", filename, result)
    return result


# --- write tools (HITL via requires_approval) ---------------------------------------

def _build_raw(to: str, subject: str, body: str) -> str:
    msg = MIMEText(body, _charset="utf-8")
    msg["to"] = to
    msg["subject"] = subject
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def gmail_draft(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _svc()
    if svc is None:
        return NOT_CONNECTED
    to = arguments.get("to", "")
    subject = arguments.get("subject", "")
    body = arguments.get("body", "")
    if not to or not isinstance(to, str):
        return {"error": "Missing or invalid 'to'"}
    if not isinstance(subject, str) or not isinstance(body, str):
        return {"error": "Invalid 'subject' or 'body'"}
    try:
        raw = _build_raw(to, subject, body)
        draft = svc.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}
        ).execute()
        # Do NOT echo recipient/body back beyond what is needed.
        result = {"status": "success", "draft_id": draft.get("id"), "to": to, "subject": subject}
    except Exception as e:
        result = {"error": f"Gmail draft lỗi: {e}"}
    _record(session_id, "draft", to, result)
    return result


def gmail_send(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _svc()
    if svc is None:
        return NOT_CONNECTED
    to = arguments.get("to", "")
    subject = arguments.get("subject", "")
    body = arguments.get("body", "")
    if not to or not isinstance(to, str):
        return {"error": "Missing or invalid 'to'"}
    if not isinstance(subject, str) or not isinstance(body, str):
        return {"error": "Invalid 'subject' or 'body'"}
    try:
        raw = _build_raw(to, subject, body)
        sent = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
        result = {"status": "success", "message_id": sent.get("id"),
                  "thread_id": sent.get("threadId"), "to": to, "subject": subject}
    except Exception as e:
        result = {"error": f"Gmail send lỗi: {e}"}
    _record(session_id, "send", to, result)
    return result


def gmail_label(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _svc()
    if svc is None:
        return NOT_CONNECTED
    msg_id = arguments.get("message_id", "")
    if not msg_id or not isinstance(msg_id, str):
        return {"error": "Missing or invalid 'message_id'"}
    add = arguments.get("add_labels") or []
    remove = arguments.get("remove_labels") or []
    if not isinstance(add, list) or not isinstance(remove, list):
        return {"error": "'add_labels'/'remove_labels' must be arrays"}
    if not add and not remove:
        return {"error": "Provide at least one of 'add_labels' / 'remove_labels'"}
    try:
        updated = svc.users().messages().modify(
            userId="me", id=msg_id,
            body={"addLabelIds": [str(x) for x in add],
                  "removeLabelIds": [str(x) for x in remove]},
        ).execute()
        result = {"status": "success", "message_id": msg_id,
                  "label_ids": updated.get("labelIds", [])}
    except Exception as e:
        result = {"error": f"Gmail label lỗi: {e}"}
    _record(session_id, "label", msg_id, result)
    return result


def gmail_trash(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _svc()
    if svc is None:
        return NOT_CONNECTED
    msg_id = arguments.get("message_id", "")
    if not msg_id or not isinstance(msg_id, str):
        return {"error": "Missing or invalid 'message_id'"}
    try:
        # trash (reversible) — never permanent delete in this phase.
        svc.users().messages().trash(userId="me", id=msg_id).execute()
        result = {"status": "success", "message_id": msg_id, "trashed": True}
    except Exception as e:
        result = {"error": f"Gmail trash lỗi: {e}"}
    _record(session_id, "trash", msg_id, result)
    return result


