"""Unit tests for Google Workspace tools (safety-critical paths).

These test the pure/guardable logic without a live Google connection:
- Sheets formula-injection guard (RAW vs USER_ENTERED)
- Shared retry + redacted-error helpers
- Download size cap helper
- not-connected handling
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services import sheets_tools
from app.services import google_workspace_common as gwc


# --- Sheets formula-injection guard (#2) ---------------------------------------

def test_value_input_option_defaults_to_raw():
    assert sheets_tools._value_input_option({}) == "RAW"
    assert sheets_tools._value_input_option({"values": [["=SUM(A1)"]]}) == "RAW"


def test_value_input_option_user_entered_only_when_opted_in():
    assert sheets_tools._value_input_option({"as_formula": True}) == "USER_ENTERED"
    assert sheets_tools._value_input_option({"as_formula": False}) == "RAW"


def test_sheets_update_uses_raw_by_default():
    fake_svc = MagicMock()
    exec_mock = fake_svc.spreadsheets.return_value.values.return_value.update.return_value
    exec_mock.execute.return_value = {"updatedCells": 1, "updatedRange": "A1"}
    with patch.object(sheets_tools, "_sheets", return_value=fake_svc), \
         patch.object(sheets_tools, "record_action"):
        sheets_tools.sheets_update(
            {"spreadsheet_id": "sid", "range": "A1", "values": [["=IMPORTXML('http://evil')"]]},
            "sess",
        )
    _, kwargs = fake_svc.spreadsheets.return_value.values.return_value.update.call_args
    assert kwargs["valueInputOption"] == "RAW"


def test_sheets_update_user_entered_when_as_formula():
    fake_svc = MagicMock()
    exec_mock = fake_svc.spreadsheets.return_value.values.return_value.update.return_value
    exec_mock.execute.return_value = {"updatedCells": 1, "updatedRange": "A1"}
    with patch.object(sheets_tools, "_sheets", return_value=fake_svc), \
         patch.object(sheets_tools, "record_action"):
        sheets_tools.sheets_update(
            {"spreadsheet_id": "sid", "range": "A1", "values": [["=SUM(A1:A2)"]], "as_formula": True},
            "sess",
        )
    _, kwargs = fake_svc.spreadsheets.return_value.values.return_value.update.call_args
    assert kwargs["valueInputOption"] == "USER_ENTERED"


def test_sheets_not_connected():
    with patch.object(sheets_tools, "_sheets", return_value=None):
        out = sheets_tools.sheets_read({"spreadsheet_id": "x", "range": "A1"}, "sess")
    assert "error" in out


# --- safe_error redaction (#7) -------------------------------------------------

def test_safe_error_redacts_secret_shapes():
    exc = Exception("failed with token sk-abcdef1234567890 in request")
    out = gwc.safe_error("Gmail send lỗi", exc)
    assert "error" in out
    assert "sk-abcdef1234567890" not in out["error"]
    assert out["error"].startswith("Gmail send lỗi:")


def test_safe_error_keeps_benign_text():
    out = gwc.safe_error("Drive read lỗi", Exception("file not found"))
    assert out["error"] == "Drive read lỗi: file not found"


# --- execute_with_retry (#6) ---------------------------------------------------

def _http_error(status):
    from googleapiclient.errors import HttpError
    resp = MagicMock()
    resp.status = status
    return HttpError(resp, b"error")


def test_retry_succeeds_first_try():
    req = MagicMock()
    req.execute.return_value = {"ok": True}
    assert gwc.execute_with_retry(req) == {"ok": True}
    assert req.execute.call_count == 1


def test_retry_on_transient_then_success():
    req = MagicMock()
    req.execute.side_effect = [_http_error(503), {"ok": True}]
    with patch.object(gwc.time, "sleep"):
        assert gwc.execute_with_retry(req) == {"ok": True}
    assert req.execute.call_count == 2


def test_no_retry_on_non_transient():
    req = MagicMock()
    req.execute.side_effect = _http_error(404)
    from googleapiclient.errors import HttpError
    with pytest.raises(HttpError):
        gwc.execute_with_retry(req)
    assert req.execute.call_count == 1


def test_retry_exhausts_then_raises():
    req = MagicMock()
    req.execute.side_effect = _http_error(429)
    from googleapiclient.errors import HttpError
    with patch.object(gwc.time, "sleep"):
        with pytest.raises(HttpError):
            gwc.execute_with_retry(req)
    assert req.execute.call_count == gwc._MAX_RETRIES


# --- download size cap helper (#4) ---------------------------------------------

def test_max_download_bytes_reads_config():
    with patch.object(gwc.settings, "GOOGLE_MAX_DOWNLOAD_MB", 10):
        assert gwc.max_download_bytes() == 10 * 1024 * 1024
