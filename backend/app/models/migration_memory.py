# -*- coding: utf-8 -*-
"""Database migration for Phase 6 — Long-term memory.

Creates the long_term_memory table (raw sqlite3 so it runs without the async
driver) and seeds the memory.* tools into the registry. Tool rows are upserted
by name, mirroring migration_sandbox.run_migration().
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
        "name": "memory.save",
        "description": (
            "Lưu một ghi nhớ dài hạn (xuyên phiên) về người dùng hoặc quy trình làm việc. "
            "Dùng cho: sở thích, quy ước, facts đã xác nhận (type=semantic); "
            "hoặc workflow/quy trình lặp lại (type=procedural). "
            "KHÔNG lưu mật khẩu, API key, token, OTP hay dữ liệu nhạy cảm."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Nội dung cần ghi nhớ (ngắn gọn, một ý)"},
                "type": {
                    "type": "string",
                    "enum": ["semantic", "procedural", "episodic"],
                    "description": "Loại ghi nhớ: semantic (tri thức/sở thích), procedural (quy trình), episodic (sự kiện đáng nhớ)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Nhãn phân loại tùy chọn",
                },
            },
            "required": ["content"],
        },
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "reversible", "rollback_supported": 1, "logs_sensitive_args": 0,
    },
    {
        "name": "memory.search",
        "description": (
            "Tìm trong ghi nhớ dài hạn (xuyên phiên) các thông tin liên quan đến truy vấn. "
            "Trả về các ghi nhớ semantic/procedural/episodic đang bật, kèm nguồn gốc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Từ khóa hoặc câu cần tìm"},
                "type": {
                    "type": "string",
                    "enum": ["semantic", "procedural", "episodic"],
                    "description": "Lọc theo loại ghi nhớ (tùy chọn)",
                },
                "limit": {"type": "integer", "description": "Số kết quả tối đa (mặc định 10)"},
            },
            "required": ["query"],
        },
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
]


def run_migration():
    """Create long_term_memory table and seed/refresh memory.* tools."""
    print(f"Running Memory (Phase 6) migration on {DB_PATH}...")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS long_term_memory (
                id VARCHAR PRIMARY KEY,
                type VARCHAR DEFAULT 'semantic',
                content TEXT NOT NULL,
                source VARCHAR,
                confidence INTEGER,
                tags_json TEXT,
                enabled BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used_at TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ltm_type ON long_term_memory(type)")
        print("Created table: long_term_memory")

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
        print(f"Seeded {seeded} new, refreshed {refreshed} existing memory.* tools "
              f"({len(DEFAULT_TOOLS)} total defined).")
    except Exception as e:
        conn.rollback()
        print(f"Memory migration error: {e}")
        raise
    finally:
        conn.close()

    print("Memory (Phase 6) migration completed.")


if __name__ == "__main__":
    run_migration()
