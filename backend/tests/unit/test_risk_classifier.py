"""Unit tests for the risk classifier module."""
import pytest
import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.services.risk_classifier import RiskClassifier, RiskLevel


@pytest.fixture
def classifier():
    """Create a fresh RiskClassifier instance."""
    return RiskClassifier()


# ---------------------------------------------------------------------------
# Deny-pattern tests (path traversal and destructive commands)
# ---------------------------------------------------------------------------

class TestDenyPatterns:
    """Tests for deny-pattern matching in classify()."""

    def test_url_encoded_path_traversal_denied(self, classifier):
        result = classifier.classify(
            tool_name="file.read",
            arguments={"path": "..%2F..%2Fetc%2Fpasswd"},
            tool_metadata={"risk_level": 0, "requires_approval": False},
        )
        assert result["denied"] is True
        assert result["risk_level"] == RiskLevel.CRITICAL.value

    def test_normal_path_allowed(self, classifier):
        result = classifier.classify(
            tool_name="file.read",
            arguments={"path": "docs/report.txt"},
            tool_metadata={"risk_level": 0, "requires_approval": False},
        )
        assert result["denied"] is False

    def test_fork_bomb_denied(self, classifier):
        result = classifier.classify(
            tool_name="sandbox.shell",
            arguments={"command": ":() { :|:& };:"},
            tool_metadata={"risk_level": 0, "requires_approval": False},
        )
        assert result["denied"] is True
        assert result["risk_level"] == RiskLevel.CRITICAL.value

    def test_rm_rf_root_denied(self, classifier):
        result = classifier.classify(
            tool_name="sandbox.shell",
            arguments={"command": "rm -rf /"},
            tool_metadata={"risk_level": 0, "requires_approval": False},
        )
        assert result["denied"] is True
        assert result["risk_level"] == RiskLevel.CRITICAL.value

    def test_env_file_path_denied(self, classifier):
        result = classifier.classify(
            tool_name="file.read",
            arguments={"path": "config/.env"},
            tool_metadata={"risk_level": 0, "requires_approval": False},
        )
        assert result["denied"] is True

    def test_pem_file_path_denied(self, classifier):
        result = classifier.classify(
            tool_name="file.read",
            arguments={"path": "certs/server.pem"},
            tool_metadata={"risk_level": 0, "requires_approval": False},
        )
        assert result["denied"] is True


# ---------------------------------------------------------------------------
# Risk-adjustment tests (tool-specific argument analysis)
# ---------------------------------------------------------------------------

class TestRiskAdjustment:
    """Tests for _adjust_risk_by_arguments (via classify())."""

    def test_file_delete_env_is_critical(self, classifier):
        result = classifier.classify(
            tool_name="file.delete",
            arguments={"path": "config/.env"},
            tool_metadata={"risk_level": 1, "requires_approval": True},
        )
        # .env in path triggers deny via path_deny_patterns first
        # but if it's the file.delete tool_name, it still ends up CRITICAL
        assert result["risk_level"] == RiskLevel.CRITICAL.value

    def test_file_write_sh_is_high(self, classifier):
        with patch("app.services.risk_classifier.RiskClassifier._workspace_file_exists", return_value=False):
            result = classifier.classify(
                tool_name="file.write",
                arguments={"path": "scripts/deploy.sh", "content": "echo hello"},
                tool_metadata={"risk_level": 0, "requires_approval": False},
            )
        assert result["risk_level"] >= RiskLevel.HIGH.value
        assert "write_executable" in result["matched_rules"]

    def test_browser_click_submit_is_high(self, classifier):
        result = classifier.classify(
            tool_name="browser.click",
            arguments={"target": "Submit Order", "submit": True},
            tool_metadata={"risk_level": 0, "requires_approval": False},
        )
        assert result["risk_level"] >= RiskLevel.HIGH.value
        assert "browser_sensitive_action" in result["matched_rules"]

    def test_desktop_key_win_r_is_critical(self, classifier):
        result = classifier.classify(
            tool_name="desktop.key",
            arguments={"keys": "win+r"},
            tool_metadata={"risk_level": 2, "requires_approval": True},
        )
        assert result["risk_level"] == RiskLevel.CRITICAL.value
        assert "desktop_key_system_combo" in result["matched_rules"]

    def test_desktop_key_alt_f4_is_critical(self, classifier):
        result = classifier.classify(
            tool_name="desktop.key",
            arguments={"keys": "alt+f4"},
            tool_metadata={"risk_level": 2, "requires_approval": True},
        )
        assert result["risk_level"] == RiskLevel.CRITICAL.value

    def test_file_delete_workspace_root_is_critical(self, classifier):
        result = classifier.classify(
            tool_name="file.delete",
            arguments={"path": "."},
            tool_metadata={"risk_level": 1, "requires_approval": True},
        )
        assert result["risk_level"] == RiskLevel.CRITICAL.value
        assert "delete_workspace_root" in result["matched_rules"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge case tests for RiskClassifier."""

    def test_empty_arguments(self, classifier):
        result = classifier.classify(
            tool_name="file.read",
            arguments={},
            tool_metadata={"risk_level": 0, "requires_approval": False},
        )
        assert result["denied"] is False
        assert result["risk_level"] == 0

    def test_unknown_tool_uses_base_risk(self, classifier):
        result = classifier.classify(
            tool_name="some.unknown.tool",
            arguments={"foo": "bar"},
            tool_metadata={"risk_level": 0, "requires_approval": False},
        )
        assert result["denied"] is False
        assert result["risk_level"] == 0

    def test_requires_approval_propagated(self, classifier):
        result = classifier.classify(
            tool_name="file.read",
            arguments={"path": "readme.md"},
            tool_metadata={"risk_level": 1, "requires_approval": True},
        )
        assert result["requires_approval"] is True

    def test_medium_risk_triggers_requires_approval(self, classifier):
        with patch("app.services.risk_classifier.RiskClassifier._workspace_file_exists", return_value=False):
            result = classifier.classify(
                tool_name="file.write",
                arguments={"path": "output.sh", "content": "#!/bin/bash"},
                tool_metadata={"risk_level": 0, "requires_approval": False},
            )
        # .sh extension -> HIGH risk -> requires_approval should be True
        assert result["requires_approval"] is True
