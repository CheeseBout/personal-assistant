# Database utilities - async operations

from sqlalchemy.ext.asyncio import AsyncSession
from typing import AsyncGenerator

from .database import async_engine, AsyncSessionLocal

# Re-export ORM models so the rest of the app can import them from a single place
from .database import (  # noqa: F401
    Document,
    DocumentVersion,
    Chunk,
    Message,
    AuditLog,
    DocumentMetadata,
)

async def init_async_db():
    """Initialize async database tables"""
    from .database import Base
    async with async_engine.begin() as conn:
        # For SQLite, we need to enable foreign keys
        await conn.run_sync(lambda conn: conn.execute("PRAGMA foreign_keys=ON"))
        # Create all tables
        await conn.run_sync(Base.metadata.create_all)


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for FastAPI - returns async session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def sync_to_async(session):
    """Convert sync session operations to async context"""
    # This is a simple wrapper - in production use run_sync
    return session
