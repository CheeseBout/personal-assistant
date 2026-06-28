# -*- coding: utf-8 -*-
"""Database migration for Phase 8 — News + Scheduler.

Creates the scheduled_tasks and news_reports tables (raw sqlite3 so it runs
without the async driver) and seeds the web.search/news.summarize tools into
the registry. Tool rows are upserted by name, mirroring migration_sandbox.
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
        "name": "web.search",
        "description": (
            "Tìm kiếm web theo truy vấn, trả về danh sách kết quả kèm tiêu đề, link gốc và "
            "đoạn trích. Chỉ đọc, không thay đổi dữ liệu. Dùng khi cần thông tin mới/ngoài tài liệu."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Câu truy vấn tìm kiếm"},
                "max_results": {"type": "integer", "description": "Số kết quả tối đa (mặc định theo cấu hình)"},
            },
            "required": ["query"],
        },
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "news.summarize",
        "description": (
            "Tìm tin tức từ nhiều nguồn, khử trùng lặp và tóm tắt có trích dẫn link gốc. "
            "Phân biệt fact và nhận định, nêu rõ khi nguồn mâu thuẫn. Chỉ dựa trên nguồn lấy được, "
            "không suy đoán. Kết quả được lưu thành báo cáo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Chủ đề/tin cần tóm tắt"},
                "max_sources": {"type": "integer", "description": "Số nguồn tối đa đưa vào tóm tắt"},
            },
            "required": ["query"],
        },
        "risk_level": 1, "requires_approval": 0,
        "rollback_type": "reversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
]


def run_migration():
    """Create scheduled_tasks + news_reports tables and seed news/web tools."""
    print(f"Running News + Scheduler (Phase 8) migration on {DB_PATH}...")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                kind VARCHAR DEFAULT 'news_summary',
                schedule VARCHAR,
                params_json TEXT,
                enabled BOOLEAN DEFAULT 1,
                last_run_at TIMESTAMP,
                last_status VARCHAR,
                next_run_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS news_reports (
                id VARCHAR PRIMARY KEY,
                task_id VARCHAR,
                query VARCHAR,
                summary TEXT,
                sources_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_news_task ON news_reports(task_id)")
        print("Created tables: scheduled_tasks, news_reports")

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
        print(f"Seeded {seeded} new, refreshed {refreshed} existing news/web tools "
              f"({len(DEFAULT_TOOLS)} total defined).")
    except Exception as e:
        conn.rollback()
        print(f"News migration error: {e}")
        raise
    finally:
        conn.close()

    print("News + Scheduler (Phase 8) migration completed.")


if __name__ == "__main__":
    run_migration()
