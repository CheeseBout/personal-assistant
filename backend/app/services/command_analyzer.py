"""Command Analyzer — static analysis for shell commands (Phase 7 sandbox).

Classifies a shell command not just by its leading binary name but by analyzing
arguments, paths, redirection and network intent (REQUIREMENTS section 15.4).

The analyzer mirrors the philosophy of risk_classifier.py: deny-first for clearly
destructive shapes, otherwise return a structured risk assessment that the
permission engine turns into an allow/ask/ask_strong decision.
"""

import re
import shlex
from typing import Dict, Any, List

# Risk levels mirror risk_classifier.RiskLevel (0=low,1=medium,2=high,3=critical).

# Read-only binaries — safe to run, low risk.
READONLY_CMDS = {
    "ls", "dir", "cat", "type", "grep", "rg", "pwd", "echo", "head", "tail",
    "wc", "find", "where", "whoami", "date", "env", "printenv", "tree", "stat",
    "file", "sort", "uniq", "diff", "cut", "awk",
}

# Commands that write/modify the filesystem.
WRITE_CMDS = {
    "touch", "mkdir", "mv", "move", "cp", "copy", "ren", "rename", "tee",
    "sed", "tar", "zip", "unzip", "gzip", "gunzip",
}

# Commands that reach the network.
NETWORK_CMDS = {
    "curl", "wget", "nc", "ncat", "ssh", "scp", "sftp", "ftp", "telnet",
    "git", "ping", "nslookup", "dig", "rsync",
}

# Package managers — both write the filesystem AND use the network.
PKG_CMDS = {"pip", "pip3", "npm", "yarn", "pnpm", "apt", "apt-get", "brew", "conda"}

# Indirect execution: impact unknown without reading the target script.
EXEC_CMDS = {"python", "python3", "py", "node", "bash", "sh", "powershell", "pwsh", "cmd", "perl", "ruby"}

# Hard-deny command shapes: irreversible / destructive regardless of mode.
# Matched against the full command text (case-insensitive).
DENY_PATTERNS = [
    (r"\brm\s+(-[a-z]*\s+)*-?[a-z]*r[a-z]*f?[a-z]*\s+/", "rm_recursive_root"),
    (r"\brm\s+-rf\s+/", "rm_rf_root"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork_bomb"),
    (r"\bmkfs\b", "mkfs_format"),
    (r"\bdd\s+if=", "dd_write"),
    (r"\b(format)\s+[a-z]:", "windows_format"),
    (r"\bdel\s+/[a-z]?\s*\\?\*?", "windows_del_root"),
    (r"\brmdir\s+/s", "windows_rmdir_recursive"),
    (r">\s*/dev/sd[a-z]", "overwrite_block_device"),
    (r"\bchmod\s+-r\s+777\s+/", "chmod_root"),
]

# Elevated/privileged — allowed only in Mode E, always strong-confirm.
PRIVILEGED_CMDS = {"sudo", "su", "chmod", "chown", "chgrp", "mount", "umount", "kill", "killall", "taskkill"}


def _split_pipeline(command: str) -> List[str]:
    """Split a command on shell separators (| && || ; ) into sub-commands."""
    parts = re.split(r"\|\||&&|[|;&]", command)
    return [p.strip() for p in parts if p.strip()]


def _first_token(sub: str) -> str:
    """Best-effort extraction of the leading binary name of a sub-command."""
    try:
        tokens = shlex.split(sub, posix=False)
    except ValueError:
        tokens = sub.split()
    if not tokens:
        return ""
    tok = tokens[0].strip('"\'')
    # Strip path prefix (e.g. /usr/bin/curl, .\script) and .exe suffix.
    tok = re.split(r"[\\/]", tok)[-1]
    if tok.lower().endswith(".exe"):
        tok = tok[:-4]
    return tok.lower()


def analyze_command(command: str) -> Dict[str, Any]:
    """Statically analyze a shell command.

    Returns:
    {
        "category": "readonly|write|network|package|exec|privileged|unknown",
        "risk_level": 0-3,
        "network": bool,
        "writes_fs": bool,
        "reads_outside_workspace": bool,
        "denied": bool,
        "matched_rules": [str],
        "explanation": str,
    }
    """
    matched_rules: List[str] = []
    explanation: List[str] = []
    command = (command or "").strip()

    if not command:
        return {
            "category": "unknown", "risk_level": 0, "network": False,
            "writes_fs": False, "reads_outside_workspace": False, "denied": False,
            "matched_rules": [], "explanation": "Empty command",
        }

    # 1) Hard deny patterns over the whole command.
    for pattern, rule in DENY_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            matched_rules.append(f"deny:{rule}")
            return {
                "category": "privileged", "risk_level": 3, "network": False,
                "writes_fs": True, "reads_outside_workspace": False, "denied": True,
                "matched_rules": matched_rules,
                "explanation": f"Destructive command pattern: {rule}",
            }

    # 2) Path analysis: absolute paths or traversal out of workspace.
    reads_outside = False
    if re.search(r"(^|\s)(/|[a-zA-Z]:\\)", command) or "../" in command or "..\\" in command:
        # Allow common harmless absolute-looking flags? Keep conservative: flag it.
        reads_outside = True
        matched_rules.append("path_outside_workspace")
        explanation.append("Command references absolute path or traversal")

    # 3) Redirection writes to filesystem.
    writes_fs = False
    if re.search(r"(?<![0-9])>>?", command):
        writes_fs = True
        matched_rules.append("redirect_write")
        explanation.append("Output redirection writes to a file")

    # 4) Classify each sub-command in the pipeline; take the strongest.
    network = False
    category = "readonly"
    risk = 0

    for sub in _split_pipeline(command):
        binary = _first_token(sub)
        if not binary:
            continue
        if binary in PRIVILEGED_CMDS:
            category, risk = "privileged", max(risk, 2)
            matched_rules.append(f"privileged:{binary}")
            explanation.append(f"Privileged command: {binary}")
        elif binary in PKG_CMDS:
            network, writes_fs = True, True
            category, risk = "package", max(risk, 2)
            matched_rules.append(f"package:{binary}")
            explanation.append(f"Package manager (network + write): {binary}")
        elif binary in NETWORK_CMDS:
            network = True
            category, risk = "network", max(risk, 2)
            matched_rules.append(f"network:{binary}")
            explanation.append(f"Network command: {binary}")
        elif binary in EXEC_CMDS:
            # Indirect execution: impact unknown, treat as elevated risk.
            category, risk = "exec", max(risk, 2)
            matched_rules.append(f"exec:{binary}")
            explanation.append(f"Indirect execution ({binary}); impact depends on script")
        elif binary in WRITE_CMDS:
            writes_fs = True
            if category not in ("network", "package", "privileged", "exec"):
                category = "write"
            risk = max(risk, 1)
            matched_rules.append(f"write:{binary}")
            explanation.append(f"Filesystem write: {binary}")
        elif binary in READONLY_CMDS:
            risk = max(risk, 0)
        else:
            # Unknown binary — be cautious (medium).
            if category == "readonly":
                category = "unknown"
            risk = max(risk, 1)
            matched_rules.append(f"unknown:{binary}")
            explanation.append(f"Unknown command: {binary}")

    if writes_fs and risk < 1:
        risk = 1
    if reads_outside and risk < 2:
        risk = max(risk, 2)

    return {
        "category": category,
        "risk_level": risk,
        "network": network,
        "writes_fs": writes_fs,
        "reads_outside_workspace": reads_outside,
        "denied": False,
        "matched_rules": matched_rules,
        "explanation": "; ".join(explanation) if explanation else "Read-only command",
    }
