# Database utilities - async operations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from .database import async_engine, AsyncSessionLocal, SessionLocal

# Re-export ORM models so the rest of the app can import them from a single place
from .database import (  # noqa: F401
    Document,
    DocumentVersion,
    Chunk,
    Message,
    AuditLog,
    DocumentMetadata,
    Tool,
    ApprovalRequest,
    ShortTermMemory,
    ShortTermMemoryHistory,
    EpisodicEvent,
    BrowserSession,
    BrowserAction,
    GmailAction,
    GoogleWorkspaceAction,
)


class SyncAsyncSessionAdapter:
    """Tiny async wrapper over the sync SQLAlchemy session.

    This keeps the API surface used by the app working when aiosqlite is not
    available in the environment.
    """

    def __init__(self):
        self._session = SessionLocal()

    async def execute(self, *args, **kwargs):
        return self._session.execute(*args, **kwargs)

    def add(self, *args, **kwargs):
        return self._session.add(*args, **kwargs)

    def add_all(self, *args, **kwargs):
        return self._session.add_all(*args, **kwargs)

    async def commit(self):
        return self._session.commit()

    async def rollback(self):
        return self._session.rollback()

    async def close(self):
        return self._session.close()


async def init_async_db():
    """Initialize async database tables."""
    from .database import Base
    if async_engine is None:
        return
    async with async_engine.begin() as conn:
        await conn.run_sync(lambda conn: conn.execute("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for FastAPI - returns an async session or a sync fallback."""
    if AsyncSessionLocal is None:
        session = SyncAsyncSessionAdapter()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
        return

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
    """Convert sync session operations to async context."""
    return session
