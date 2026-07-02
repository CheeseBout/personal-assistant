"""Sheets Tools — sync tool handlers for the sheets.* tool category.

Contract matches gmail_tools/browser_tools:
    def sheets_xxx(arguments: dict, session_id: str) -> dict

Security:
- sheets.read auto-allow; update/append/create require HITL.
- Cell content is untrusted data (fenced by the agent loop).
- "Báo cáo/phân tích" (REQUIREMENTS 26.4) is done by the LLM on top of
  sheets.read output — no separate tool.
"""

from typing import Dict, Any, List

from .google_workspace_common import (
    service_or_none, record_action, execute_with_retry, safe_error, NOT_CONNECTED,
)


def _sheets():
    return service_or_none("sheets", "v4")


def _value_input_option(arguments: Dict[str, Any]) -> str:
    """RAW by default so untrusted cell content is written literally.

    Only interpret input as formulas (USER_ENTERED) when the caller explicitly
    opts in with as_formula=true. This blocks formula/CSV injection where an
    untrusted value like =IMPORTXML(...) would exfiltrate data on write.
    """
    return "USER_ENTERED" if arguments.get("as_formula") else "RAW"


def _coerce_values(values: Any) -> List[List[Any]]:
    """Normalize the 'values' argument into a 2D list."""
    if isinstance(values, list) and values and isinstance(values[0], list):
        return values
    if isinstance(values, list):
        return [values]  # single row
    return [[values]]


def sheets_read(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _sheets()
    if svc is None:
        return NOT_CONNECTED
    sheet_id = arguments.get("spreadsheet_id", "")
    rng = arguments.get("range", "")
    if not sheet_id or not isinstance(sheet_id, str):
        return {"error": "Missing or invalid 'spreadsheet_id'"}
    if not rng or not isinstance(rng, str):
        return {"error": "Missing or invalid 'range' (vd 'Sheet1!A1:D20')"}
    try:
        resp = execute_with_retry(svc.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=rng
        ))
        rows = resp.get("values", [])
        result = {"spreadsheet_id": sheet_id, "range": resp.get("range", rng),
                  "row_count": len(rows), "values": rows}
    except Exception as e:
        result = safe_error("Sheets read lỗi", e)
    record_action(session_id, "sheets", "read", f"{sheet_id}!{rng}", result)
    return result


def sheets_update(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _sheets()
    if svc is None:
        return NOT_CONNECTED
    sheet_id = arguments.get("spreadsheet_id", "")
    rng = arguments.get("range", "")
    values = arguments.get("values")
    if not sheet_id or not rng:
        return {"error": "Missing 'spreadsheet_id' or 'range'"}
    if values is None:
        return {"error": "Missing 'values'"}
    try:
        body = {"values": _coerce_values(values)}
        resp = execute_with_retry(svc.spreadsheets().values().update(
            spreadsheetId=sheet_id, range=rng,
            valueInputOption=_value_input_option(arguments), body=body,
        ))
        result = {"status": "success", "spreadsheet_id": sheet_id,
                  "updated_cells": resp.get("updatedCells", 0),
                  "updated_range": resp.get("updatedRange", rng)}
    except Exception as e:
        result = safe_error("Sheets update lỗi", e)
    record_action(session_id, "sheets", "update", f"{sheet_id}!{rng}", result)
    return result


def sheets_append(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _sheets()
    if svc is None:
        return NOT_CONNECTED
    sheet_id = arguments.get("spreadsheet_id", "")
    rng = arguments.get("range", "")
    values = arguments.get("values")
    if not sheet_id or not rng:
        return {"error": "Missing 'spreadsheet_id' or 'range'"}
    if values is None:
        return {"error": "Missing 'values'"}
    try:
        body = {"values": _coerce_values(values)}
        resp = execute_with_retry(svc.spreadsheets().values().append(
            spreadsheetId=sheet_id, range=rng,
            valueInputOption=_value_input_option(arguments),
            insertDataOption="INSERT_ROWS", body=body,
        ))
        updates = resp.get("updates", {})
        result = {"status": "success", "spreadsheet_id": sheet_id,
                  "updated_cells": updates.get("updatedCells", 0),
                  "updated_range": updates.get("updatedRange", rng)}
    except Exception as e:
        result = safe_error("Sheets append lỗi", e)
    record_action(session_id, "sheets", "append", f"{sheet_id}!{rng}", result)
    return result


def sheets_create(arguments: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    svc = _sheets()
    if svc is None:
        return NOT_CONNECTED
    title = arguments.get("title", "")
    if not title or not isinstance(title, str):
        return {"error": "Missing or invalid 'title'"}
    try:
        created = execute_with_retry(svc.spreadsheets().create(
            body={"properties": {"title": title}},
            fields="spreadsheetId,properties.title",
        ))
        result = {"status": "success",
                  "spreadsheet_id": created.get("spreadsheetId"),
                  "title": created.get("properties", {}).get("title", title)}
    except Exception as e:
        result = safe_error("Sheets create lỗi", e)
    record_action(session_id, "sheets", "create", title, result)
    return result
