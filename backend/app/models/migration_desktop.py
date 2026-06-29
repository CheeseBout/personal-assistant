# -*- coding: utf-8 -*-
"""Database migration for Phase 9 — Desktop perception (read-only).

Creates the desktop_observations table (raw sqlite3 so it runs without the async
driver) and seeds the read-only desktop.* perception tools into the registry.
NO control tools (click/type) are added — that is Phase 10. Tool rows are
upserted by name, mirroring migration_news.run_migration().
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
        "name": "desktop.observe",
        "description": (
            "Quan sát màn hình hiện tại (chỉ đọc): chụp ảnh, OCR văn bản, phát hiện cửa sổ đang "
            "hoạt động, đọc cây accessibility và tóm tắt. KHÔNG điều khiển chuột/bàn phím. "
            "Văn bản nhạy cảm được che. Ảnh chụp lưu cục bộ, không gửi ra ngoài trừ khi bật chế độ vision."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "include_summary": {"type": "boolean", "description": "Có tạo tóm tắt màn hình không (mặc định có)"},
            },
            "required": [],
        },
        "risk_level": 1, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "desktop.active_window",
        "description": (
            "Lấy tiêu đề cửa sổ đang ở tiền cảnh (chỉ đọc, không chụp màn hình). Nhẹ, dùng để biết "
            "người dùng đang làm việc trên ứng dụng nào."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "desktop.ui_elements",
        "description": (
            "Đọc cây accessibility (UI elements) của cửa sổ đang hoạt động (chỉ đọc). "
            "Trả về danh sách các phần tử UI: nút, ô nhập, menu, nhãn… kèm vị trí và trạng thái. "
            "Dùng để hiểu cấu trúc giao diện ứng dụng đang mở mà không cần chụp màn hình."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_depth": {"type": "integer", "description": "Độ sâu tối đa của cây UI (mặc định 3)"},
            },
            "required": [],
        },
        "risk_level": 1, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "desktop.list_windows",
        "description": (
            "Liệt kê tất cả cửa sổ đang mở trên desktop (chỉ đọc). "
            "Trả về tiêu đề và trạng thái (visible/minimized) của từng cửa sổ."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
]


def run_migration():
    """Create desktop_observations table and seed read-only desktop.* tools."""
    print(f"Running Desktop Perception (Phase 9) migration on {DB_PATH}...")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS desktop_observations (
                id VARCHAR PRIMARY KEY,
                session_id VARCHAR,
                active_window VARCHAR,
                ocr_text TEXT,
                summary TEXT,
                image_path VARCHAR,
                masked BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_desktop_obs ON desktop_observations(session_id)")
        print("Created table: desktop_observations")

        # Add ui_elements column if missing (for DBs created before this migration version).
        try:
            conn.execute("ALTER TABLE desktop_observations ADD COLUMN ui_elements TEXT")
            print("Added column: desktop_observations.ui_elements")
        except sqlite3.OperationalError:
            pass  # column already exists

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
        print(f"Seeded {seeded} new, refreshed {refreshed} existing desktop.* tools "
              f"({len(DEFAULT_TOOLS)} total defined).")
    except Exception as e:
        conn.rollback()
        print(f"Desktop migration error: {e}")
        raise
    finally:
        conn.close()

    print("Desktop Perception (Phase 9) migration completed.")


if __name__ == "__main__":
    run_migration()
