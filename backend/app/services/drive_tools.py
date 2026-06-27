"""Drive Tools — sync tool handlers for the drive.* tool category.

Contract matches gmail_tools/browser_tools:
    def drive_xxx(arguments: dict, session_id: str) -> dict
Returns a plain dict; expected failures use the "error" key (no raising).

Security:
- Read tools (search/read) auto-allow; write tools (upload/move/rename/trash)
  are seeded requires_approval=1 so they flow through HITL.
- upload reads ONLY from the agent workspace; download writes ONLY into it
  (reuse file_tools._resolve_path to block path traversal).
- delete is implemented as TRASH (recoverable), never permanent.
"""

import io
from typing import Dict, Any

from .google_workspace_common import service_or_none, record_action, NOT_CONNECTED
from .file_tools import _resolve_path
from ..core.logging_config import logger

# Google-native MIME types we export to text when reading.
_EXPORT_TEXT = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}


def _drive():
    return service_or_none("drive", "v3")


def drive_search(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _drive()
    if svc is None:
        return NOT_CONNECTED
    query = arguments.get("query", "")
    if not isinstance(query, str):
        return {"error": "Invalid 'query'"}
    max_results = arguments.get("max_results", 20)
    try:
        max_results = max(1, min(int(max_results), 100))
    except (ValueError, TypeError):
        max_results = 20
    try:
        resp = svc.files().list(
            q=query or None,
            pageSize=max_results,
            fields="files(id,name,mimeType,modifiedTime,size,owners(emailAddress))",
            orderBy="modifiedTime desc",
        ).execute()
        files = [
            {
                "id": f.get("id"),
                "name": f.get("name"),
                "mime_type": f.get("mimeType"),
                "modified": f.get("modifiedTime"),
                "size": f.get("size"),
            }
            for f in (resp.get("files") or [])
        ]
        result = {"query": query, "count": len(files), "files": files}
    except Exception as e:
        result = {"error": f"Drive search lỗi: {e}"}
    record_action(session_id, "drive", "search", query, result)
    return result


def drive_read(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _drive()
    if svc is None:
        return NOT_CONNECTED
    file_id = arguments.get("file_id", "")
    if not file_id or not isinstance(file_id, str):
        return {"error": "Missing or invalid 'file_id'"}
    max_chars = arguments.get("max_chars", 10000)
    try:
        max_chars = max(500, min(int(max_chars), 60000))
    except (ValueError, TypeError):
        max_chars = 10000
    try:
        meta = svc.files().get(fileId=file_id, fields="id,name,mimeType,size").execute()
        mime = meta.get("mimeType", "")
        text = ""
        if mime in _EXPORT_TEXT:
            data = svc.files().export(fileId=file_id, mimeType=_EXPORT_TEXT[mime]).execute()
            text = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
        elif mime.startswith("text/"):
            data = svc.files().get_media(fileId=file_id).execute()
            text = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
        else:
            text = "(Không phải file text; dùng drive.download để tải về.)"
        result = {
            "id": file_id, "name": meta.get("name"), "mime_type": mime,
            "content": text[:max_chars],
        }
    except Exception as e:
        result = {"error": f"Drive read lỗi: {e}"}
    record_action(session_id, "drive", "read", file_id, result)
    return result


def drive_download(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _drive()
    if svc is None:
        return NOT_CONNECTED
    file_id = arguments.get("file_id", "")
    dest_rel = arguments.get("dest_path", "")
    if not file_id or not isinstance(file_id, str):
        return {"error": "Missing or invalid 'file_id'"}
    if not dest_rel or not isinstance(dest_rel, str):
        return {"error": "Missing or invalid 'dest_path' (đường dẫn trong workspace)"}
    try:
        abs_path = _resolve_path(dest_rel)
    except ValueError as e:
        return {"error": str(e)}
    try:
        from googleapiclient.http import MediaIoBaseDownload

        meta = svc.files().get(fileId=file_id, fields="name,mimeType").execute()
        mime = meta.get("mimeType", "")
        buf = io.BytesIO()
        if mime in _EXPORT_TEXT:
            request = svc.files().export_media(fileId=file_id, mimeType=_EXPORT_TEXT[mime])
        else:
            request = svc.files().get_media(fileId=file_id)
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(buf.getvalue())
        result = {"status": "success", "file_id": file_id, "saved_path": dest_rel,
                  "size_bytes": abs_path.stat().st_size}
    except Exception as e:
        result = {"error": f"Drive download lỗi: {e}"}
    record_action(session_id, "drive", "download", dest_rel, result)
    return result


def drive_upload(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _drive()
    if svc is None:
        return NOT_CONNECTED
    src_rel = arguments.get("path", "")
    if not src_rel or not isinstance(src_rel, str):
        return {"error": "Missing or invalid 'path' (file trong workspace)"}
    try:
        abs_path = _resolve_path(src_rel)
    except ValueError as e:
        return {"error": str(e)}
    if not abs_path.is_file():
        return {"error": f"File không tồn tại trong workspace: {src_rel}"}
    folder_id = arguments.get("folder_id")
    name = arguments.get("name") or abs_path.name
    try:
        from googleapiclient.http import MediaFileUpload

        body = {"name": name}
        if folder_id:
            body["parents"] = [folder_id]
        media = MediaFileUpload(str(abs_path), resumable=False)
        created = svc.files().create(body=body, media_body=media, fields="id,name").execute()
        result = {"status": "success", "file_id": created.get("id"), "name": created.get("name")}
    except Exception as e:
        result = {"error": f"Drive upload lỗi: {e}"}
    record_action(session_id, "drive", "upload", f"{src_rel} -> {name}", result)
    return result


def drive_move(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _drive()
    if svc is None:
        return NOT_CONNECTED
    file_id = arguments.get("file_id", "")
    new_parent = arguments.get("folder_id", "")
    if not file_id or not new_parent:
        return {"error": "Missing 'file_id' or 'folder_id'"}
    try:
        meta = svc.files().get(fileId=file_id, fields="parents").execute()
        prev_parents = ",".join(meta.get("parents", []))
        svc.files().update(
            fileId=file_id, addParents=new_parent,
            removeParents=prev_parents, fields="id,parents",
        ).execute()
        result = {"status": "success", "file_id": file_id, "folder_id": new_parent}
    except Exception as e:
        result = {"error": f"Drive move lỗi: {e}"}
    record_action(session_id, "drive", "move", file_id, result)
    return result


def drive_rename(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _drive()
    if svc is None:
        return NOT_CONNECTED
    file_id = arguments.get("file_id", "")
    new_name = arguments.get("name", "")
    if not file_id or not new_name:
        return {"error": "Missing 'file_id' or 'name'"}
    try:
        updated = svc.files().update(fileId=file_id, body={"name": new_name}, fields="id,name").execute()
        result = {"status": "success", "file_id": file_id, "name": updated.get("name")}
    except Exception as e:
        result = {"error": f"Drive rename lỗi: {e}"}
    record_action(session_id, "drive", "rename", file_id, result)
    return result


def drive_trash(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _drive()
    if svc is None:
        return NOT_CONNECTED
    file_id = arguments.get("file_id", "")
    if not file_id or not isinstance(file_id, str):
        return {"error": "Missing or invalid 'file_id'"}
    try:
        # trash (recoverable) — never permanent delete in this phase.
        svc.files().update(fileId=file_id, body={"trashed": True}, fields="id").execute()
        result = {"status": "success", "file_id": file_id, "trashed": True}
    except Exception as e:
        result = {"error": f"Drive trash lỗi: {e}"}
    record_action(session_id, "drive", "trash", file_id, result)
    return result
