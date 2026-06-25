"""Database migration for Agent Core tables.

Creates the tables required for Agent Core if they don't exist, and seeds default
tools into the registry. Uses the stdlib sqlite3 driver directly so the script
can run without the async driver installed.
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
        "name": "file.read",
        "description": "Ã„ÂÃ¡Â»Âc nÃ¡Â»â„¢i dung file trong workspace",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Ã„ÂÃ†Â°Ã¡Â»Âng dÃ¡ÂºÂ«n tÃ†Â°Ã†Â¡ng Ã„â€˜Ã¡Â»â€˜i trong workspace"}},
            "required": ["path"],
        },
        "risk_level": 0,
        "requires_approval": 0,
        "rollback_type": "irreversible",
        "rollback_supported": 0,
        "logs_sensitive_args": 0,
    },
    {
        "name": "file.write",
        "description": "Ghi nÃ¡Â»â„¢i dung vÃƒÂ o file trong workspace",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ã„ÂÃ†Â°Ã¡Â»Âng dÃ¡ÂºÂ«n tÃ†Â°Ã†Â¡ng Ã„â€˜Ã¡Â»â€˜i trong workspace"},
                "content": {"type": "string", "description": "NÃ¡Â»â„¢i dung Ã„â€˜Ã¡Â»Æ’ ghi"},
            },
            "required": ["path", "content"],
        },
        "risk_level": 1,
        "requires_approval": 1,
        "rollback_type": "reversible",
        "rollback_supported": 1,
        "logs_sensitive_args": 0,
    },
    {
        "name": "file.list",
        "description": "LiÃ¡Â»â€¡t kÃƒÂª nÃ¡Â»â„¢i dung thÃ†Â° mÃ¡Â»Â¥c trong workspace",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Ã„ÂÃ†Â°Ã¡Â»Âng dÃ¡ÂºÂ«n thÃ†Â° mÃ¡Â»Â¥c (mÃ¡ÂºÂ·c Ã„â€˜Ã¡Â»â€¹nh lÃƒÂ  workspace root)"}},
            "required": [],
        },
        "risk_level": 0,
        "requires_approval": 0,
        "rollback_type": "irreversible",
        "rollback_supported": 0,
        "logs_sensitive_args": 0,
    },
    {
        "name": "file.delete",
        "description": "XÃƒÂ³a file trong workspace",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Ã„ÂÃ†Â°Ã¡Â»Âng dÃ¡ÂºÂ«n tÃ†Â°Ã†Â¡ng Ã„â€˜Ã¡Â»â€˜i Ã„â€˜Ã¡ÂºÂ¿n file cÃ¡ÂºÂ§n xÃƒÂ³a"}},
            "required": ["path"],
        },
        "risk_level": 2,
        "requires_approval": 1,
        "rollback_type": "snapshot_required",
        "rollback_supported": 1,
        "logs_sensitive_args": 0,
    },
    {
        "name": "file.undo",
        "description": "KhÃƒÂ´i phÃ¡Â»Â¥c file tÃ¡Â»Â« snapshot Ã„â€˜ÃƒÂ£ tÃ¡ÂºÂ¡o trÃ†Â°Ã¡Â»â€ºc khi ghi/xÃƒÂ³a",
        "input_schema": {
            "type": "object",
            "properties": {
                "snapshot": {"type": "string", "description": "Ã„ÂÃ†Â°Ã¡Â»Âng dÃ¡ÂºÂ«n snapshot (tÃ†Â°Ã†Â¡ng Ã„â€˜Ã¡Â»â€˜i workspace) do write/delete trÃ¡ÂºÂ£ vÃ¡Â»Â"},
                "path": {"type": "string", "description": "Ã„ÂÃ†Â°Ã¡Â»Âng dÃ¡ÂºÂ«n file Ã„â€˜ÃƒÂ­ch cÃ¡ÂºÂ§n khÃƒÂ´i phÃ¡Â»Â¥c"},
            },
            "required": ["snapshot", "path"],
        },
        "risk_level": 1,
        "requires_approval": 0,
        "rollback_type": "reversible",
        "rollback_supported": 1,
        "logs_sensitive_args": 0,
    },
    {
        "name": "rag.search",
        "description": "TÃƒÂ¬m kiÃ¡ÂºÂ¿m thÃƒÂ´ng tin tÃ¡Â»Â« tÃƒÂ i liÃ¡Â»â€¡u Ã„â€˜ÃƒÂ£ upload",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "CÃƒÂ¢u hÃ¡Â»Âi/tÃ¡Â»Â« khÃƒÂ³a tÃƒÂ¬m kiÃ¡ÂºÂ¿m"},
                "n_results": {"type": "integer", "description": "SÃ¡Â»â€˜ lÃ†Â°Ã¡Â»Â£ng kÃ¡ÂºÂ¿t quÃ¡ÂºÂ£ tÃ¡Â»â€˜i Ã„â€˜a"},
            },
            "required": ["query"],
        },
        "risk_level": 0,
        "requires_approval": 0,
        "rollback_type": "irreversible",
        "rollback_supported": 0,
        "logs_sensitive_args": 0,
    },
]


def run_migration():
    """Create Agent Core tables and seed default tools."""
    print(f"Running Agent Core database migration on {DB_PATH}...")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tools (
                id VARCHAR PRIMARY KEY,
                name VARCHAR UNIQUE NOT NULL,
                description TEXT,
                input_schema TEXT,
                risk_level INTEGER DEFAULT 0,
                requires_approval BOOLEAN DEFAULT 0,
                rollback_type VARCHAR,
                rollback_supported BOOLEAN DEFAULT 0,
                logs_sensitive_args BOOLEAN DEFAULT 0,
                enabled BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("Created table: tools")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS approval_requests (
                id VARCHAR PRIMARY KEY,
                session_id VARCHAR NOT NULL,
                tool_name VARCHAR NOT NULL,
                arguments_json TEXT,
                risk_level INTEGER,
                reason TEXT,
                status VARCHAR DEFAULT 'pending',
                requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                decided_at TIMESTAMP,
                decided_by VARCHAR,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_approval_session ON approval_requests(session_id, status)")
        print("Created table: approval_requests")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS short_term_memory (
                id VARCHAR PRIMARY KEY,
                session_id VARCHAR NOT NULL,
                key VARCHAR NOT NULL,
                value_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stm_session ON short_term_memory(session_id)")
        print("Created table: short_term_memory")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS episodic_events (
                id VARCHAR PRIMARY KEY,
                session_id VARCHAR NOT NULL,
                actor VARCHAR,
                action VARCHAR,
                details_json TEXT,
                metadata_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_episodic_session ON episodic_events(session_id)")
        print("Created table: episodic_events")

        seeded = 0
        for tool in DEFAULT_TOOLS:
            cur = conn.execute("SELECT id FROM tools WHERE name = ?", (tool["name"],))
            if cur.fetchone():
                continue
            conn.execute("""
                INSERT INTO tools (id, name, description, input_schema, risk_level, requires_approval,
                                   rollback_type, rollback_supported, logs_sensitive_args, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(uuid.uuid4()),
                tool["name"],
                tool["description"],
                json.dumps(tool["input_schema"]),
                tool["risk_level"],
                tool["requires_approval"],
                tool["rollback_type"],
                tool["rollback_supported"],
                tool["logs_sensitive_args"],
                1,
                datetime.utcnow().isoformat(),
            ))
            seeded += 1

        conn.commit()
        print(f"Seeded {seeded} new tools ({len(DEFAULT_TOOLS)} total defined).")
    except Exception as e:
        conn.rollback()
        print(f"Migration error: {e}")
        raise
    finally:
        conn.close()

    print("Agent Core migration completed.")


if __name__ == "__main__":
    run_migration()
