"""Risk Classifier — determines risk level for tool calls.

The classifier uses static tool metadata plus argument analysis to produce
a risk score and a permission decision recommendation (allow/ask/deny).
"""

import json
import re
import unicodedata
import urllib.parse
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
            r"\bmkfs\b",               # format filesystem
            r"\bdd\s+if=",             # raw disk write
            r"(?i)\bformat\s+[a-z]:",  # windows format drive
            r"(?i)\bdel\s+/[a-z]\s",   # windows recursive/force delete
            r"(?i)\brmdir\s+/s",       # windows recursive rmdir
        ]
        # Argument keys treated as file paths for path-based deny checks.
        self.path_keys = {
            "path", "file", "filename", "snapshot", "target", "dest", "destination", "url",
        }
        # Submit/destructive intents in a browser click/type target raise risk.
        self.sensitive_action_words = (
            "submit", "pay", "purchase", "buy", "checkout", "delete", "remove",
            "send", "confirm", "transfer", "login", "sign in", "đăng nhập", "gửi", "xóa",
        )

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

    @staticmethod
    def _normalize_value(value: str) -> str:
        """Normalize a string to defeat encoding-based bypass."""
        decoded = urllib.parse.unquote(value)
        return unicodedata.normalize("NFKC", decoded)

    def _check_deny_patterns(self, arguments: Dict[str, Any], matched_rules: List[str]) -> bool:
        """Check arguments against deny patterns.

        Path-based patterns are tested only against path-like fields so that a
        document's content mentioning "password" is not mistaken for a secret
        file path. Command-based patterns are tested against all text.
        Values are normalized (URL-decoded, Unicode NFKC) before matching.
        """
        for key, value in arguments.items():
            if key in self.path_keys and isinstance(value, str):
                normalized = self._normalize_value(value)
                for pattern in self.path_deny_patterns:
                    if re.search(pattern, normalized, re.IGNORECASE):
                        matched_rules.append(f"deny_path:{pattern}")
                        return True

        all_text = self._normalize_value(json.dumps(arguments, ensure_ascii=False))
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

        elif tool_name in ("browser.click", "browser.type"):
            target = str(arguments.get("target", "")).lower()
            submitting = bool(arguments.get("submit", False))
            if submitting or any(w in target for w in self.sensitive_action_words):
                risk = max(risk, RiskLevel.HIGH.value)
                matched_rules.append("browser_sensitive_action")
                explanation_parts.append("Browser action may submit/modify account data")

        elif tool_name == "sandbox.shell":
            # Static analysis of the shell command (REQUIREMENTS 15.4).
            from .command_analyzer import analyze_command
            command = str(arguments.get("command", ""))
            analysis = analyze_command(command)
            if analysis["network"]:
                risk = max(risk, RiskLevel.HIGH.value)
                matched_rules.append("sandbox_shell_network")
                explanation_parts.append("Shell command uses network")
            if analysis["writes_fs"]:
                risk = max(risk, RiskLevel.MEDIUM.value)
                matched_rules.append("sandbox_shell_writes")
                explanation_parts.append("Shell command writes filesystem")
            if analysis["reads_outside_workspace"]:
                risk = max(risk, RiskLevel.HIGH.value)
                matched_rules.append("sandbox_shell_outside_workspace")
                explanation_parts.append("Shell command references paths outside workspace")
            risk = max(risk, analysis["risk_level"])
            matched_rules.extend(analysis["matched_rules"])

        elif tool_name == "sandbox.python":
            if arguments.get("allow_network"):
                risk = max(risk, RiskLevel.HIGH.value)
                matched_rules.append("sandbox_python_network")
                explanation_parts.append("Python sandbox with network access (Mode C)")

        elif tool_name == "sandbox.install":
            # Package install always uses the network.
            risk = max(risk, RiskLevel.HIGH.value)
            matched_rules.append("sandbox_install_network")
            explanation_parts.append("Package install requires network access")

        elif tool_name in ("desktop.click", "desktop.type", "desktop.key", "desktop.drag"):
            # Phase 10: controlling the real desktop. Escalate dangerous shapes
            # to CRITICAL; all of these already force ask_strong (base risk 2).
            if tool_name == "desktop.type":
                target = (str(arguments.get("name", "")) + " " +
                          str(arguments.get("auto_id", ""))).lower()
                secret_fields = ("password", "mật khẩu", "mat khau", "pin", "otp", "cvv", "passcode")
                if any(w in target for w in secret_fields):
                    risk = max(risk, RiskLevel.CRITICAL.value)
                    matched_rules.append("desktop_type_secret_field")
                    explanation_parts.append("Typing into a credential/OTP field")
            elif tool_name == "desktop.key":
                keys = str(arguments.get("keys", "")).lower().replace(" ", "")
                dangerous_combos = ("win+r", "ctrl+alt+del", "ctrl+alt+delete", "alt+f4", "win+e")
                if keys in dangerous_combos:
                    risk = max(risk, RiskLevel.CRITICAL.value)
                    matched_rules.append("desktop_key_system_combo")
                    explanation_parts.append("Dangerous system key combination")
            else:  # desktop.click / desktop.drag
                target = str(arguments.get("name", "")).lower()
                if any(w in target for w in self.sensitive_action_words):
                    risk = max(risk, RiskLevel.HIGH.value)
                    matched_rules.append("desktop_sensitive_action")
                    explanation_parts.append("Clicking a submit/destructive control")

        return risk
