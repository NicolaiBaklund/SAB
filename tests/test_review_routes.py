from datetime import datetime

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.main import app
from src.api.review import get_session
from src.data.models import Article, Sentiment


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


@pytest_asyncio.fixture
async def client(session: AsyncSession):
    async def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api_client:
        yield api_client
    app.dependency_overrides.clear()


async def seed_article(session: AsyncSession) -> Article:
    article = Article(
        ticker="MOWI",
        source="gnews",
        url="https://example.com/route",
        published=dt("2026-06-09T08:00:00"),
        title="Mowi route test",
        body="Stored route body.",
        fetched_at=dt("2026-06-09T09:00:00"),
    )
    session.add(article)
    await session.flush()
    session.add(
        Sentiment(
            article_id=article.id,
            score=0.7,
            label="positive",
            model="model-a",
            scored_at=dt("2026-06-09T10:00:00"),
        )
    )
    await session.commit()
    return article


@pytest.mark.asyncio
async def test_articles_route_binds_query_parameters(
    client: httpx.AsyncClient,
    session: AsyncSession,
):
    await seed_article(session)

    response = await client.get(
        "/api/review/articles",
        params={
            "limit": "1",
            "offset": "0",
            "score_state": "scored",
            "published_from": "2026-06-01",
            "published_to": "2026-06-30",
            "model": "model-a",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["limit"] == 1
    assert payload["offset"] == 0
    assert payload["items"][0]["companies"][0]["sentiment"]["label"] == "positive"


@pytest.mark.asyncio
async def test_articles_route_rejects_invalid_limit(client: httpx.AsyncClient):
    response = await client.get("/api/review/articles", params={"limit": "0"})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "limit"]


@pytest.mark.asyncio
async def test_filter_options_route(client: httpx.AsyncClient, session: AsyncSession):
    await seed_article(session)

    response = await client.get("/api/review/filter-options")

    assert response.status_code == 200
    assert response.json() == {
        "tickers": ["MOWI"],
        "sources": ["gnews"],
        "labels": ["positive"],
        "models": ["model-a"],
    }

