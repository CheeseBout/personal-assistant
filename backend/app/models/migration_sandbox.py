# -*- coding: utf-8 -*-
"""Database migration for Phase 7 — Sandbox execution.

Creates the sandbox_runs table (raw sqlite3 so it runs without the async driver)
and seeds the sandbox.* tools into the registry. Tool rows are upserted by name,
mirroring migration_browser.run_migration().
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
        "name": "sandbox.python",
        "description": "Chạy đoạn code Python trong sandbox cô lập (Mode A mặc định: không mạng, chỉ workspace, timeout ngắn). Bật allow_network hoặc install cần phê duyệt mạnh.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Mã Python cần chạy"},
                "allow_network": {"type": "boolean", "description": "Cho phép truy cập mạng (Mode C, cần phê duyệt)"},
                "timeout": {"type": "integer", "description": "Giới hạn thời gian (giây)"},
            },
            "required": ["code"],
        },
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 1,
    },
    {
        "name": "sandbox.shell",
        "description": "Chạy một lệnh shell trong sandbox (Mode D). Lệnh được phân tích tĩnh (command analyzer); lệnh ghi file/mạng/đọc ngoài workspace cần phê duyệt; lệnh phá hoại bị chặn.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Lệnh shell cần chạy"},
                "timeout": {"type": "integer", "description": "Giới hạn thời gian (giây)"},
            },
            "required": ["command"],
        },
        "risk_level": 2, "requires_approval": 1,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 1,
    },
    {
        "name": "sandbox.install",
        "description": "Cài đặt gói Python bằng pip vào sandbox (Mode C: cần mạng + phê duyệt mạnh). Gói được cài vào thư mục riêng của phiên, có cache.",
        "input_schema": {
            "type": "object",
            "properties": {
                "packages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Danh sách tên gói (vd: ['requests', 'numpy==1.24.3'])",
                },
            },
            "required": ["packages"],
        },
        "risk_level": 2, "requires_approval": 1,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "sandbox.list_artifacts",
        "description": "Liệt kê các file hiện có trong thư mục sandbox của phiên",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "sandbox.read_artifact",
        "description": "Đọc nội dung một file artifact trong sandbox của phiên",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Tên file tương đối trong sandbox"}},
            "required": ["name"],
        },
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
]


def run_migration():
    """Create sandbox_runs table and seed/refresh sandbox.* tools."""
    print(f"Running Sandbox (Phase 7) migration on {DB_PATH}...")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sandbox_runs (
                id VARCHAR PRIMARY KEY,
                session_id VARCHAR NOT NULL,
                kind VARCHAR,
                mode VARCHAR,
                code TEXT,
                status VARCHAR,
                exit_code INTEGER,
                killed_reason VARCHAR,
                stdout_preview TEXT,
                stderr_preview TEXT,
                artifacts_json TEXT,
                duration_ms INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sandbox_run ON sandbox_runs(session_id)")
        print("Created table: sandbox_runs")

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
        print(f"Seeded {seeded} new, refreshed {refreshed} existing sandbox.* tools "
              f"({len(DEFAULT_TOOLS)} total defined).")
    except Exception as e:
        conn.rollback()
        print(f"Sandbox migration error: {e}")
        raise
    finally:
        conn.close()

    print("Sandbox (Phase 7) migration completed.")


if __name__ == "__main__":
    run_migration()
