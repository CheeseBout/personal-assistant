"""Risk Classifier — determines risk level for tool calls.

The classifier uses static tool metadata plus argument analysis to produce
a risk score and a permission decision recommendation (allow/ask/deny).
"""

import json
import re
from typing import Dict, Any, List
from enum import Enum

from ..core.logging_config import logger


class RiskLevel(Enum):
    LOW = 0      # Read-only, safe operations
    MEDIUM = 1   # Creates data but undoable
    HIGH = 2     # Modifies/deletes data, hard to undo
    CRITICAL = 3 # Irreversible or exposes sensitive data


class RiskClassifier:
    """Classify tool calls by risk level and suggest permission decision."""

    def __init__(self):
        # Patterns matched against PATH-like arguments only (the file being
        # targeted). Matching these against free-form content caused false
        # positives (e.g. a document mentioning "password" got hard-denied).
        self.path_deny_patterns = [
            r"\.\./",                  # directory traversal
            r"^/etc/",                 # unix system files
            r"^[A-Za-z]:\\Windows\\",  # windows system files
            r"\.env$",                 # secret-bearing dotfiles
            r"\.pem$",
            r"\.key$",
            r"(^|[\\/])id_rsa",        # private key files
        ]
        # Patterns matched against ALL argument text — destructive command
        # shapes that should never appear regardless of which field they're in.
        self.command_deny_patterns = [
            r"rm\s+-rf\s+/",           # recursive root delete
            r":\(\)\s*\{\s*:\|:&\s*\};:",  # fork bomb
        ]
        # Argument keys treated as file paths for path-based deny checks.
        self.path_keys = {
            "path", "file", "filename", "snapshot", "target", "dest", "destination",
        }

    def classify(self, tool_name: str, arguments: Dict[str, Any], tool_metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Determine risk level and suggest decision.

        Returns:
        {
            "risk_level": 0-3,
            "requires_approval": bool,
            "denied": bool,
            "explanation": str,
            "matched_rules": List[str]
        }
        """
        matched_rules = []
        explanation_parts = []

        # Start with tool's declared risk level
        base_risk = tool_metadata.get("risk_level", 0)
        requires_approval = tool_metadata.get("requires_approval", False)

        # Check deny patterns on arguments (simple heuristic)
        denied = self._check_deny_patterns(arguments, matched_rules)
        if denied:
            return {
                "risk_level": RiskLevel.CRITICAL.value,
                "requires_approval": True,
                "denied": True,
                "explanation": "Action denied due to security policy violation",
                "matched_rules": matched_rules,
            }

        # Adjust risk based on argument sensitivity
        adjusted_risk = self._adjust_risk_by_arguments(tool_name, arguments, base_risk, matched_rules, explanation_parts)

        # If tool already requires approval, keep it
        final_requires_approval = requires_approval or adjusted_risk >= RiskLevel.MEDIUM.value

        return {
            "risk_level": adjusted_risk,
            "requires_approval": final_requires_approval,
            "denied": False,
            "explanation": "; ".join(explanation_parts) if explanation_parts else "Standard risk",
            "matched_rules": matched_rules,
        }

    def _workspace_file_exists(self, rel_path: str) -> bool:
        """Check whether a path exists inside the real workspace, safely."""
        if not rel_path:
            return False
        try:
            from .file_tools import _resolve_path
            return _resolve_path(rel_path).exists()
        except Exception:
            # Path outside workspace or unresolved — traversal is already
            # caught by deny patterns; treat as non-existing here.
            return False

    def _check_deny_patterns(self, arguments: Dict[str, Any], matched_rules: List[str]) -> bool:
        """Check arguments against deny patterns.

        Path-based patterns are tested only against path-like fields so that a
        document's content mentioning "password" is not mistaken for a secret
        file path. Command-based patterns are tested against all text.
        """
        # Path-based deny: only against fields that name a file/path.
        for key, value in arguments.items():
            if key in self.path_keys and isinstance(value, str):
                for pattern in self.path_deny_patterns:
                    if re.search(pattern, value, re.IGNORECASE):
                        matched_rules.append(f"deny_path:{pattern}")
                        return True

        # Command-based deny: destructive shapes anywhere in the arguments.
        all_text = json.dumps(arguments, ensure_ascii=False)
        for pattern in self.command_deny_patterns:
            if re.search(pattern, all_text, re.IGNORECASE):
                matched_rules.append(f"deny_command:{pattern}")
                return True
        return False

    def _adjust_risk_by_arguments(self, tool_name: str, arguments: Dict[str, Any],
                                 base_risk: int, matched_rules: List[str],
                                 explanation_parts: List[str]) -> int:
        """Adjust risk based on specific tool arguments."""
        risk = base_risk

        if tool_name == "file.delete":
            # Deleting root workspace is very risky
            path = arguments.get("path", "")
            if path in ["", ".", "workspace"]:
                risk = max(risk, RiskLevel.CRITICAL.value)
                matched_rules.append("delete_workspace_root")
                explanation_parts.append("Deleting workspace root is high risk")
            elif path.endswith((".env", ".key", ".pem")):
                risk = max(risk, RiskLevel.CRITICAL.value)
                matched_rules.append("delete_sensitive_file")
                explanation_parts.append("Deleting sensitive file")

        elif tool_name == "file.write":
            path = arguments.get("path", "")
            content = arguments.get("content", "")
            # Writing executable or config files
            if path.endswith((".sh", ".exe", ".bat", ".ps1")):
                risk = max(risk, RiskLevel.HIGH.value)
                matched_rules.append("write_executable")
                explanation_parts.append("Writing executable script")
            # Overwriting existing files is medium risk. Resolve against the
            # real workspace root rather than a hardcoded "/workspace".
            if self._workspace_file_exists(path):
                risk = max(risk, RiskLevel.MEDIUM.value)
                matched_rules.append("overwrite_existing")
                explanation_parts.append("Overwriting existing file")

        elif tool_name == "rag.search":
            # Large result counts could be expensive
            n_results = arguments.get("n_results", 10)
            if n_results and n_results > 100:
                risk = max(risk, RiskLevel.MEDIUM.value)
                matched_rules.append("large_retrieval")
                explanation_parts.append("Large retrieval requested")

        return risk
