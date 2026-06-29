# -*- coding: utf-8 -*-
"""Automated tests for Phase 10 desktop control.

Covers the migration seed (risk flags), risk-classifier escalation, the
permission pipeline routing control actions to ask_strong + ApprovalRequest,
the opt-in gate (DESKTOP_ENABLE_CONTROL off => refuse without touching any
control library), executor registration, and argument validation.

These run headless: the engine's gate short-circuits before pyautogui/pywinauto
are imported, so no real desktop interaction occurs. Run: pytest -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from app.models.migration_desktop_control import run_migration as seed_control_tools
from app.services.risk_classifier import RiskClassifier, RiskLevel
from app.services.tool_registry import ToolRegistry
from app.services.tool_executor import ToolExecutor, TOOL_EXECUTORS
from app.services.desktop_control import DesktopControl
from app.core.config import settings

SID = "pytest-phase10"

CONTROL_TOOLS = ["desktop.click", "desktop.type", "desktop.key", "desktop.drag",
                 "desktop.mouse_move", "desktop.scroll", "desktop.wait"]
STATE_CHANGING = ["desktop.click", "desktop.type", "desktop.key", "desktop.drag"]
LOW_RISK = ["desktop.mouse_move", "desktop.scroll", "desktop.wait"]


@pytest.fixture(scope="module", autouse=True)
def _seed():
    seed_control_tools()
    ToolRegistry.get_instance()._load_tools()  # force reload after seeding
    ToolRegistry.get_instance()._initialized = True


@pytest.fixture(autouse=True)
def _control_off(monkeypatch):
    # Default every test to control DISABLED unless it opts in.
    monkeypatch.setattr(settings, "DESKTOP_ENABLE_CONTROL", False)


# ---- migration seed -------------------------------------------------------

def test_all_control_tools_registered():
    reg = ToolRegistry.get_instance()
    for name in CONTROL_TOOLS:
        assert reg.get_tool(name) is not None, f"{name} not seeded"


def test_state_changing_tools_require_approval():
    reg = ToolRegistry.get_instance()
    for name in STATE_CHANGING:
        t = reg.get_tool(name)
        assert t["risk_level"] == 2, f"{name} should be HIGH"
        assert t["requires_approval"] == 1, f"{name} must require approval"


def test_low_risk_tools_run_directly():
    reg = ToolRegistry.get_instance()
    for name in LOW_RISK:
        t = reg.get_tool(name)
        assert t["risk_level"] == 0
        assert t["requires_approval"] == 0


def test_type_tool_logs_sensitive_args():
    assert ToolRegistry.get_instance().get_tool("desktop.type")["logs_sensitive_args"] == 1


def test_executors_mapped():
    for name in CONTROL_TOOLS:
        assert name in TOOL_EXECUTORS


# ---- risk classifier escalation ------------------------------------------

def _classify(tool, args):
    reg = ToolRegistry.get_instance()
    return RiskClassifier().classify(tool, args, reg.get_tool(tool))


def test_type_into_password_field_is_critical():
    c = _classify("desktop.type", {"text": "x", "name": "Password"})
    assert c["risk_level"] == RiskLevel.CRITICAL.value


def test_type_into_otp_field_is_critical():
    c = _classify("desktop.type", {"text": "123", "name": "Mã OTP", "auto_id": "otp_input"})
    assert c["risk_level"] == RiskLevel.CRITICAL.value


def test_type_normal_field_stays_high():
    c = _classify("desktop.type", {"text": "hello", "name": "Document body"})
    assert c["risk_level"] == RiskLevel.HIGH.value


def test_dangerous_key_combo_is_critical():
    for combo in ("win+r", "ctrl+alt+del", "alt+f4"):
        c = _classify("desktop.key", {"keys": combo})
        assert c["risk_level"] == RiskLevel.CRITICAL.value, combo


def test_safe_key_stays_high():
    c = _classify("desktop.key", {"keys": "ctrl+c"})
    assert c["risk_level"] == RiskLevel.HIGH.value


def test_click_sensitive_target_escalates():
    c = _classify("desktop.click", {"name": "Delete account"})
    assert c["risk_level"] >= RiskLevel.HIGH.value


def test_click_plain_target_stays_high():
    c = _classify("desktop.click", {"name": "Notepad text area"})
    assert c["risk_level"] == RiskLevel.HIGH.value


# ---- permission pipeline --------------------------------------------------

def test_click_routes_to_ask_strong():
    from app.services.permission_engine import PermissionEngine
    r = PermissionEngine().check_and_log(SID, "desktop.click", {"name": "OK button"})
    assert r["decision"] == "ask_strong"
    assert r.get("approval_id")


def test_executor_click_returns_pending_approval():
    r = ToolExecutor().execute("desktop.click", {"name": "OK"}, SID)
    assert r["status"] == "pending_approval"
    assert r.get("approval_id")


# ---- opt-in gate (control OFF) -------------------------------------------

def test_engine_refuses_when_disabled():
    # Low-risk tool auto-allows at permission layer, so the executor runs and
    # must hit the disabled gate WITHOUT importing any control library.
    r = ToolExecutor().execute("desktop.mouse_move", {"x": 10, "y": 10}, SID)
    assert r["status"] == "success"
    assert r["result"]["status"] == "disabled"


def test_dispatch_after_approval_blocked_when_disabled():
    # Even after a (hypothetical) approval, the engine refuses while control off.
    r = ToolExecutor().dispatch_after_approval("desktop.click", {"name": "OK"}, SID)
    assert r["status"] == "success"
    assert r["result"]["status"] == "disabled"


def test_engine_methods_disabled_directly():
    ctrl = DesktopControl.get_instance()
    assert ctrl.click(SID, name="X")["status"] == "disabled"
    assert ctrl.type_text(SID, text="hi", name="Y")["status"] == "disabled"
    assert ctrl.press_key(SID, keys="enter")["status"] == "disabled"


def test_wait_is_not_gated_by_control_flag():
    # wait is read-only; it should run even with control disabled.
    r = DesktopControl.get_instance().wait(SID, seconds=0)
    assert r["status"] == "success"
    assert r["action"] == "wait"


# ---- argument validation --------------------------------------------------

def test_type_requires_text():
    errs = ToolRegistry.get_instance().validate_arguments("desktop.type", {})
    assert errs  # missing required "text"


def test_key_requires_keys():
    errs = ToolRegistry.get_instance().validate_arguments("desktop.key", {})
    assert errs  # missing required "keys"


def test_invalid_key_rejected_by_engine(monkeypatch):
    monkeypatch.setattr(settings, "DESKTOP_ENABLE_CONTROL", True)
    r = DesktopControl.get_instance().press_key(SID, keys="frobnicate")
    assert r["status"] == "error"
