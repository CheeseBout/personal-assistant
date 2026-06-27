"""Secret redaction — strip sensitive values before they reach logs.

Per the safety model, secrets/API keys/private keys must never be written to
episodic memory, audit logs, or the app log file. This module provides a single
redaction pass applied at every logging sink.

Two layers:
1. Pattern-based: regexes catch common secret shapes (sk- keys, bearer tokens,
   private key blocks) inside any string.
2. Key-name based: dict values whose key looks like a credential are masked
   wholesale, regardless of content.
"""

import copy
import re
from typing import Any

REDACTED = "[REDACTED]"

# Dict keys whose values are credentials and should be masked entirely.
SENSITIVE_KEY_NAMES = {
    "password", "passwd", "pwd", "secret", "client_secret",
    "token", "access_token", "refresh_token", "api_key", "apikey",
    "authorization", "auth", "private_key", "privatekey",
    "credentials", "credential", "session_token",
    # Browser (Phase 4): cookies / CSRF tokens must never reach logs.
    "cookie", "cookies", "set-cookie", "csrf", "xsrf", "csrf_token", "xsrf_token",
}

# Pattern-based redaction for secret-shaped substrings inside free text.
_PATTERNS = [
    # OpenAI / common provider keys: sk-... , sk-proj-...
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b"), "sk-[REDACTED]"),
    # Bearer tokens
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer [REDACTED]"),
    # PEM private key blocks
    (re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ), "[REDACTED_PRIVATE_KEY]"),
    # key=value / key: value where key signals a secret
    (re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd|client[_-]?secret)\b"
        r"\s*[:=]\s*[\"']?[A-Za-z0-9._\-/+]{6,}[\"']?"
    ), r"\1=[REDACTED]"),
]


def redact_text(value: str) -> str:
    """Apply all secret patterns to a string."""
    if not isinstance(value, str) or not value:
        return value
    out = value
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def redact_value(obj: Any) -> Any:
    """Recursively redact a JSON-like structure (dict/list/str).

    Returns a redacted deep copy; the input is never mutated.
    """
    if isinstance(obj, dict):
        result = {}
        for key, val in obj.items():
            if isinstance(key, str) and key.lower() in SENSITIVE_KEY_NAMES:
                result[key] = REDACTED
            else:
                result[key] = redact_value(val)
        return result
    if isinstance(obj, list):
        return [redact_value(item) for item in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def redact_arguments(arguments: Any, sensitive: bool = False) -> Any:
    """Redact tool arguments before logging.

    When the tool is flagged ``logs_sensitive_args``, every string argument is
    masked wholesale (the tool itself declared its inputs are sensitive).
    Otherwise only secret-shaped substrings and credential-named keys are masked.
    """
    if sensitive and isinstance(arguments, dict):
        return {k: (REDACTED if isinstance(v, str) else redact_value(v))
                for k, v in arguments.items()}
    return redact_value(arguments)
