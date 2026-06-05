from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from src.settings import get_settings
from src.data.models import Base


def _make_engine():
    settings = get_settings()
    return create_async_engine(settings.DATABASE_URL, echo=False)


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
