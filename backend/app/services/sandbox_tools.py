"""Sandbox Tools — Phase 7 tool functions exposed to the agent.

Each function follows the tool contract: execute(arguments, session_id) -> dict.
Every run is recorded in the sandbox_runs table (mirroring how browser_tools log
to browser_actions) with secret-shaped substrings redacted from the stored
code/command/output preview.

Risk classification and HITL approval happen upstream in PermissionEngine /
RiskClassifier — these functions assume permission has already been granted.
"""

import uuid
import json
from typing import Dict, Any, List

from sqlalchemy.orm import Session

from .sandbox_runner import SandboxRunner
from .command_analyzer import analyze_command
from ..core.config import settings
from ..core.logging_config import logger
from ..core.redaction import redact_text
from ..models.database import get_sync_db, SandboxRun

_PREVIEW_LIMIT = 4000


def _log_run(session_id: str, kind: str, mode: str, code: str, result: Dict[str, Any]) -> None:
    """Persist a sandbox run to the viewer table (redacted, truncated)."""
    db: Session = next(get_sync_db())
    try:
        db.add(SandboxRun(
            id=str(uuid.uuid4()),
            session_id=session_id,
            kind=kind,
            mode=mode,
            code=redact_text(code or "")[:_PREVIEW_LIMIT],
            status=result.get("status", "error"),
            exit_code=result.get("exit_code"),
            killed_reason=result.get("killed_reason"),
            stdout_preview=redact_text(result.get("stdout", ""))[:_PREVIEW_LIMIT],
            stderr_preview=redact_text(result.get("stderr", ""))[:_PREVIEW_LIMIT],
            artifacts_json=result.get("artifacts", []),
            duration_ms=result.get("duration_ms"),
        ))
        db.commit()
    except Exception as e:
        logger.error(f"sandbox run logging failed: {e}")
        db.rollback()
    finally:
        db.close()


def sandbox_python(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Run Python code in the session sandbox."""
    code = arguments.get("code", "")
    allow_network = bool(arguments.get("allow_network", settings.SANDBOX_ALLOW_NETWORK_DEFAULT))
    timeout = arguments.get("timeout")
    if not code:
        return {"error": "code is required"}

    runner = SandboxRunner.get_instance()
    result = runner.run_python(code, session_id, allow_network=allow_network, timeout=timeout)
    mode = "C" if allow_network else "A"
    _log_run(session_id, "python", mode, code, result)
    return result


def sandbox_shell(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Run a shell command in the session sandbox (Mode D)."""
    command = arguments.get("command", "")
    timeout = arguments.get("timeout")
    if not command:
        return {"error": "command is required"}

    # Defense in depth: re-check the analyzer here even though the permission
    # engine already classified it. A denied shape must never execute.
    analysis = analyze_command(command)
    if analysis["denied"]:
        result = {
            "status": "denied", "exit_code": None, "killed_reason": None,
            "stdout": "", "stderr": f"Blocked by command analyzer: {analysis['explanation']}",
            "artifacts": [], "duration_ms": 0, "command": command,
        }
        _log_run(session_id, "shell", "D", command, result)
        return result

    runner = SandboxRunner.get_instance()
    allow_network = analysis["network"]
    result = runner.run_shell(command, session_id, allow_network=allow_network, timeout=timeout)
    _log_run(session_id, "shell", "D", command, result)
    return result


def sandbox_install(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """pip install packages into the session sandbox (Mode C)."""
    packages: List[str] = arguments.get("packages", [])
    if not packages or not isinstance(packages, list):
        return {"error": "packages (list) is required"}

    runner = SandboxRunner.get_instance()
    result = runner.run_install(packages, session_id)
    _log_run(session_id, "install", "C", "pip install " + " ".join(map(str, packages)), result)
    return result


def sandbox_list_artifacts(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """List files in the session sandbox."""
    return SandboxRunner.get_instance().list_artifacts(session_id)


def sandbox_read_artifact(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Read a single artifact file from the session sandbox."""
    name = arguments.get("name", "")
    if not name:
        return {"error": "name is required"}
    return SandboxRunner.get_instance().read_artifact(session_id, name)
