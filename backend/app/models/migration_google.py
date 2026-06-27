# -*- coding: utf-8 -*-
"""Database migration for Phase 5 — Google integrations (Gmail first).

Creates the gmail_actions table (raw sqlite3 so it runs without the async
driver) and seeds the gmail.* tools into the registry. Tool rows are upserted
by name, mirroring migration_browser.run_migration().

Risk policy (REQUIREMENTS 26.4): read tools auto-allow; every write/send/label/
trash action requires HITL approval. gmail.send is risk 3 (ask_strong).
"""

import uuid
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "db" / "agent.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


DEFAULT_TOOLS = [
    {
        "name": "gmail.search",
        "description": "Tìm email theo cú pháp Gmail (vd 'from:x@y.com is:unread'); trả id, from, subject, snippet",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Truy vấn tìm kiếm kiểu Gmail"},
                "max_results": {"type": "integer", "description": "Số kết quả tối đa (1-50)"},
            },
            "required": ["query"],
        },
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "gmail.read",
        "description": "Đọc nội dung một email theo message_id (header + body text)",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "ID của email"},
                "max_chars": {"type": "integer", "description": "Giới hạn ký tự body"},
            },
            "required": ["message_id"],
        },
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "gmail.thread_summary",
        "description": "Lấy toàn bộ nội dung text của một thread email để tóm tắt",
        "input_schema": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "string", "description": "ID của thread"},
                "max_chars": {"type": "integer", "description": "Giới hạn ký tự gộp"},
            },
            "required": ["thread_id"],
        },
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "gmail.list_attachments",
        "description": "Liệt kê các tệp đính kèm của một email (tên, kích thước, attachment_id)",
        "input_schema": {
            "type": "object",
            "properties": {"message_id": {"type": "string", "description": "ID của email"}},
            "required": ["message_id"],
        },
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "gmail.get_attachment",
        "description": "Tải một tệp đính kèm về thư mục attachments của agent (ghi file local)",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "ID của email"},
                "attachment_id": {"type": "string", "description": "ID tệp đính kèm"},
                "filename": {"type": "string", "description": "Tên file để lưu"},
            },
            "required": ["message_id", "attachment_id"],
        },
        "risk_level": 1, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "gmail.draft",
        "description": "Tạo bản nháp email (KHÔNG gửi). Cần phê duyệt",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Người nhận"},
                "subject": {"type": "string", "description": "Tiêu đề"},
                "body": {"type": "string", "description": "Nội dung"},
            },
            "required": ["to", "subject", "body"],
        },
        "risk_level": 1, "requires_approval": 1,
        "rollback_type": "reversible", "rollback_supported": 0, "logs_sensitive_args": 1,
    },
    {
        "name": "gmail.send",
        "description": "Gửi một email. Cần phê duyệt mạnh; nội dung/người nhận được redact trong log",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Người nhận"},
                "subject": {"type": "string", "description": "Tiêu đề"},
                "body": {"type": "string", "description": "Nội dung"},
            },
            "required": ["to", "subject", "body"],
        },
        "risk_level": 3, "requires_approval": 1,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 1,
    },
    {
        "name": "gmail.label",
        "description": "Thêm/bỏ nhãn cho một email (add_labels / remove_labels). Cần phê duyệt",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "ID của email"},
                "add_labels": {"type": "array", "items": {"type": "string"}, "description": "Label IDs cần thêm"},
                "remove_labels": {"type": "array", "items": {"type": "string"}, "description": "Label IDs cần bỏ"},
            },
            "required": ["message_id"],
        },
        "risk_level": 2, "requires_approval": 1,
        "rollback_type": "reversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "gmail.trash",
        "description": "Chuyển một email vào thùng rác (có thể khôi phục). Cần phê duyệt",
        "input_schema": {
            "type": "object",
            "properties": {"message_id": {"type": "string", "description": "ID của email"}},
            "required": ["message_id"],
        },
        "risk_level": 2, "requires_approval": 1,
        "rollback_type": "reversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
]


def run_migration():
    """Create gmail_actions table and seed/refresh gmail.* tools."""
    print(f"Running Google (Phase 5) migration on {DB_PATH}...")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS gmail_actions (
                id VARCHAR PRIMARY KEY,
                session_id VARCHAR NOT NULL,
                action VARCHAR,
                target VARCHAR,
                status VARCHAR,
                details_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gmail_action ON gmail_actions(session_id)")
        print("Created table: gmail_actions")

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
        print(f"Seeded {seeded} new, refreshed {refreshed} existing gmail.* tools "
              f"({len(DEFAULT_TOOLS)} total defined).")
    except Exception as e:
        conn.rollback()
        print(f"Google migration error: {e}")
        raise
    finally:
        conn.close()

    print("Google (Phase 5) migration completed.")


if __name__ == "__main__":
    run_migration()

