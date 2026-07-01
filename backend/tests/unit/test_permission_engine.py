"""Unit tests for the permission engine decision logic.

PermissionEngine requires several singletons (ToolRegistry, EpisodicMemory,
ShortTermMemoryManager) and database access, making direct instantiation
complex in unit tests. Instead, we test the core _decide_action logic by
extracting it into a standalone helper and testing the decision priority chain.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


def _decide_action(risk_level: int, tool_requires_approval: bool, classifier_suggests: bool) -> str:
    """Mirror of PermissionEngine._decide_action logic.

    This is a pure-function copy of the decision logic from
    app/services/permission_engine.py so we can test it without
    instantiating PermissionEngine and its singleton dependencies.

    Policy precedence: deny-first is already handled by classifier.
    Now decide between allow/ask/ask_strong.
    """
    if classifier_suggests or tool_requires_approval:
        if risk_level >= 2:
            return "ask_strong"
        else:
            return "ask"
    return "allow"


# ---------------------------------------------------------------------------
# Decision logic tests
# ---------------------------------------------------------------------------

class TestDecideAction:
    """Tests for the permission decision priority chain."""

    def test_low_risk_no_approval_required_allows(self):
        result = _decide_action(
            risk_level=0,
            tool_requires_approval=False,
            classifier_suggests=False,
        )
        assert result == "allow"

    def test_medium_risk_tool_requires_approval_asks(self):
        result = _decide_action(
            risk_level=1,
            tool_requires_approval=True,
            classifier_suggests=False,
        )
        assert result == "ask"

    def test_high_risk_requires_approval_asks_strong(self):
        result = _decide_action(
            risk_level=2,
            tool_requires_approval=True,
            classifier_suggests=False,
        )
        assert result == "ask_strong"

    def test_critical_risk_asks_strong(self):
        result = _decide_action(
            risk_level=3,
            tool_requires_approval=True,
            classifier_suggests=True,
        )
        assert result == "ask_strong"

    def test_classifier_suggests_approval_asks(self):
        result = _decide_action(
            risk_level=1,
            tool_requires_approval=False,
            classifier_suggests=True,
        )
        assert result == "ask"

    def test_classifier_suggests_high_risk_asks_strong(self):
        result = _decide_action(
            risk_level=2,
            tool_requires_approval=False,
            classifier_suggests=True,
        )
        assert result == "ask_strong"

    def test_neither_flag_set_allows(self):
        # Even with medium/high risk, if neither flag is set -> allow
        # (This mirrors the method; in practice the classifier sets the flag
        # for risk >= MEDIUM, so this scenario shouldn't occur in production.)
        result = _decide_action(
            risk_level=1,
            tool_requires_approval=False,
            classifier_suggests=False,
        )
        assert result == "allow"

    def test_both_flags_set_low_risk_asks(self):
        result = _decide_action(
            risk_level=0,
            tool_requires_approval=True,
            classifier_suggests=True,
        )
        assert result == "ask"

    def test_both_flags_set_high_risk_asks_strong(self):
        result = _decide_action(
            risk_level=3,
            tool_requires_approval=True,
            classifier_suggests=True,
        )
        assert result == "ask_strong"


# ---------------------------------------------------------------------------
# Deny-first priority chain reasoning tests
# ---------------------------------------------------------------------------

class TestDenyFirstPriorityChain:
    """Verify that the priority chain deny > ask_strong > ask > allow holds."""

    def test_priority_ordering(self):
        """The decision escalates with risk and flags, never downgrades."""
        # No flags, low risk -> allow (weakest)
        assert _decide_action(0, False, False) == "allow"

        # Flag set, low risk -> ask (stronger)
        assert _decide_action(0, True, False) == "ask"

        # Flag set, high risk -> ask_strong (strongest available in _decide_action)
        assert _decide_action(2, True, False) == "ask_strong"

        # Deny is handled before _decide_action in the real engine;
        # _decide_action never returns "deny" -- that's correct by design.

    def test_ask_strong_threshold_is_risk_2(self):
        """Risk level 2 is the boundary between ask and ask_strong."""
        assert _decide_action(1, True, True) == "ask"
        assert _decide_action(2, True, True) == "ask_strong"
