"""Agent Loop — core agent runtime.

The agent loop:
1. Receives user input and session context.
2. Assembles context (conversation history + short-term memory).
3. Calls LLM with available tools.
4. If LLM returns text → final answer.
5. If LLM requests tool calls:
   a. Validate arguments
   b. Check permissions (may trigger HITL)
   c. Execute tools (or pause for approval)
   d. Log results and feed back to LLM
   e. Repeat loop (up to max iterations)

The loop is deterministic and fully audited via episodic memory.
"""

import uuid
from typing import Dict, Any, List, Optional

from sqlalchemy.orm import Session

from ..services.llm import LLMProvider, Message as LLMMessage, ToolCall as LLMToolCall
from ..services.tool_registry import ToolRegistry
from ..services.tool_executor import ToolExecutor
from ..services.short_term_memory import ShortTermMemoryManager
from ..services.episodic_memory import EpisodicMemory
from ..core.logging_config import logger
from ..models.database import get_sync_db, Message as MessageModel, AuditLog


class AgentLoop:
    """Main agent runtime loop."""

    def __init__(self, llm_provider: Optional[LLMProvider] = None):
        self.llm = llm_provider or LLMProvider()
        self.registry = ToolRegistry.get_instance()
        self.executor = ToolExecutor()
        self.stm = ShortTermMemoryManager.get_instance()
        self.episodic = EpisodicMemory.get_instance()
        self.max_iterations = 10

    def run(self, session_id: str, user_input: str, db: Session) -> Dict[str, Any]:
        """Execute the agent loop for a user message.

        Returns:
        {
            "response": str,
            "status": "completed" | "pending_approval" | "error",
            "approval_id": str (if pending),
            "iterations": int,
            "tool_calls": List[dict]
        }
        """
        try:
            # Save user message
            user_msg = MessageModel(
                id=str(uuid.uuid4()),
                session_id=session_id,
                role="user",
                content=user_input,
            )
            db.add(user_msg)

            # Load conversation history
            history = self._get_history(session_id, db)
            messages = self._build_initial_messages(history, user_input)

            # Load short-term memory context
            stm_context = self.stm.get_all(session_id, db)
            if stm_context:
                stm_summary = "\n".join([f"{k}: {v}" for k, v in stm_context.items()])
                messages.append({"role": "system", "content": f"Session context:\n{stm_summary}"})

            tool_calls_made = []
            iterations = 0

            while iterations < self.max_iterations:
                iterations += 1

                # Call LLM with tools
                response = self.llm.chat(
                    messages=messages,
                    tools=self._get_tools_schema(),
                    temperature=0.7
                )

                # If no tool calls, we have final response
                if not response.tool_calls:
                    final_response = response.content or "No response generated."
                    # Save assistant message
                    assistant_msg = MessageModel(
                        id=str(uuid.uuid4()),
                        session_id=session_id,
                        role="assistant",
                        content=final_response,
                    )
                    db.add(assistant_msg)
                    db.commit()
                    self.episodic.log_event(
                        session_id=session_id,
                        actor="agent_loop",
                        action="response_completed",
                        details={"iterations": iterations, "tool_calls": len(tool_calls_made)},
                        db=db
                    )
                    return {
                        "response": final_response,
                        "status": "completed",
                        "iterations": iterations,
                        "tool_calls": tool_calls_made,
                    }

                # Process tool calls (can be multiple; handle sequentially for now)
                for tc in response.tool_calls:
                    tool_name = tc.name
                    arguments = tc.arguments

                    # Validate arguments
                    validation_errors = self.registry.validate_arguments(tool_name, arguments)
                    if validation_errors:
                        err_msg = {"error": f"Invalid arguments: {', '.join(validation_errors)}"}
                        messages.append({"role": "assistant", "content": f"Tool call error: {err_msg}"})
                        continue

                    # Execute tool (may return pending approval)
                    exec_result = self.executor.execute(tool_name, arguments, session_id, db=db)

                    tool_calls_made.append({
                        "tool": tool_name,
                        "arguments": arguments,
                        "status": exec_result["status"],
                        "result": exec_result if exec_result["status"] != "pending_approval" else None,
                    })

                    if exec_result["status"] == "pending_approval":
                        # HITL: return immediately with approval request
                        db.commit()
                        return {
                            "response": "Cần xác nhận hành động",
                            "status": "pending_approval",
                            "approval_id": exec_result["approval_id"],
                            "iterations": iterations,
                            "tool_calls": tool_calls_made,
                        }
                    elif exec_result["status"] == "denied":
                        # Permission denied, tell the LLM
                        messages.append({
                            "role": "assistant",
                            "content": f"Action denied: {exec_result.get('reason', 'No reason given')}"
                        })
                    elif exec_result["status"] == "error":
                        messages.append({
                            "role": "assistant",
                            "content": f"Tool error: {exec_result.get('error', 'Unknown error')}"
                        })
                    else:
                        # Success, add tool result to conversation
                        result_str = str(exec_result.get("result", {}))
                        messages.append({
                            "role": "assistant",
                            "content": f"Tool {tool_name} returned: {result_str}"
                        })

                db.commit()

            # Max iterations reached
            self.episodic.log_event(
                session_id=session_id,
                actor="agent_loop",
                action="max_iterations_reached",
                details={"iterations": iterations},
                db=db
            )
            db.add(AuditLog(
                id=str(uuid.uuid4()),
                session_id=session_id,
                actor="agent_loop",
                action="max_iterations",
                details={"iterations": iterations}
            ))
            db.commit()
            return {
                "response": "Đã đạt giới hạn số bước xử lý. Vui lòng thử lại với câu hỏi đơn giản hơn.",
                "status": "error",
                "iterations": iterations,
                "tool_calls": tool_calls_made,
            }

        except Exception as e:
            logger.error(f"Agent loop error: {e}")
            db.rollback()
            self.episodic.log_event(
                session_id=session_id,
                actor="agent_loop",
                action="error",
                details={"error": str(e)},
                db=db
            )
            db.add(AuditLog(
                id=str(uuid.uuid4()),
                session_id=session_id,
                actor="agent_loop",
                action="error",
                details={"error": str(e)}
            ))
            db.commit()
            return {
                "response": f"Xin lỗi, đã xảy ra lỗi: {str(e)}",
                "status": "error",
                "iterations": 0,
                "tool_calls": [],
            }

    def run_after_approval(self, session_id: str, tool_call: Dict[str, Any],
                           db: Session, stm=None, episodic=None) -> Dict[str, Any]:
        """Resume agent loop after user approved a tool call."""
        if stm is None:
            stm = self.stm
        if episodic is None:
            episodic = self.episodic

        # The tool_call dict should contain tool_name, arguments
        tool_name = tool_call.get("tool")
        arguments = tool_call.get("arguments", {})

        # Execute the approved tool
        exec_result = self.executor.dispatch_after_approval(tool_name, arguments, session_id, db=db)

        # Continue LLM loop with this result
        history = self._get_history(session_id, db)
        messages = self._build_initial_messages(history, history[-1]["content"] if history else "")
        # Add tool result
        result_str = str(exec_result.get("result", {}))
        messages.append({
            "role": "assistant",
            "content": f"Tool {tool_name} returned: {result_str}"
        })

        # Continue iteration
        try:
            response = self.llm.chat(messages=messages, tools=self._get_tools_schema(), temperature=0.7)
        except Exception as e:
            return {"response": f"LLM error after approval: {str(e)}", "status": "error"}

        if not response.tool_calls:
            final_response = response.content or "No response."
            assistant_msg = MessageModel(
                id=str(uuid.uuid4()),
                session_id=session_id,
                role="assistant",
                content=final_response,
            )
            db.add(assistant_msg)
            db.commit()
            return {"response": final_response, "status": "completed"}

        # More tool calls: recurse once (could loop but keeping simple)
        # In a full implementation, this would go back to the main loop
        return {
            "response": response.content or "",
            "status": "completed",
            "note": "Additional tool calls after approval not implemented in this simplified resume"
        }

    def _get_history(self, session_id: str, db: Session, limit: int = 20) -> List[Dict]:
        """Get conversation history for context."""
        stmt = MessageModel.__table__.select().where(
            MessageModel.session_id == session_id
        ).order_by(MessageModel.created_at).limit(limit)
        result = db.execute(stmt)
        messages = result.fetchall()
        return [
            {"role": msg.role, "content": msg.content}
            for msg in messages
        ]

    def _build_initial_messages(self, history: List[Dict], current_input: str) -> List[Dict]:
        """Build initial message list with history and current user input."""
        messages = []
        # Include recent history (exclude current since we'll add it separately)
        for msg in history[-10:]:
            if msg["role"] in ["user", "assistant"]:
                messages.append(msg)
        # Add current user message if not already in history
        if not history or history[-1]["content"] != current_input or history[-1]["role"] != "user":
            messages.append({"role": "user", "content": current_input})
        return messages

    def _get_tools_schema(self) -> List[Dict]:
        """Build OpenAI tools schema from registry."""
        tools = []
        for tool in self.registry.list_tools():
            tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                }
            })
        return tools
