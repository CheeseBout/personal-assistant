"""File Tools — workspace file operations with safety constraints.

All file operations are sandboxed to the workspace directory. Directory
traversal is blocked. Snapshots are created before destructive operations
to support undo.
"""

import os
import shutil
import time
from pathlib import Path
from typing import Dict, Any

from ..core.config import settings
from ..core.logging_config import logger

# Workspace root: /data/workspace/
WORKSPACE_ROOT = Path(settings.UPLOAD_DIR).parent / "workspace"
WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

SNAPSHOT_ROOT = WORKSPACE_ROOT / ".snapshots"
SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)


def _resolve_path(rel_path: str) -> Path:
    """Resolve relative path within workspace, blocking directory traversal."""
    if not rel_path:
        return WORKSPACE_ROOT
    safe_path = (WORKSPACE_ROOT / rel_path).resolve()
    if not str(safe_path).startswith(str(WORKSPACE_ROOT.resolve())):
        raise ValueError(f"Path outside workspace not allowed: {rel_path}")
    return safe_path


def _create_snapshot(path: Path, session_id: str) -> str:
    """Create snapshot before destructive operation. Returns snapshot path."""
    if not path.exists():
        return ""
    snapshot_dir = SNAPSHOT_ROOT / session_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time())
    rel_name = str(path.relative_to(WORKSPACE_ROOT)).replace("/", "_").replace("\\", "_")
    snapshot_path = snapshot_dir / f"{rel_name}_{timestamp}"
    shutil.copy2(path, snapshot_path)
    return str(snapshot_path.relative_to(WORKSPACE_ROOT))


def file_read(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Read file contents."""
    rel_path = arguments.get("path", "")
    try:
        path = _resolve_path(rel_path)
        if not path.exists():
            return {"error": "File not found", "path": rel_path}
        if path.is_dir():
            return {"error": "Path is a directory, not a file", "path": rel_path}
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read(100_000)  # Limit to 100KB
        return {"content": content, "path": rel_path, "size": len(content)}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"file.read error: {e}")
        return {"error": f"Failed to read file: {str(e)}"}


def file_write(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Write content to file. Creates snapshot if file exists."""
    rel_path = arguments.get("path", "")
    content = arguments.get("content", "")
    try:
        path = _resolve_path(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Snapshot before overwriting
        snapshot_path = ""
        if path.exists():
            snapshot_path = _create_snapshot(path, session_id)

        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

        return {
            "status": "success",
            "path": rel_path,
            "bytes_written": len(content),
            "snapshot": snapshot_path if snapshot_path else None,
        }
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"file.write error: {e}")
        return {"error": f"Failed to write file: {str(e)}"}


def file_list(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """List directory contents."""
    rel_path = arguments.get("path", "")
    try:
        dir_path = _resolve_path(rel_path)
        if not dir_path.exists():
            return {"error": "Directory not found", "path": rel_path}
        if not dir_path.is_dir():
            return {"error": "Path is not a directory", "path": rel_path}

        entries = []
        for item in sorted(dir_path.iterdir()):
            if item.name.startswith("."):
                continue  # Skip hidden files/snapshots
            entries.append({
                "name": item.name,
                "type": "directory" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else 0,
            })

        return {"entries": entries, "path": rel_path, "count": len(entries)}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"file.list error: {e}")
        return {"error": f"Failed to list directory: {str(e)}"}


def file_delete(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Delete file with snapshot."""
    rel_path = arguments.get("path", "")
    try:
        path = _resolve_path(rel_path)
        if not path.exists():
            return {"error": "File not found", "path": rel_path}
        if path.is_dir():
            return {"error": "Cannot delete directory (files only)", "path": rel_path}

        # Always snapshot before delete
        snapshot_path = _create_snapshot(path, session_id)
        path.unlink()

        return {
            "status": "success",
            "path": rel_path,
            "snapshot": snapshot_path,
            "message": "File deleted, snapshot created for undo"
        }
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"file.delete error: {e}")
        return {"error": f"Failed to delete file: {str(e)}"}
