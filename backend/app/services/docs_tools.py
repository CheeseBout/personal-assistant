"""Docs Tools — sync tool handlers for the docs.* tool category.

Contract matches gmail_tools/browser_tools:
    def docs_xxx(arguments: dict, session_id: str) -> dict

Security:
- docs.read auto-allow; create/edit require HITL.
- docs.export writes ONLY into the agent workspace (reuse file_tools._resolve_path).
- Document content is untrusted data (fenced by the agent loop).
"""

import io
from typing import Dict, Any, List

from .google_workspace_common import service_or_none, record_action, NOT_CONNECTED
from .file_tools import _resolve_path


def _docs():
    return service_or_none("docs", "v1")


def _extract_doc_text(doc: dict) -> str:
    """Flatten the Docs body content into plain text."""
    out: List[str] = []
    for el in (doc.get("body", {}).get("content") or []):
        para = el.get("paragraph")
        if not para:
            continue
        for run in (para.get("elements") or []):
            txt = run.get("textRun", {}).get("content")
            if txt:
                out.append(txt)
    return "".join(out)


def docs_read(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _docs()
    if svc is None:
        return NOT_CONNECTED
    doc_id = arguments.get("document_id", "")
    if not doc_id or not isinstance(doc_id, str):
        return {"error": "Missing or invalid 'document_id'"}
    max_chars = arguments.get("max_chars", 12000)
    try:
        max_chars = max(500, min(int(max_chars), 80000))
    except (ValueError, TypeError):
        max_chars = 12000
    try:
        doc = svc.documents().get(documentId=doc_id).execute()
        result = {
            "document_id": doc_id,
            "title": doc.get("title"),
            "content": _extract_doc_text(doc)[:max_chars],
        }
    except Exception as e:
        result = {"error": f"Docs read lỗi: {e}"}
    record_action(session_id, "docs", "read", doc_id, result)
    return result


def docs_create(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _docs()
    if svc is None:
        return NOT_CONNECTED
    title = arguments.get("title", "")
    body = arguments.get("body", "")
    if not title or not isinstance(title, str):
        return {"error": "Missing or invalid 'title'"}
    if not isinstance(body, str):
        return {"error": "Invalid 'body'"}
    try:
        doc = svc.documents().create(body={"title": title}).execute()
        doc_id = doc.get("documentId")
        if body:
            svc.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": [{"insertText": {"location": {"index": 1}, "text": body}}]},
            ).execute()
        result = {"status": "success", "document_id": doc_id, "title": title}
    except Exception as e:
        result = {"error": f"Docs create lỗi: {e}"}
    record_action(session_id, "docs", "create", title, result)
    return result


def docs_edit(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _docs()
    if svc is None:
        return NOT_CONNECTED
    doc_id = arguments.get("document_id", "")
    text = arguments.get("text", "")
    if not doc_id or not isinstance(doc_id, str):
        return {"error": "Missing or invalid 'document_id'"}
    if not isinstance(text, str) or not text:
        return {"error": "Missing or invalid 'text'"}
    mode = arguments.get("mode", "append")  # append | insert
    index = arguments.get("index", 1)
    try:
        index = int(index)
    except (ValueError, TypeError):
        index = 1
    try:
        if mode == "append":
            doc = svc.documents().get(documentId=doc_id).execute()
            end = doc.get("body", {}).get("content", [{}])[-1].get("endIndex", 1)
            loc = max(1, end - 1)
        else:
            loc = max(1, index)
        svc.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{"insertText": {"location": {"index": loc}, "text": text}}]},
        ).execute()
        result = {"status": "success", "document_id": doc_id, "mode": mode, "inserted_chars": len(text)}
    except Exception as e:
        result = {"error": f"Docs edit lỗi: {e}"}
    record_action(session_id, "docs", "edit", doc_id, result)
    return result


def docs_export(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    # Export delegates to the Drive export endpoint (Docs API has no export).
    drive = service_or_none("drive", "v3")
    if drive is None:
        return NOT_CONNECTED
    doc_id = arguments.get("document_id", "")
    dest_rel = arguments.get("dest_path", "")
    fmt = (arguments.get("format") or "txt").lower()
    if not doc_id or not dest_rel:
        return {"error": "Missing 'document_id' or 'dest_path'"}
    mime_map = {"txt": "text/plain", "pdf": "application/pdf",
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    if fmt not in mime_map:
        return {"error": f"Định dạng không hỗ trợ: {fmt} (txt/pdf/docx)"}
    try:
        abs_path = _resolve_path(dest_rel)
    except ValueError as e:
        return {"error": str(e)}
    try:
        from googleapiclient.http import MediaIoBaseDownload

        buf = io.BytesIO()
        request = drive.files().export_media(fileId=doc_id, mimeType=mime_map[fmt])
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(buf.getvalue())
        result = {"status": "success", "document_id": doc_id,
                  "saved_path": dest_rel, "format": fmt,
                  "size_bytes": abs_path.stat().st_size}
    except Exception as e:
        result = {"error": f"Docs export lỗi: {e}"}
    record_action(session_id, "docs", "export", dest_rel, result)
    return result
