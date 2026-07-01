"""Tool Registry — central catalog of all executable tools.

Responsibilities:
- Load tool metadata from database at startup.
- Provide tool lookup by name.
- Validate tool arguments against input schema (JSON Schema).
- List available tools with their risk levels.
"""

import json
from typing import Dict, Any, List, Optional

from ..core.logging_config import logger


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
        """Fetch all enabled tools from the database via SQLAlchemy."""
        try:
            from ..models.database import get_sync_db, Tool
            db = next(get_sync_db())
            try:
                rows = db.query(Tool).filter(Tool.enabled == True).all()
                self._tools = {}
                for row in rows:
                    try:
                        schema = json.loads(row.input_schema) if row.input_schema else {}
                    except (json.JSONDecodeError, TypeError):
                        schema = {}
                        logger.warning(f"Invalid JSON schema for tool {row.name}, using empty schema")
                    self._tools[row.name] = {
                        "id": row.id,
                        "name": row.name,
                        "description": row.description or "",
                        "input_schema": schema,
                        "risk_level": row.risk_level,
                        "requires_approval": bool(row.requires_approval),
                        "rollback_type": row.rollback_type,
                        "rollback_supported": bool(row.rollback_supported),
                        "logs_sensitive_args": bool(row.logs_sensitive_args),
                        "enabled": bool(row.enabled),
                        "created_at": str(row.created_at) if row.created_at else None,
                    }
                logger.info(f"ToolRegistry loaded {len(self._tools)} tools")
            finally:
                db.close()
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
            return []

        try:
            import jsonschema
            jsonschema.validate(arguments, schema)
            return []
        except ImportError:
            return self._validate_fallback(arguments, schema)
        except jsonschema.ValidationError as e:
            return [e.message]

    def _validate_fallback(self, arguments: Dict[str, Any], schema: Dict) -> List[str]:
        """Fallback validation when jsonschema is not installed."""
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
                    type_checks = {
                        "string": str, "integer": int, "number": (int, float),
                        "boolean": bool, "object": dict, "array": list,
                    }
                    expected = type_checks.get(expected_type)
                    if expected and not isinstance(value, expected):
                        errors.append(f"Field '{key}' must be a {expected_type}")
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
