"""Unit tests for the browser URL guard (domain allow/blocklist + SSRF)."""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.browser_manager import BrowserManager


@pytest.fixture
def mgr():
    # Construct without launching the browser — _check_url is pure logic + DNS.
    return BrowserManager()


def _settings(allow="", block="", block_private=True):
    return {
        "domain_allowlist": allow,
        "domain_blocklist": block,
        "block_private_ips": block_private,
    }


def test_non_http_scheme_rejected(mgr):
    assert mgr._check_url("file:///etc/passwd") is not None
    assert mgr._check_url("ftp://example.com") is not None


def test_empty_url_rejected(mgr):
    assert mgr._check_url("") is not None
    assert mgr._check_url(None) is not None


def test_public_domain_allowed_when_no_allowlist(mgr):
    with patch.object(mgr, "_browser_settings", return_value=_settings()):
        with patch.object(BrowserManager, "_is_blocked_ip", staticmethod(lambda h: False)):
            assert mgr._check_url("https://example.com") is None


def test_blocklist_domain_rejected(mgr):
    with patch.object(mgr, "_browser_settings", return_value=_settings(block="evil.com")):
        with patch.object(BrowserManager, "_is_blocked_ip", staticmethod(lambda h: False)):
            assert mgr._check_url("https://evil.com") is not None
            # subdomain also blocked
            assert mgr._check_url("https://sub.evil.com") is not None


def test_non_allowlist_domain_rejected_when_allowlist_set(mgr):
    with patch.object(mgr, "_browser_settings", return_value=_settings(allow="example.com")):
        with patch.object(BrowserManager, "_is_blocked_ip", staticmethod(lambda h: False)):
            assert mgr._check_url("https://other.com") is not None
            assert mgr._check_url("https://example.com") is None
            assert mgr._check_url("https://sub.example.com") is None


def test_loopback_rejected(mgr):
    with patch.object(mgr, "_browser_settings", return_value=_settings()):
        assert mgr._check_url("http://127.0.0.1:8000") is not None


def test_link_local_metadata_rejected(mgr):
    with patch.object(mgr, "_browser_settings", return_value=_settings()):
        assert mgr._check_url("http://169.254.169.254/latest/meta-data/") is not None


def test_private_range_rejected(mgr):
    with patch.object(mgr, "_browser_settings", return_value=_settings()):
        assert mgr._check_url("http://10.0.0.5") is not None
        assert mgr._check_url("http://192.168.1.1") is not None


def test_private_ip_allowed_when_toggle_off(mgr):
    with patch.object(mgr, "_browser_settings", return_value=_settings(block_private=False)):
        # SSRF guard disabled → loopback passes scheme/domain checks
        assert mgr._check_url("http://127.0.0.1:8000") is None


def test_is_blocked_ip_literals():
    assert BrowserManager._is_blocked_ip("127.0.0.1") is True
    assert BrowserManager._is_blocked_ip("169.254.169.254") is True
    assert BrowserManager._is_blocked_ip("10.1.2.3") is True
    assert BrowserManager._is_blocked_ip("192.168.0.1") is True
    assert BrowserManager._is_blocked_ip("0.0.0.0") is True


def test_is_blocked_ip_public():
    # 8.8.8.8 is a public IP literal — not blocked
    assert BrowserManager._is_blocked_ip("8.8.8.8") is False


def test_is_blocked_ip_resolves_to_private():
    def fake_getaddrinfo(host, *a, **k):
        return [(None, None, None, None, ("127.0.0.1", 0))]
    with patch("app.services.browser_manager.socket.getaddrinfo", fake_getaddrinfo):
        assert BrowserManager._is_blocked_ip("rebind.evil.com") is True


def test_is_blocked_ip_unresolvable_fails_closed():
    import socket as _socket
    def boom(host, *a, **k):
        raise _socket.gaierror("no such host")
    with patch("app.services.browser_manager.socket.getaddrinfo", boom):
        assert BrowserManager._is_blocked_ip("nonexistent.invalid") is True
