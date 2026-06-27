"""Automated tests for Phase 7 sandbox execution.

Covers command analysis, the process-based runner (Python/shell, timeout, secret
exclusion, network block, artifact capture) and the permission integration
(deny-first for destructive shapes, auto-allow for safe Python). Run: pytest -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from app.services.command_analyzer import analyze_command
from app.services.sandbox_runner import SandboxRunner, _minimal_env

SID = "pytest-phase7"

# ---- command analyzer -----------------------------------------------------

def test_readonly_command():
    a = analyze_command("ls -la")
    assert a["category"] == "readonly"
    assert a["risk_level"] == 0
    assert not a["network"] and not a["denied"]

def test_network_command():
    a = analyze_command("curl http://example.com")
    assert a["network"] is True
    assert a["risk_level"] >= 2

def test_write_command():
    a = analyze_command("echo hi > out.txt")
    assert a["writes_fs"] is True
    assert a["risk_level"] >= 1

def test_package_install_is_network_and_write():
    a = analyze_command("pip install requests")
    assert a["network"] and a["writes_fs"]
    assert a["category"] == "package"

def test_dangerous_rm_denied():
    assert analyze_command("rm -rf /")["denied"] is True

def test_fork_bomb_denied():
    assert analyze_command(":(){ :|:& };:")["denied"] is True

def test_indirect_execution_flagged():
    a = analyze_command("python script.py")
    assert a["category"] == "exec"
    assert a["risk_level"] >= 2

def test_traversal_reads_outside():
    a = analyze_command("cat ../../etc/passwd")
    assert a["reads_outside_workspace"] is True

# ---- minimal env ----------------------------------------------------------

def test_minimal_env_excludes_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "topsecret")
    env = _minimal_env(allow_network=False)
    assert not any("KEY" in k.upper() or "SECRET" in k.upper() or "TOKEN" in k.upper()
                   for k in env)

# ---- sandbox runner -------------------------------------------------------

def test_python_stdout():
    r = SandboxRunner.get_instance().run_python("print(2 + 3)", SID)
    assert r["status"] == "success"
    assert r["stdout"].strip() == "5"
    assert r["exit_code"] == 0

def test_python_timeout():
    r = SandboxRunner.get_instance().run_python("while True: pass", SID, timeout=2)
    assert r["status"] == "killed"
    assert r["killed_reason"] == "timeout"

def test_python_network_blocked_mode_a():
    r = SandboxRunner.get_instance().run_python(
        "import socket; socket.socket()", SID, allow_network=False)
    assert "Network access is disabled" in r["stderr"]

def test_python_no_secrets_in_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak-me")
    r = SandboxRunner.get_instance().run_python(
        "import os; print([k for k in os.environ if 'KEY' in k.upper()])", SID)
    assert "OPENAI_API_KEY" not in r["stdout"]

def test_python_artifact_capture():
    # Self-clean so the before/after diff is deterministic across reruns: the
    # artifact diff only flags new or size-changed files.
    import shutil
    from app.services.sandbox_runner import _session_dir
    shutil.rmtree(_session_dir("pytest-artifact"), ignore_errors=True)
    r = SandboxRunner.get_instance().run_python(
        "open('report.txt','w').write('hello')", "pytest-artifact")
    names = [a["name"] for a in r["artifacts"]]
    assert "report.txt" in names

def test_shell_echo():
    r = SandboxRunner.get_instance().run_shell("echo sandbox-ok", SID)
    assert r["status"] == "success"
    assert "sandbox-ok" in r["stdout"]

def test_install_rejects_bad_specs():
    r = SandboxRunner.get_instance().run_install(["--evil-flag", "; rm -rf /"], SID)
    assert r["status"] == "error"

# ---- permission integration ----------------------------------------------

def test_executor_denies_destructive_shell():
    from app.services.tool_executor import ToolExecutor
    from app.services.tool_registry import ToolRegistry
    ToolRegistry.get_instance().initialize()
    r = ToolExecutor().execute("sandbox.shell", {"command": "rm -rf /"}, SID)
    assert r["status"] == "denied"

def test_executor_allows_safe_python():
    from app.services.tool_executor import ToolExecutor
    from app.services.tool_registry import ToolRegistry
    ToolRegistry.get_instance().initialize()
    r = ToolExecutor().execute("sandbox.python", {"code": "print('hi')"}, SID)
    assert r["status"] == "success"
    assert r["result"]["stdout"].strip() == "hi"

def test_executor_gates_network_python():
    from app.services.tool_executor import ToolExecutor
    from app.services.tool_registry import ToolRegistry
    ToolRegistry.get_instance().initialize()
    r = ToolExecutor().execute(
        "sandbox.python", {"code": "print(1)", "allow_network": True}, SID)
    assert r["status"] == "pending_approval"

