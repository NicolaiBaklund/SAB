import pytest
import pytest_asyncio
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select

from src.data.models import Base, Article, Sentiment

IN_MEMORY = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(IN_MEMORY)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_tables_created(session: AsyncSession):
    result = await session.execute(select(Article))
    assert result.scalars().all() == []

    result = await session.execute(select(Sentiment))
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_insert_article(session: AsyncSession):
    article = Article(
        ticker="MOWI",
        source="newsweb",
        url="https://newsweb.oslobors.no/message/123",
        published=_now(),
        title="Mowi Q1 results",
        body="Strong quarter for Mowi.",
        fetched_at=_now(),
    )
    session.add(article)
    await session.commit()

    result = await session.execute(select(Article))
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].ticker == "MOWI"
    assert rows[0].source == "newsweb"


@pytest.mark.asyncio
async def test_url_unique_constraint(session: AsyncSession):
    url = "https://newsweb.oslobors.no/message/dupe"
    a1 = Article(ticker="SALM", source="newsweb", url=url, fetched_at=_now())
    a2 = Article(ticker="SALM", source="newsweb", url=url, fetched_at=_now())
    session.add(a1)
    await session.commit()

    session.add(a2)
    with pytest.raises(Exception):
        await session.commit()


@pytest.mark.asyncio
async def test_insert_sentiment(session: AsyncSession):
    article = Article(
        ticker="LSG",
        source="e24",
        url="https://e24.no/article/1",
        fetched_at=_now(),
    )
    session.add(article)
    await session.flush()

    sentiment = Sentiment(
        article_id=article.id,
        score=0.75,
        label="positive",
        model="norw-ai-magistral-24b",
        scored_at=_now(),
    )
    session.add(sentiment)
    await session.commit()

    result = await session.execute(select(Sentiment))
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].score == 0.75
    assert rows[0].label == "positive"


@pytest.mark.asyncio
async def test_sentiment_references_article(session: AsyncSession):
    article = Article(ticker="GSF", source="dn", url="https://dn.no/1", fetched_at=_now())
    session.add(article)
    await session.flush()

    sentiment = Sentiment(
        article_id=article.id,
        score=-0.3,
        label="negative",
        model="norw-ai-magistral-24b",
        scored_at=_now(),
    )
    session.add(sentiment)
    await session.commit()

    result = await session.execute(select(Sentiment))
    s = result.scalars().first()
    assert s.article_id == article.id
