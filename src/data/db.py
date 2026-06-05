from contextlib import asynccontextmanager
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from src.settings import get_settings
from src.data.models import Base


def _enable_sqlite_fk(engine) -> None:
    """SQLite ignores FK constraints unless PRAGMA foreign_keys=ON per connection."""

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _make_engine():
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    if settings.DATABASE_URL.startswith("sqlite"):
        _enable_sqlite_fk(engine)
    return engine


async def init_db() -> None:
    """Create all tables. Safe to call on every startup (no-op if tables exist)."""
    engine = _make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


@asynccontextmanager
async def get_db():
    """Async context manager yielding a database session."""
    engine = _make_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await engine.dispose()
