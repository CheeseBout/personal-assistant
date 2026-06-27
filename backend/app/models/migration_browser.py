# -*- coding: utf-8 -*-
"""Database migration for Phase 4 — Browser automation.

Creates browser_sessions / browser_actions tables (raw sqlite3 so it runs without
the async driver) and seeds the browser.* tools into the registry. Tool rows are
upserted by name, mirroring migration_agent_core.run_migration().
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
        "name": "browser.open",
        "description": "Mở một URL trong trình duyệt của agent (http/https, theo allowlist/blocklist)",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Địa chỉ trang cần mở"}},
            "required": ["url"],
        },
        "risk_level": 1, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "browser.observe",
        "description": "Quan sát trang hiện tại: URL, tiêu đề, văn bản hiển thị, form, link và (tuỳ chọn) accessibility tree",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_chars": {"type": "integer", "description": "Giới hạn ký tự văn bản trả về"},
                "accessibility": {"type": "boolean", "description": "Kèm accessibility tree đã rút gọn (role/name)"},
            },
            "required": [],
        },
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "browser.extract",
        "description": "Trích nội dung text khớp một CSS selector trên trang hiện tại",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector"},
                "limit": {"type": "integer", "description": "Số phần tử tối đa"},
            },
            "required": ["selector"],
        },
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "browser.click",
        "description": "Click vào phần tử theo văn bản hoặc CSS selector (cần phê duyệt)",
        "input_schema": {
            "type": "object",
            "properties": {"target": {"type": "string", "description": "Văn bản nút hoặc CSS selector"}},
            "required": ["target"],
        },
        "risk_level": 2, "requires_approval": 1,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "browser.type",
        "description": "Gõ nội dung vào ô input; có thể submit. Cần phê duyệt; giá trị gõ được redact trong log",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "CSS selector của ô nhập"},
                "value": {"type": "string", "description": "Nội dung cần gõ"},
                "submit": {"type": "boolean", "description": "Nhấn Enter để submit sau khi gõ"},
            },
            "required": ["target", "value"],
        },
        "risk_level": 2, "requires_approval": 1,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 1,
    },
    {
        "name": "browser.screenshot",
        "description": "Chụp ảnh màn hình trang hiện tại (PNG base64)",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "browser.wait",
        "description": "Chờ một selector xuất hiện hoặc chờ một khoảng thời gian",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector cần chờ"},
                "ms": {"type": "integer", "description": "Số mili-giây chờ nếu không có selector"},
            },
            "required": [],
        },
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "browser.download",
        "description": "Click vào phần tử tải xuống và lưu file vào thư mục download riêng của agent (cần phê duyệt)",
        "input_schema": {
            "type": "object",
            "properties": {"target": {"type": "string", "description": "Văn bản nút hoặc CSS selector kích hoạt tải xuống"}},
            "required": ["target"],
        },
        "risk_level": 2, "requires_approval": 1,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "browser.upload",
        "description": "Tải một file từ workspace lên ô input[type=file] trên trang (chỉ file trong workspace; cần phê duyệt)",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector của input[type=file]"},
                "path": {"type": "string", "description": "Đường dẫn tương đối trong workspace của file cần upload"},
            },
            "required": ["selector", "path"],
        },
        "risk_level": 2, "requires_approval": 1,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "browser.close",
        "description": "Đóng tab trình duyệt của phiên hiện tại",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
]


def run_migration():
    """Create browser tables and seed/refresh browser.* tools."""
    print(f"Running Browser (Phase 4) migration on {DB_PATH}...")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS browser_sessions (
                id VARCHAR PRIMARY KEY,
                session_id VARCHAR NOT NULL,
                current_url VARCHAR,
                title VARCHAR,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_browser_session ON browser_sessions(session_id)")
        print("Created table: browser_sessions")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS browser_actions (
                id VARCHAR PRIMARY KEY,
                session_id VARCHAR NOT NULL,
                action VARCHAR,
                target VARCHAR,
                status VARCHAR,
                details_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_browser_action ON browser_actions(session_id)")
        print("Created table: browser_actions")

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
        print(f"Seeded {seeded} new, refreshed {refreshed} existing browser.* tools "
              f"({len(DEFAULT_TOOLS)} total defined).")
    except Exception as e:
        conn.rollback()
        print(f"Browser migration error: {e}")
        raise
    finally:
        conn.close()

    print("Browser (Phase 4) migration completed.")


if __name__ == "__main__":
    run_migration()
