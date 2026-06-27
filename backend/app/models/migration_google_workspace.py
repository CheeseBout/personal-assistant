# -*- coding: utf-8 -*-
"""Database migration for Phase 5 (đợt 2) — Google Workspace (Drive/Docs/Sheets).

Creates the google_workspace_actions table (raw sqlite3) and seeds the drive.*/
docs.*/sheets.* tools into the registry. Tool rows are upserted by name, mirroring
migration_google.run_migration().

Risk policy (REQUIREMENTS 26.4): read auto-allow; local-write (download/export)
risk 1 no approval; any write back to the Google account (upload/move/rename/
trash/create/edit/update/append) requires HITL. No permanent delete — trash only.
"""

import uuid
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "db" / "agent.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _ro():
    return {"risk_level": 0, "requires_approval": 0,
            "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0}


def _local_write():
    # writes a file into the local workspace only
    return {"risk_level": 1, "requires_approval": 0,
            "rollback_type": "reversible", "rollback_supported": 0, "logs_sensitive_args": 0}


def _create():
    return {"risk_level": 1, "requires_approval": 1,
            "rollback_type": "reversible", "rollback_supported": 0, "logs_sensitive_args": 0}


def _mutate():
    # writes back to the user's Google account
    return {"risk_level": 2, "requires_approval": 1,
            "rollback_type": "reversible", "rollback_supported": 0, "logs_sensitive_args": 0}


DEFAULT_TOOLS = [
    # --- Drive ---
    {
        "name": "drive.search",
        "description": "Tìm file trên Drive theo truy vấn Drive (vd \"name contains 'report'\"); trả id, name, mimeType, modifiedTime",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Truy vấn kiểu Drive API (q). Bỏ trống = file gần đây"},
                "max_results": {"type": "integer", "description": "Số kết quả tối đa (1-100)"},
            },
            "required": [],
        },
        **_ro(),
    },
    {
        "name": "drive.read",
        "description": "Đọc nội dung text của một file Drive (export Google Docs/Sheets sang text). Dùng cho cả tóm tắt",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "ID file"},
                "max_chars": {"type": "integer", "description": "Giới hạn ký tự"},
            },
            "required": ["file_id"],
        },
        **_ro(),
    },
    {
        "name": "drive.download",
        "description": "Tải một file Drive về workspace (đường dẫn tương đối trong workspace)",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "ID file"},
                "dest_path": {"type": "string", "description": "Đường dẫn đích trong workspace"},
            },
            "required": ["file_id", "dest_path"],
        },
        **_local_write(),
    },
    {
        "name": "drive.upload",
        "description": "Tải một file từ workspace lên Drive (chỉ file trong workspace). Cần phê duyệt",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Đường dẫn file trong workspace"},
                "name": {"type": "string", "description": "Tên file trên Drive (mặc định = tên gốc)"},
                "folder_id": {"type": "string", "description": "ID thư mục đích (tuỳ chọn)"},
            },
            "required": ["path"],
        },
        **_mutate(),
    },
    {
        "name": "drive.move",
        "description": "Chuyển file sang thư mục khác. Cần phê duyệt",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "ID file"},
                "folder_id": {"type": "string", "description": "ID thư mục đích"},
            },
            "required": ["file_id", "folder_id"],
        },
        **_mutate(),
    },
    {
        "name": "drive.rename",
        "description": "Đổi tên file Drive. Cần phê duyệt",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "ID file"},
                "name": {"type": "string", "description": "Tên mới"},
            },
            "required": ["file_id", "name"],
        },
        **_mutate(),
    },
    {
        "name": "drive.trash",
        "description": "Chuyển file Drive vào thùng rác (có thể khôi phục). Cần phê duyệt",
        "input_schema": {
            "type": "object",
            "properties": {"file_id": {"type": "string", "description": "ID file"}},
            "required": ["file_id"],
        },
        **_mutate(),
    },
    # --- Docs ---
    {
        "name": "docs.read",
        "description": "Đọc nội dung text của một Google Doc (gồm dùng để tóm tắt)",
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "ID document"},
                "max_chars": {"type": "integer", "description": "Giới hạn ký tự"},
            },
            "required": ["document_id"],
        },
        **_ro(),
    },
    {
        "name": "docs.create",
        "description": "Tạo một Google Doc mới với tiêu đề + nội dung ban đầu. Cần phê duyệt",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Tiêu đề document"},
                "body": {"type": "string", "description": "Nội dung ban đầu (tuỳ chọn)"},
            },
            "required": ["title"],
        },
        **_create(),
    },
    {
        "name": "docs.edit",
        "description": "Chèn/nối text vào Google Doc (mode append|insert). Cần phê duyệt",
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "ID document"},
                "text": {"type": "string", "description": "Nội dung cần chèn"},
                "mode": {"type": "string", "description": "append (mặc định) hoặc insert"},
                "index": {"type": "integer", "description": "Vị trí chèn khi mode=insert"},
            },
            "required": ["document_id", "text"],
        },
        **_mutate(),
    },
    {
        "name": "docs.export",
        "description": "Export một Google Doc ra file trong workspace (txt/pdf/docx)",
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "ID document"},
                "dest_path": {"type": "string", "description": "Đường dẫn đích trong workspace"},
                "format": {"type": "string", "description": "txt | pdf | docx (mặc định txt)"},
            },
            "required": ["document_id", "dest_path"],
        },
        **_local_write(),
    },
    # --- Sheets ---
    {
        "name": "sheets.read",
        "description": "Đọc một vùng dữ liệu của spreadsheet (vd 'Sheet1!A1:D20'). Dùng cho cả báo cáo/phân tích",
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string", "description": "ID spreadsheet"},
                "range": {"type": "string", "description": "Vùng A1, vd 'Sheet1!A1:D20'"},
            },
            "required": ["spreadsheet_id", "range"],
        },
        **_ro(),
    },
    {
        "name": "sheets.update",
        "description": "Ghi giá trị vào một vùng của spreadsheet. Cần phê duyệt",
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string", "description": "ID spreadsheet"},
                "range": {"type": "string", "description": "Vùng A1 cần ghi"},
                "values": {"type": "array", "description": "Mảng 2 chiều các giá trị (hàng x cột)"},
            },
            "required": ["spreadsheet_id", "range", "values"],
        },
        **_mutate(),
    },
    {
        "name": "sheets.append",
        "description": "Thêm các dòng vào cuối một bảng spreadsheet. Cần phê duyệt",
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string", "description": "ID spreadsheet"},
                "range": {"type": "string", "description": "Vùng bảng để thêm dòng"},
                "values": {"type": "array", "description": "Mảng 2 chiều các dòng cần thêm"},
            },
            "required": ["spreadsheet_id", "range", "values"],
        },
        **_mutate(),
    },
    {
        "name": "sheets.create",
        "description": "Tạo một spreadsheet mới với tiêu đề. Cần phê duyệt",
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string", "description": "Tiêu đề spreadsheet"}},
            "required": ["title"],
        },
        **_create(),
    },
]


def run_migration():
    """Create google_workspace_actions table and seed/refresh drive/docs/sheets tools."""
    print(f"Running Google Workspace (Phase 5.2) migration on {DB_PATH}...")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS google_workspace_actions (
                id VARCHAR PRIMARY KEY,
                session_id VARCHAR NOT NULL,
                service VARCHAR,
                action VARCHAR,
                target VARCHAR,
                status VARCHAR,
                details_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gws_action ON google_workspace_actions(session_id)")
        print("Created table: google_workspace_actions")

        seeded = 0
        refreshed = 0
        for tool in DEFAULT_TOOLS:
            schema_json = json.dumps(tool["input_schema"], ensure_ascii=False)
            cur = conn.execute("SELECT id FROM tools WHERE name = ?", (tool["name"],))
            row = cur.fetchone()
            if row:
                conn.execute("""
                    UPDATE tools
                    SET description = ?, input_schema = ?, risk_level = ?, requires_approval = ?,
                        rollback_type = ?, rollback_supported = ?, logs_sensitive_args = ?
                    WHERE name = ?
                """, (
                    tool["description"], schema_json, tool["risk_level"], tool["requires_approval"],
                    tool["rollback_type"], tool["rollback_supported"], tool["logs_sensitive_args"],
                    tool["name"],
                ))
                refreshed += 1
            else:
                conn.execute("""
                    INSERT INTO tools (id, name, description, input_schema, risk_level, requires_approval,
                                       rollback_type, rollback_supported, logs_sensitive_args, enabled, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(uuid.uuid4()), tool["name"], tool["description"], schema_json,
                    tool["risk_level"], tool["requires_approval"], tool["rollback_type"],
                    tool["rollback_supported"], tool["logs_sensitive_args"], 1,
                    datetime.utcnow().isoformat(),
                ))
                seeded += 1

        conn.commit()
        print(f"Seeded {seeded} new, refreshed {refreshed} existing drive/docs/sheets tools "
              f"({len(DEFAULT_TOOLS)} total defined).")
    except Exception as e:
        conn.rollback()
        print(f"Google Workspace migration error: {e}")
        raise
    finally:
        conn.close()

    print("Google Workspace (Phase 5.2) migration completed.")


if __name__ == "__main__":
    run_migration()
