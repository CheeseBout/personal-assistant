from sqlalchemy import create_engine, Column, String, Integer, DateTime, Text, Boolean, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os
from pathlib import Path

# Database path - relative to backend folder
DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "db" / "agent.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

# For sync operations (migrations, initial setup)
SYNC_DB_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(SYNC_DB_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Document(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True)
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    mime_type = Column(String)
    file_hash = Column(String)
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


def init_db():
    """Initialize database tables"""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Dependency for FastAPI - returns sync session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
