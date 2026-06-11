from datetime import datetime

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.main import app
from src.api.review import get_session
from src.api.sentiment import sentiment_timeseries
from src.data.models import Article, Sentiment


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


async def add_scored_article(
    session: AsyncSession,
    *,
    ticker: str,
    url: str,
    published: datetime | None,
    score: float,
    relevance: str = "direct",
    scored_at: datetime | None = None,
) -> Article:
    article = Article(
        ticker=ticker,
        source="gnews",
        url=url,
        published=published,
        title="Salmon market update",
        body="Stored body text.",
        fetched_at=dt("2026-06-09T12:00:00"),
    )
    session.add(article)
    await session.flush()
    label = {-1.0: "negative", 0.0: "neutral", 1.0: "positive"}[score]
    session.add(
        Sentiment(
            article_id=article.id,
            score=score,
            label=label,
            relevance=relevance,
            model="model-a",
            scored_at=scored_at or dt("2026-06-09T13:00:00"),
        )
    )
    await session.flush()
    return article


def series_for(payload: dict, ticker: str) -> dict:
    return next(s for s in payload["series"] if s["ticker"] == ticker)


@pytest.mark.asyncio
async def test_daily_mean_groups_scores_by_published_day(session: AsyncSession):
    await add_scored_article(
        session, ticker="MOWI", url="u1", published=dt("2026-06-01T08:00:00"), score=1.0
    )
    await add_scored_article(
        session, ticker="MOWI", url="u2", published=dt("2026-06-01T15:00:00"), score=0.0
    )
    await session.commit()

    payload = await sentiment_timeseries(session)

    points = series_for(payload, "MOWI")["points"]
    assert points == [
        {"date": "2026-06-01", "mean": 0.5, "rolling": 0.5, "count": 2}
    ]


@pytest.mark.asyncio
async def test_rolling_mean_is_article_weighted_over_trailing_window(
    session: AsyncSession,
):
    # Day 1: two positives; day 3: one negative. Rolling on day 3 covers all
    # three articles (article-weighted), not the mean of the two daily means.
    await add_scored_article(
        session, ticker="MOWI", url="u1", published=dt("2026-06-01T08:00:00"), score=1.0
    )
    await add_scored_article(
        session, ticker="MOWI", url="u2", published=dt("2026-06-01T09:00:00"), score=1.0
    )
    await add_scored_article(
        session, ticker="MOWI", url="u3", published=dt("2026-06-03T08:00:00"), score=-1.0
    )
    await session.commit()

    payload = await sentiment_timeseries(session)

    points = series_for(payload, "MOWI")["points"]
    assert points[1]["date"] == "2026-06-03"
    assert points[1]["mean"] == -1.0
    assert points[1]["rolling"] == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_rolling_window_excludes_scores_older_than_seven_days(
    session: AsyncSession,
):
    await add_scored_article(
        session, ticker="MOWI", url="u1", published=dt("2026-06-01T08:00:00"), score=1.0
    )
    # 2026-06-08 is day 8 after 2026-06-01: outside the trailing 7-day window.
    await add_scored_article(
        session, ticker="MOWI", url="u2", published=dt("2026-06-08T08:00:00"), score=-1.0
    )
    await session.commit()

    payload = await sentiment_timeseries(session)

    points = series_for(payload, "MOWI")["points"]
    assert points[1] == {
        "date": "2026-06-08",
        "mean": -1.0,
        "rolling": -1.0,
        "count": 1,
    }


@pytest.mark.asyncio
async def test_off_topic_scores_are_excluded(session: AsyncSession):
    await add_scored_article(
        session,
        ticker="GSF",
        url="u1",
        published=dt("2026-06-01T08:00:00"),
        score=0.0,
        relevance="off_topic",
    )
    await add_scored_article(
        session, ticker="GSF", url="u2", published=dt("2026-06-01T09:00:00"), score=1.0
    )
    await session.commit()

    payload = await sentiment_timeseries(session)

    points = series_for(payload, "GSF")["points"]
    assert points == [{"date": "2026-06-01", "mean": 1.0, "rolling": 1.0, "count": 1}]


@pytest.mark.asyncio
async def test_unscored_and_unpublished_articles_are_skipped(session: AsyncSession):
    unscored = Article(
        ticker="MOWI",
        source="gnews",
        url="u1",
        published=dt("2026-06-01T08:00:00"),
        title="No score yet",
        body=None,
        fetched_at=dt("2026-06-09T12:00:00"),
    )
    session.add(unscored)
    await add_scored_article(
        session, ticker="MOWI", url="u2", published=None, score=1.0
    )
    await session.commit()

    payload = await sentiment_timeseries(session)

    assert series_for(payload, "MOWI")["points"] == []


@pytest.mark.asyncio
async def test_only_latest_sentiment_per_article_counts(session: AsyncSession):
    article = await add_scored_article(
        session,
        ticker="MOWI",
        url="u1",
        published=dt("2026-06-01T08:00:00"),
        score=1.0,
        scored_at=dt("2026-06-09T13:00:00"),
    )
    session.add(
        Sentiment(
            article_id=article.id,
            score=-1.0,
            label="negative",
            relevance="direct",
            model="model-b",
            scored_at=dt("2026-06-10T13:00:00"),
        )
    )
    await session.commit()

    payload = await sentiment_timeseries(session)

    points = series_for(payload, "MOWI")["points"]
    assert points == [{"date": "2026-06-01", "mean": -1.0, "rolling": -1.0, "count": 1}]


@pytest_asyncio.fixture
async def client(session: AsyncSession):
    async def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api_client:
        yield api_client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_timeseries_route(client: httpx.AsyncClient, session: AsyncSession):
    await add_scored_article(
        session, ticker="MOWI", url="u1", published=dt("2026-06-01T08:00:00"), score=1.0
    )
    await session.commit()

    response = await client.get("/api/sentiment/timeseries")

    assert response.status_code == 200
    payload = response.json()
    assert payload["window_days"] == 7
    assert series_for(payload, "MOWI")["points"][0]["mean"] == 1.0


@pytest.mark.asyncio
async def test_all_active_tickers_returned_even_without_data(session: AsyncSession):
    payload = await sentiment_timeseries(session)

    tickers = [series["ticker"] for series in payload["series"]]
    assert "MOWI" in tickers
    assert tickers == sorted(tickers)
    assert all(series["points"] == [] for series in payload["series"])
    assert payload["window_days"] == 7
