# -*- coding: utf-8 -*-
"""Database migration for Phase 10 — Desktop control (click/type/keyboard/mouse).

Seeds the desktop.* control tools into the registry. These are the highest-risk
tools in the system: state-changing actions (click/type/key/drag) are seeded at
risk_level=2 + requires_approval=1 so they always route through HITL ask_strong.
mouse_move/scroll/wait are non-mutating (risk 0). Tool rows are upserted by name,
mirroring migration_desktop.run_migration().

NOTE: seeding a tool here does NOT enable control — DESKTOP_ENABLE_CONTROL must
also be turned on in .env. The engine refuses to act while it is off.
"""

import uuid
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "db" / "agent.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


_TARGET_PROPS = {
    "name": {"type": "string", "description": "Tên phần tử UI đích (lấy từ desktop.ui_elements). Ưu tiên dùng cách này."},
    "auto_id": {"type": "string", "description": "automation_id của phần tử UI đích (nếu có)."},
    "x": {"type": "integer", "description": "Toạ độ X tuyệt đối (dùng khi không có name/auto_id)."},
    "y": {"type": "integer", "description": "Toạ độ Y tuyệt đối (dùng khi không có name/auto_id)."},
}


DEFAULT_TOOLS = [
    {
        "name": "desktop.click",
        "description": (
            "Click vào một phần tử UI trên màn hình. Ưu tiên xác định phần tử qua name/auto_id "
            "(lấy từ desktop.ui_elements); nếu không có thì dùng toạ độ x,y. Hành động này thay đổi "
            "trạng thái máy người dùng nên LUÔN cần xác nhận. Chỉ hoạt động khi đã bật điều khiển."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                **_TARGET_PROPS,
                "button": {"type": "string", "description": "left | right | middle (mặc định left)"},
                "double": {"type": "boolean", "description": "Double-click nếu true"},
                "verify": {"type": "boolean", "description": "Quan sát lại sau khi click để xác nhận"},
            },
            "required": [],
        },
        "risk_level": 2, "requires_approval": 1,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "desktop.type",
        "description": (
            "Gõ văn bản vào ô nhập đang focus hoặc vào phần tử chỉ định qua name/auto_id. "
            "Có thể nhấn Enter sau khi gõ (tham số enter). KHÔNG bao giờ tự gõ mật khẩu/OTP — "
            "gõ vào ô nhạy cảm sẽ bị nâng mức rủi ro tối đa. Luôn cần xác nhận."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Nội dung cần gõ"},
                "name": {"type": "string", "description": "Tên ô nhập đích (tuỳ chọn)"},
                "auto_id": {"type": "string", "description": "automation_id ô nhập đích (tuỳ chọn)"},
                "enter": {"type": "boolean", "description": "Nhấn Enter sau khi gõ"},
                "verify": {"type": "boolean", "description": "Quan sát lại sau khi gõ"},
            },
            "required": ["text"],
        },
        "risk_level": 2, "requires_approval": 1,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 1,
    },
    {
        "name": "desktop.key",
        "description": (
            "Nhấn một phím hoặc tổ hợp phím, ví dụ 'enter', 'tab', 'ctrl+c', 'ctrl+v'. "
            "Tổ hợp hệ thống nguy hiểm (win+r, ctrl+alt+del, alt+f4) bị nâng mức rủi ro tối đa. "
            "Luôn cần xác nhận."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keys": {"type": "string", "description": "Phím/tổ hợp, ví dụ 'enter' hoặc 'ctrl+c'"},
                "verify": {"type": "boolean", "description": "Quan sát lại sau khi nhấn"},
            },
            "required": ["keys"],
        },
        "risk_level": 2, "requires_approval": 1,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "desktop.drag",
        "description": (
            "Kéo-thả chuột từ toạ độ (from_x, from_y) tới (to_x, to_y). Dùng để chọn vùng, "
            "di chuyển đối tượng. Thay đổi trạng thái nên luôn cần xác nhận."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_x": {"type": "integer", "description": "Toạ độ X điểm bắt đầu"},
                "from_y": {"type": "integer", "description": "Toạ độ Y điểm bắt đầu"},
                "to_x": {"type": "integer", "description": "Toạ độ X điểm kết thúc"},
                "to_y": {"type": "integer", "description": "Toạ độ Y điểm kết thúc"},
                "button": {"type": "string", "description": "left | right | middle (mặc định left)"},
                "verify": {"type": "boolean", "description": "Quan sát lại sau khi kéo"},
            },
            "required": ["from_x", "from_y", "to_x", "to_y"],
        },
        "risk_level": 2, "requires_approval": 1,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "desktop.mouse_move",
        "description": (
            "Di chuyển con trỏ chuột tới toạ độ x,y hoặc tới một phần tử UI (không click). "
            "Không thay đổi trạng thái nên có thể chạy ngay."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Toạ độ X"},
                "y": {"type": "integer", "description": "Toạ độ Y"},
                "name": {"type": "string", "description": "Tên phần tử để di chuột tới (tuỳ chọn)"},
                "auto_id": {"type": "string", "description": "automation_id phần tử (tuỳ chọn)"},
            },
            "required": [],
        },
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "desktop.scroll",
        "description": (
            "Cuộn cửa sổ đang hoạt động lên hoặc xuống một lượng nhất định. "
            "Không thay đổi dữ liệu nên có thể chạy ngay."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "integer", "description": "Số nấc cuộn (>0)"},
                "direction": {"type": "string", "description": "up | down (mặc định down)"},
            },
            "required": ["amount"],
        },
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
    {
        "name": "desktop.wait",
        "description": (
            "Chờ một khoảng thời gian (seconds) hoặc chờ tới khi một phần tử UI xuất hiện "
            "(name/auto_id, tối đa timeout giây). Dùng giữa các bước điều khiển. Chỉ chờ, không thao tác."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "Số giây chờ cố định"},
                "name": {"type": "string", "description": "Chờ tới khi phần tử có tên này xuất hiện"},
                "auto_id": {"type": "string", "description": "Chờ tới khi phần tử có automation_id này xuất hiện"},
                "timeout": {"type": "number", "description": "Thời gian chờ tối đa khi đợi phần tử (giây)"},
            },
            "required": [],
        },
        "risk_level": 0, "requires_approval": 0,
        "rollback_type": "irreversible", "rollback_supported": 0, "logs_sensitive_args": 0,
    },
]


def run_migration():
    """Seed desktop.* control tools (Phase 10) into the registry."""
    print(f"Running Desktop Control (Phase 10) migration on {DB_PATH}...")

    conn = sqlite3.connect(str(DB_PATH))
    try:
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
        print(f"Seeded {seeded} new, refreshed {refreshed} existing desktop.* control tools "
              f"({len(DEFAULT_TOOLS)} total defined).")
    except Exception as e:
        conn.rollback()
        print(f"Desktop control migration error: {e}")
        raise
    finally:
        conn.close()

    print("Desktop Control (Phase 10) migration completed.")


if __name__ == "__main__":
    run_migration()
