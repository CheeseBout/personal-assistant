"""Tool Registry — central catalog of all executable tools.

Responsibilities:
- Load tool metadata from database at startup.
- Provide tool lookup by name.
- Validate tool arguments against input schema (JSON Schema draft 4).
- List available tools with their risk levels.
"""

import json
from typing import Dict, Any, List, Optional
import sqlite3
from pathlib import Path

from ..core.logging_config import logger

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "db" / "agent.db"


class ToolRegistry:
    """Singleton registry of available tools."""

    _instance: Optional["ToolRegistry"] = None

    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._initialized = False

    @classmethod
    def get_instance(cls) -> "ToolRegistry":
        if cls._instance is None:
            cls._instance = ToolRegistry()
        return cls._instance

    def initialize(self):
        """Load tool definitions from database. Call at app startup."""
        if self._initialized:
            return
        self._load_tools()
        self._initialized = True

    def _load_tools(self):
        """Fetch all enabled tools from the database."""
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tools WHERE enabled = 1")
            rows = cursor.fetchall()
            conn.close()

            self._tools = {}
            for row in rows:
                try:
                    schema = json.loads(row["input_schema"]) if row["input_schema"] else {}
                except json.JSONDecodeError:
                    schema = {}
                    logger.warning(f"Invalid JSON schema for tool {row['name']}, using empty schema")
                self._tools[row["name"]] = {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row["description"] or "",
                    "input_schema": schema,
                    "risk_level": row["risk_level"],
                    "requires_approval": bool(row["requires_approval"]),
                    "rollback_type": row["rollback_type"],
                    "rollback_supported": bool(row["rollback_supported"]),
                    "logs_sensitive_args": bool(row["logs_sensitive_args"]),
                    "enabled": bool(row["enabled"]),
                    "created_at": row["created_at"],
                }
            logger.info(f"ToolRegistry loaded {len(self._tools)} tools")
        except Exception as e:
            logger.error(f"Failed to load tools from database: {e}")
            self._tools = {}

    def get_tool(self, name: str) -> Optional[Dict[str, Any]]:
        return self._tools.get(name)

    def list_tools(self) -> List[Dict[str, Any]]:
        return list(self._tools.values())

    def validate_arguments(self, tool_name: str, arguments: Dict[str, Any]) -> List[str]:
        """Validate arguments against the tool's input schema. Return list of errors."""
        tool = self.get_tool(tool_name)
        if not tool:
            return [f"Unknown tool: {tool_name}"]

        schema = tool["input_schema"]
        if not schema:
            return []  # No schema to validate against

        errors = []
        required = schema.get("required", [])
        properties = schema.get("properties", {})

        for field in required:
            if field not in arguments:
                errors.append(f"Missing required field: {field}")

        for key, value in arguments.items():
            if key in properties:
                expected_type = properties[key].get("type")
                if expected_type:
                    if expected_type == "string" and not isinstance(value, str):
                        errors.append(f"Field '{key}' must be a string")
                    elif expected_type == "integer" and not isinstance(value, int):
                        errors.append(f"Field '{key}' must be an integer")
                    elif expected_type == "number" and not isinstance(value, (int, float)):
                        errors.append(f"Field '{key}' must be a number")
                    elif expected_type == "boolean" and not isinstance(value, bool):
                        errors.append(f"Field '{key}' must be a boolean")
                    elif expected_type == "object" and not isinstance(value, dict):
                        errors.append(f"Field '{key}' must be an object")
                    elif expected_type == "array" and not isinstance(value, list):
                        errors.append(f"Field '{key}' must be an array")

        return errors

    def requires_approval(self, tool_name: str) -> bool:
        tool = self.get_tool(tool_name)
        return tool["requires_approval"] if tool else False

    def get_risk_level(self, tool_name: str) -> int:
        tool = self.get_tool(tool_name)
        return tool["risk_level"] if tool else 99

    def get_rollback_info(self, tool_name: str) -> Dict[str, Any]:
        tool = self.get_tool(tool_name)
        if not tool:
            return {"supported": False, "type": "irreversible"}
        return {
            "supported": tool["rollback_supported"],
            "type": tool["rollback_type"],
        }
