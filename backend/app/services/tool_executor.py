"""Tool Executor — routes tool calls to their implementation and logs execution.

Each tool executor module should expose:
    execute(arguments: dict, session_id: str) -> dict

The tool_executor calls the appropriate function and logs the result to
episodic memory.
"""

from typing import Dict, Any

from .tool_registry import ToolRegistry
from .permission_engine import PermissionEngine
from .episodic_memory import EpisodicMemory
from .file_tools import file_read, file_write, file_list, file_delete, file_undo
from .rag_tool import execute_rag_search
from ..core.logging_config import logger
from ..core.redaction import redact_arguments

# Mapping tool name -> executor function
TOOL_EXECUTORS = {
    "file.read": file_read,
    "file.write": file_write,
    "file.list": file_list,
    "file.delete": file_delete,
    "file.undo": file_undo,
    "rag.search": execute_rag_search,
}


class ToolExecutor:
    """Central dispatcher for tool execution with permission integration."""

    def __init__(self):
        self.registry = ToolRegistry.get_instance()
        self.permission = PermissionEngine()
        self.episodic = EpisodicMemory.get_instance()

    def _safe_args(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Redact arguments for logging, honoring the tool's sensitivity flag."""
        tool = self.registry.get_tool(tool_name)
        sensitive = bool(tool and tool.get("logs_sensitive_args"))
        return redact_arguments(arguments, sensitive=sensitive)

    def execute(self, tool_name: str, arguments: Dict[str, Any], session_id: str,
                db=None) -> Dict[str, Any]:
        """Execute a tool call, checking permissions first.

        Flow:
        1. Check permission (may return pending approval)
        2. If pending, return {"status": "pending", "approval_id": ...}
        3. If allowed, dispatch to executor and log result
        4. Return result
        """
        # Permission check
        perm_result = self.permission.check_and_log(session_id, tool_name, arguments, db=db)
        if perm_result["decision"] == "deny":
            return {
                "status": "denied",
                "tool": tool_name,
                "reason": perm_result["explanation"],
                "risk_level": perm_result["risk_level"],
            }

        if perm_result["decision"] in ("ask", "ask_strong"):
            return {
                "status": "pending_approval",
                "tool": tool_name,
                "approval_id": perm_result["approval_id"],
                "reason": perm_result["explanation"],
                "risk_level": perm_result["risk_level"],
            }

        # permission approved (auto), proceed
        executor = TOOL_EXECUTORS.get(tool_name)
        if not executor:
            err = {"error": f"Unknown tool: {tool_name}"}
            self.episodic.log_event(
                session_id=session_id,
                actor="tool_executor",
                action="tool_error",
                details={"tool": tool_name, "error": "unknown tool"},
                db=db
            )
            return {"status": "error", **err}

        try:
            result = executor(arguments, session_id)
            # Log successful/failed execution
            self.episodic.log_event(
                session_id=session_id,
                actor="tool_executor",
                action="tool_executed",
                details={
                    "tool": tool_name,
                    "arguments": self._safe_args(tool_name, arguments),
                    "result_status": "success" if "error" not in result else "error",
                },
                db=db
            )
            return {"status": "success", "tool": tool_name, "result": result}
        except Exception as e:
            logger.error(f"Tool execution failed {tool_name}: {e}")
            self.episodic.log_event(
                session_id=session_id,
                actor="tool_executor",
                action="tool_error",
                details={"tool": tool_name, "arguments": self._safe_args(tool_name, arguments), "error": str(e)},
                db=db
            )
            return {"status": "error", "tool": tool_name, "error": str(e)}

    def dispatch_after_approval(self, tool_name: str, arguments: Dict[str, Any],
                                session_id: str, db=None) -> Dict[str, Any]:
        """Execute tool after user approval. Skips permission re-check."""
        executor = TOOL_EXECUTORS.get(tool_name)
        if not executor:
            return {"status": "error", "error": f"Unknown tool: {tool_name}"}

        try:
            result = executor(arguments, session_id)
            self.episodic.log_event(
                session_id=session_id,
                actor="tool_executor",
                action="tool_executed_after_approval",
                details={"tool": tool_name, "result_status": "success" if "error" not in result else "error"},
                db=db
            )
            return {"status": "success", "tool": tool_name, "result": result}
        except Exception as e:
            logger.error(f"Tool execution (after approval) failed {tool_name}: {e}")
            self.episodic.log_event(
                session_id=session_id,
                actor="tool_executor",
                action="tool_error",
                details={"tool": tool_name, "error": str(e)},
                db=db
            )
            return {"status": "error", "tool": tool_name, "error": str(e)}
