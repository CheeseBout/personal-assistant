"""Sandbox Runner — process-based execution engine for Phase 7.

Runs Python and shell commands in an isolated per-session working directory with
resource limits (timeout + best-effort RAM cap), a minimal environment that never
exposes secrets, stdout/stderr capture, and artifact (file diff) capture.

Isolation is process-based (subprocess), not container-based — see the plan's
"Ngoài phạm vi" note. On Windows the Unix `resource` module is unavailable, so RAM
limits are enforced best-effort by polling RSS via psutil and killing the process
tree on breach. Network for Python is blocked by injecting a sitecustomize that
monkeypatches socket; shell network is governed by command analysis + HITL upstream.
"""

import os
import sys
import re
import time
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional

from ..core.config import settings
from ..core.logging_config import logger

try:
    import psutil
except ImportError:  # best-effort: RAM monitoring disabled without psutil
    psutil = None

SANDBOX_ROOT = (Path(settings.UPLOAD_DIR).parent / "sandbox").resolve()
SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)

# Env var name fragments that must never be passed into the sandbox.
_SECRET_FRAGMENTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH", "API")

# Valid pip package spec: name with optional extras/version pin. No flags/shell-meta.
_PKG_SPEC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?([=<>!~]=?[A-Za-z0-9._*+!-]+)?$")

# sitecustomize injected to block network when allow_network is False.
_NO_NETWORK_SITECUSTOMIZE = (
    "import socket as _s\n"
    "def _blocked(*a, **k):\n"
    "    raise OSError('Network access is disabled in this sandbox mode')\n"
    "_s.socket = _blocked\n"
    "_s.create_connection = _blocked\n"
    "try:\n"
    "    _s.create_server = _blocked\n"
    "except Exception:\n"
    "    pass\n"
)


def _session_dir(session_id: str) -> Path:
    """Per-session sandbox working directory, created on demand."""
    safe = "".join(c for c in (session_id or "default") if c.isalnum() or c in "-_")
    d = (SANDBOX_ROOT / (safe or "default")).resolve()
    if not str(d).startswith(str(SANDBOX_ROOT)):
        raise ValueError("Invalid session sandbox path")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _minimal_env(allow_network: bool, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Build a minimal environment that excludes secrets (REQUIREMENTS 15.3)."""
    env: Dict[str, str] = {}
    # Keep only the bare essentials needed for interpreters to start.
    for key in ("PATH", "SystemRoot", "SYSTEMROOT", "TEMP", "TMP", "WINDIR",
                "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS", "LANG", "LC_ALL"):
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    # Defensive: drop anything that looks secret (none should be present anyway).
    env = {k: v for k, v in env.items()
           if not any(frag in k.upper() for frag in _SECRET_FRAGMENTS)}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    if not allow_network:
        env["no_proxy"] = "*"
    if extra:
        env.update(extra)
    return env


def _snapshot_files(directory: Path) -> Dict[str, int]:
    """Map of relative file path -> size for artifact diffing.

    Internal scaffolding (network-block site dir, pip target, caches, the script
    file itself) is excluded so only user-produced files show up as artifacts.
    """
    internal = {".pip-cache", "__sandbox_site", "__sandbox_pkgs", "__pycache__"}
    out: Dict[str, int] = {}
    for p in directory.rglob("*"):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(directory).parts
        if any(part in internal for part in rel_parts):
            continue
        if p.name.startswith("__sandbox_"):
            continue
        try:
            out[str(p.relative_to(directory)).replace("\\", "/")] = p.stat().st_size
        except OSError:
            pass
    return out


def _kill_tree(pid: int) -> None:
    """Terminate a process and all its children (cross-platform via psutil)."""
    if psutil is None:
        return
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = parent.children(recursive=True) + [parent]
    for p in procs:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=3)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass


class SandboxRunner:
    """Executes Python/shell in a per-session isolated directory with limits."""

    _instance: Optional["SandboxRunner"] = None

    @classmethod
    def get_instance(cls) -> "SandboxRunner":
        if cls._instance is None:
            cls._instance = SandboxRunner()
        return cls._instance

    def _supervise(self, proc: subprocess.Popen, timeout: int,
                   max_memory_mb: int) -> Dict[str, Any]:
        """Run a memory watchdog while waiting for proc to finish.

        Returns {"killed_reason": "timeout"|"memory"|None}. The watchdog polls
        RSS via psutil; the timeout is enforced by communicate() in the caller.
        """
        state = {"killed_reason": None, "stop": False}

        def _watch():
            if psutil is None:
                return
            limit_bytes = max_memory_mb * 1024 * 1024
            try:
                ps = psutil.Process(proc.pid)
            except psutil.NoSuchProcess:
                return
            while not state["stop"] and proc.poll() is None:
                try:
                    rss = ps.memory_info().rss
                    for child in ps.children(recursive=True):
                        try:
                            rss += child.memory_info().rss
                        except psutil.Error:
                            pass
                    if rss > limit_bytes:
                        state["killed_reason"] = "memory"
                        _kill_tree(proc.pid)
                        return
                except psutil.Error:
                    return
                time.sleep(0.1)

        watcher = threading.Thread(target=_watch, daemon=True)
        watcher.start()
        return state, watcher

    def _execute(self, args, shell: bool, cwd: Path, allow_network: bool,
                 timeout: int, max_memory_mb: int,
                 extra_env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Core subprocess execution shared by python/shell runners."""
        max_out = settings.SANDBOX_MAX_OUTPUT_KB * 1024
        env = _minimal_env(allow_network, extra_env)
        before = _snapshot_files(cwd)
        started = time.time()

        try:
            proc = subprocess.Popen(
                args,
                shell=shell,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as e:
            return {"status": "error", "error": f"Failed to start process: {e}"}

        state, watcher = self._supervise(proc, timeout, max_memory_mb)
        killed_reason = None
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            killed_reason = "timeout"
            _kill_tree(proc.pid)
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
        finally:
            state["stop"] = True
            watcher.join(timeout=1)

        if state["killed_reason"]:
            killed_reason = state["killed_reason"]

        duration_ms = int((time.time() - started) * 1000)
        exit_code = proc.returncode

        after = _snapshot_files(cwd)
        artifacts = [
            {"name": name, "size": size}
            for name, size in sorted(after.items())
            if name not in before or before[name] != size
        ]

        status = "success"
        if killed_reason:
            status = "killed"
        elif exit_code != 0:
            status = "error"

        return {
            "status": status,
            "exit_code": exit_code,
            "killed_reason": killed_reason,
            "stdout": (stdout or "")[:max_out],
            "stderr": (stderr or "")[:max_out],
            "stdout_truncated": len(stdout or "") > max_out,
            "stderr_truncated": len(stderr or "") > max_out,
            "artifacts": artifacts,
            "duration_ms": duration_ms,
        }

    def run_python(self, code: str, session_id: str, allow_network: bool = False,
                   timeout: Optional[int] = None,
                   max_memory_mb: Optional[int] = None) -> Dict[str, Any]:
        """Write code to a script file and run it in the session sandbox."""
        timeout = min(timeout or settings.SANDBOX_DEFAULT_TIMEOUT_S, settings.SANDBOX_MAX_TIMEOUT_S)
        max_memory_mb = max_memory_mb or settings.SANDBOX_MAX_MEMORY_MB
        cwd = _session_dir(session_id)

        script = cwd / "__sandbox_script.py"
        script.write_text(code, encoding="utf-8")

        extra_env = None
        if not allow_network:
            # Inject a sitecustomize that blocks sockets, via an isolated dir on
            # PYTHONPATH so it loads before user code.
            site_dir = cwd / "__sandbox_site"
            site_dir.mkdir(exist_ok=True)
            (site_dir / "sitecustomize.py").write_text(_NO_NETWORK_SITECUSTOMIZE, encoding="utf-8")
            extra_env = {"PYTHONPATH": str(site_dir)}

        result = self._execute(
            [sys.executable, "-I", str(script)] if allow_network else [sys.executable, str(script)],
            shell=False, cwd=cwd, allow_network=allow_network,
            timeout=timeout, max_memory_mb=max_memory_mb, extra_env=extra_env,
        )
        result["code"] = code
        return result

    def run_shell(self, command: str, session_id: str, allow_network: bool = True,
                  timeout: Optional[int] = None,
                  max_memory_mb: Optional[int] = None) -> Dict[str, Any]:
        """Run a shell command in the session sandbox."""
        timeout = min(timeout or settings.SANDBOX_DEFAULT_TIMEOUT_S, settings.SANDBOX_MAX_TIMEOUT_S)
        max_memory_mb = max_memory_mb or settings.SANDBOX_MAX_MEMORY_MB
        cwd = _session_dir(session_id)
        result = self._execute(
            command, shell=True, cwd=cwd, allow_network=allow_network,
            timeout=timeout, max_memory_mb=max_memory_mb,
        )
        result["command"] = command
        return result

    def run_install(self, packages: List[str], session_id: str,
                    timeout: Optional[int] = None) -> Dict[str, Any]:
        """pip install packages into the session sandbox (network + cache)."""
        timeout = min(timeout or settings.SANDBOX_MAX_TIMEOUT_S, settings.SANDBOX_MAX_TIMEOUT_S)
        cwd = _session_dir(session_id)
        cache_dir = Path(settings.SANDBOX_PIP_CACHE_DIR)
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cwd / "__sandbox_pkgs"
        target.mkdir(exist_ok=True)
        # Validate package specs to avoid passing flags or shell-meta as a "package".
        clean = [p for p in packages if _PKG_SPEC_RE.match(p.strip())]
        rejected = [p for p in packages if p not in clean]
        if not clean:
            return {"status": "error", "error": "No valid package names",
                    "rejected": rejected, "command": "pip install"}
        args = [sys.executable, "-m", "pip", "install", "--cache-dir", str(cache_dir),
                "--target", str(target), *clean]
        result = self._execute(
            args, shell=False, cwd=cwd, allow_network=True,
            timeout=timeout, max_memory_mb=settings.SANDBOX_MAX_MEMORY_MB,
        )
        result["command"] = "pip install " + " ".join(packages)
        return result

    def list_artifacts(self, session_id: str) -> Dict[str, Any]:
        """List files currently in the session sandbox."""
        cwd = _session_dir(session_id)
        snap = _snapshot_files(cwd)
        return {"artifacts": [{"name": n, "size": s} for n, s in sorted(snap.items())],
                "count": len(snap)}

    def read_artifact(self, session_id: str, name: str, max_bytes: int = 200_000) -> Dict[str, Any]:
        """Read a single artifact file from the session sandbox (text)."""
        cwd = _session_dir(session_id)
        target = (cwd / name).resolve()
        if not str(target).startswith(str(cwd)):
            return {"error": "Path outside sandbox not allowed"}
        if not target.exists() or not target.is_file():
            return {"error": "Artifact not found", "name": name}
        try:
            data = target.read_text(encoding="utf-8", errors="replace")[:max_bytes]
            return {"name": name, "content": data, "size": target.stat().st_size}
        except Exception as e:
            return {"error": f"Failed to read artifact: {e}"}
