import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.data.models import Base

IN_MEMORY = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def session():
    """In-memory SQLite session with FK enforcement and tables created.

    Shared by tests that need a real database without touching the on-disk DB.
    """
    engine = create_async_engine(IN_MEMORY)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()
