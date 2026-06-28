from sqlalchemy import create_engine, Column, String, Integer, DateTime, Text, Boolean, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from datetime import datetime
import os
from pathlib import Path

# Database path - relative to backend folder
DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "db" / "agent.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Async database URL for FastAPI
ASYNC_DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"
# Sync database URL for migrations/init
SYNC_DB_URL = f"sqlite:///{DB_PATH}"

# Sync engine for init_db and fallback operation
engine = create_engine(SYNC_DB_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Async engine and session. In constrained local/dev environments aiosqlite may
# be unavailable; async_db.py falls back to a small sync-backed adapter then.
try:
    async_engine = create_async_engine(ASYNC_DATABASE_URL, echo=False)
    AsyncSessionLocal = sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
except ModuleNotFoundError:
    async_engine = None
    AsyncSessionLocal = None

Base = declarative_base()


class Document(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True)
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    mime_type = Column(String)
    file_hash = Column(String)
    file_size = Column(Integer)  # File size in bytes
    created_at = Column(DateTime, default=datetime.utcnow)
    metadata_json = Column(JSON, default={})
    is_active = Column(Boolean, default=True)


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id = Column(String, primary_key=True)
    document_id = Column(String, nullable=False)
    version = Column(Integer, default=1)
    file_path = Column(String)
    chunk_count = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)


class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(String, primary_key=True)
    document_id = Column(String, nullable=False)
    version = Column(Integer, default=1)
    chunk_index = Column(Integer)
    content = Column(Text)
    embedding_id = Column(String)  # Reference to vector DB
    metadata_json = Column(JSON, default={})


class Message(Base):
    __tablename__ = "messages"

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False)
    role = Column(String)  # user, assistant, system
    content = Column(Text)
    citations = Column(JSON, default=[])
    tool_calls = Column(JSON, default=[])
    created_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True)
    session_id = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)
    actor = Column(String)  # user, agent, system
    action = Column(String)
    details = Column(JSON, default={})


class DocumentMetadata(Base):
    __tablename__ = "document_metadata"

    id = Column(String, primary_key=True)
    document_id = Column(String, nullable=False)
    key = Column(String, nullable=False)
    value = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


# Agent Core tables

class Tool(Base):
    """Tool registry metadata."""
    __tablename__ = "tools"

    id = Column(String, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    input_schema = Column(JSON, default={})
    risk_level = Column(Integer, default=0)  # 0=low, 1=medium, 2=high, 3=critical
    requires_approval = Column(Boolean, default=False)
    rollback_type = Column(String)  # "reversible", "snapshot_required", "compensating_only", "irreversible"
    rollback_supported = Column(Boolean, default=False)
    logs_sensitive_args = Column(Boolean, default=False)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ApprovalRequest(Base):
    """HITL approval requests."""
    __tablename__ = "approval_requests"

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False)
    tool_name = Column(String, nullable=False)
    arguments_json = Column(JSON, default={})
    risk_level = Column(Integer)
    reason = Column(Text)
    status = Column(String, default="pending")  # pending, approved, denied, timeout
    requested_at = Column(DateTime, default=datetime.utcnow)
    decided_at = Column(DateTime)
    decided_by = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class ShortTermMemory(Base):
    """Session-scoped key-value store for agent state."""
    __tablename__ = "short_term_memory"

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    key = Column(String, nullable=False)
    value_json = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ShortTermMemoryHistory(Base):
    """Prior values of short-term memory keys, captured before each mutation.

    Enables undo/rollback for memory (parallel to file snapshots). Each row is
    the value as it existed *before* a set/delete operation.
    """
    __tablename__ = "short_term_memory_history"

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    key = Column(String, nullable=False)
    old_value_json = Column(JSON)  # None if the key did not exist before
    operation = Column(String)  # "set" or "delete"
    existed_before = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class EpisodicEvent(Base):
    """Append-only event log for agent actions."""
    __tablename__ = "episodic_events"

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    actor = Column(String)  # "agent", "user", "system"
    action = Column(String)
    details_json = Column(JSON, default={})
    metadata_json = Column(JSON, default={})
    created_at = Column(DateTime, default=datetime.utcnow)


class LongTermMemory(Base):
    """Durable cross-session memory (Phase 6).

    The umbrella store for memory that persists beyond a single session. The
    ``type`` column distinguishes the sub-kinds described in REQUIREMENTS §9:
    - "semantic":   facts/preferences/conventions (e.g. "User prefers concise VN summaries")
    - "procedural": reusable workflows ("how the user wants the weekly report built")
    - "episodic":   a distilled, kept summary of a past event worth remembering

    Unlike short-term memory this is NOT scoped to a session; ``source`` records
    provenance (where the memory came from, e.g. "conversation:sess_123").
    """
    __tablename__ = "long_term_memory"

    id = Column(String, primary_key=True)
    type = Column(String, default="semantic", index=True)  # semantic | procedural | episodic
    content = Column(Text, nullable=False)
    source = Column(String)            # provenance, e.g. "conversation:sess_123", "user"
    confidence = Column(Integer)       # 0-100 (stored as int to avoid float drift)
    tags_json = Column(JSON, default=[])
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_used_at = Column(DateTime)


# Phase 4 - Browser automation tables

class BrowserSession(Base):
    """Per-chat-session browser tab state (latest known)."""
    __tablename__ = "browser_sessions"

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    current_url = Column(String)
    title = Column(String)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BrowserAction(Base):
    """Append-only log of browser actions (open/click/type/...) for the viewer."""
    __tablename__ = "browser_actions"

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    action = Column(String)  # open, observe, extract, click, type, screenshot, wait, close
    target = Column(String)
    status = Column(String)  # success | error
    details_json = Column(JSON, default={})
    created_at = Column(DateTime, default=datetime.utcnow)


class GmailAction(Base):
    """Append-only log of Gmail actions (search/read/send/...) for the viewer."""
    __tablename__ = "gmail_actions"

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    action = Column(String)  # search, read, thread_summary, draft, send, label, trash, ...
    target = Column(String)
    status = Column(String)  # success | error
    details_json = Column(JSON, default={})
    created_at = Column(DateTime, default=datetime.utcnow)


class GoogleWorkspaceAction(Base):
    """Append-only log of Drive/Docs/Sheets actions for the viewer."""
    __tablename__ = "google_workspace_actions"

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    service = Column(String)  # drive | docs | sheets
    action = Column(String)   # search, read, upload, move, rename, trash, create, edit, update, ...
    target = Column(String)
    status = Column(String)   # success | error
    details_json = Column(JSON, default={})
    created_at = Column(DateTime, default=datetime.utcnow)


# Phase 7 - Sandbox execution tables

class SandboxRun(Base):
    """Append-only log of sandbox executions (python/shell/install) for the viewer."""
    __tablename__ = "sandbox_runs"

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    kind = Column(String)            # python | shell | install
    mode = Column(String)            # A | B | C | D | E
    code = Column(Text)              # code or command (redacted/truncated at write time)
    status = Column(String)          # success | error | killed | denied
    exit_code = Column(Integer)
    killed_reason = Column(String)   # timeout | memory | None
    stdout_preview = Column(Text)
    stderr_preview = Column(Text)
    artifacts_json = Column(JSON, default=[])
    duration_ms = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)


# Phase 8 - News + Scheduler tables

class ScheduledTask(Base):
    """A recurring/one-off job the scheduler runs (e.g. periodic news summary).

    Only safe task kinds are allowed (REQUIREMENTS §20.2): a scheduled task must
    never auto-perform a dangerous action. ``kind`` is restricted at creation
    time to the safe set (news_summary for now).
    """
    __tablename__ = "scheduled_tasks"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    kind = Column(String, default="news_summary")  # safe kinds only
    schedule = Column(String)            # "interval:3600" (seconds) or "cron:H M"
    params_json = Column(JSON, default={})  # e.g. {"query": "...", "max_sources": 5}
    enabled = Column(Boolean, default=True)
    last_run_at = Column(DateTime)
    last_status = Column(String)         # success | error | running | None
    next_run_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class NewsReport(Base):
    """A generated news/web-search summary report (on demand or scheduled)."""
    __tablename__ = "news_reports"

    id = Column(String, primary_key=True)
    task_id = Column(String, index=True)   # source scheduled task, or None for on-demand
    query = Column(String)
    summary = Column(Text)
    sources_json = Column(JSON, default=[])  # [{title, url, snippet, published}]
    created_at = Column(DateTime, default=datetime.utcnow)


def _migrate_schema():
    """Add columns introduced after the initial schema (lightweight, idempotent).

    create_all() does not alter existing tables, so we patch in new columns for
    local databases created by earlier versions.
    """
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()

    wanted = {
        "documents": [("file_size", "INTEGER")],
        "chunks": [("version", "INTEGER DEFAULT 1")],
        # Agent Core tables will be created via create_all, but ensure indexes exist
        "short_term_memory": [("index", "idx_stm_session")],
        "episodic_events": [("index", "idx_episodic_session")],
        "approval_requests": [("index", "idx_approval_session")],
    }
    with engine.begin() as conn:
        for table, columns in wanted.items():
            if table not in existing_tables:
                continue
            if columns[0][0] == "index":
                # Create indexes if they don't exist
                for _, idx_name in columns:
                    # Check if index exists (simple heuristic)
                    try:
                        conn.execute(text(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} (session_id)"))
                    except Exception:
                        pass
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl_type in columns:
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))


def init_db():
    """Initialize database tables"""
    Base.metadata.create_all(bind=engine)
    _migrate_schema()


def get_db():
    """Dependency for FastAPI - returns sync session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_sync_db():
    """Generator yielding a sync session. Use: db = next(get_sync_db()).

    Caller is responsible for closing the session.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        pass  # caller closes
