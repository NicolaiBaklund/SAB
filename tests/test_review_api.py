from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.review import (
    ReviewFilters,
    get_review_filter_options,
    list_review_articles,
)
from src.data.models import Article, Sentiment


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


async def add_article(
    session: AsyncSession,
    *,
    ticker: str,
    url: str,
    source: str = "gnews",
    title: str = "Salmon market update",
    body: str | None = "Stored body text.",
    published: datetime | None = None,
    fetched_at: datetime | None = None,
) -> Article:
    article = Article(
        ticker=ticker,
        source=source,
        url=url,
        published=published,
        title=title,
        body=body,
        fetched_at=fetched_at or dt("2026-06-09T12:00:00"),
    )
    session.add(article)
    await session.flush()
    return article


async def add_sentiment(
    session: AsyncSession,
    article: Article,
    *,
    score: float,
    label: str,
    model: str = "model-a",
    scored_at: datetime | None = None,
) -> Sentiment:
    sentiment = Sentiment(
        article_id=article.id,
        score=score,
        label=label,
        model=model,
        scored_at=scored_at or dt("2026-06-09T13:00:00"),
    )
    session.add(sentiment)
    await session.flush()
    return sentiment


@pytest.mark.asyncio
async def test_groups_rows_by_url_and_keeps_all_bubbles_after_ticker_filter(
    session: AsyncSession,
):
    url = "https://example.com/shared"
    mowi = await add_article(session, ticker="MOWI", url=url)
    salm = await add_article(session, ticker="SALM", url=url)
    await add_sentiment(session, mowi, score=0.62, label="positive")
    await add_sentiment(session, salm, score=-0.25, label="negative")
    await session.commit()

    payload = await list_review_articles(
        session,
        ReviewFilters(ticker="MOWI"),
    )

    assert payload["total"] == 1
    item = payload["items"][0]
    assert item["url"] == url
    assert [company["ticker"] for company in item["companies"]] == ["MOWI", "SALM"]


@pytest.mark.asyncio
async def test_latest_sentiment_across_any_model_by_default(session: AsyncSession):
    article = await add_article(session, ticker="MOWI", url="https://example.com/latest")
    await add_sentiment(
        session,
        article,
        score=-0.4,
        label="negative",
        model="model-a",
        scored_at=dt("2026-06-09T10:00:00"),
    )
    await add_sentiment(
        session,
        article,
        score=0.8,
        label="positive",
        model="model-b",
        scored_at=dt("2026-06-09T11:00:00"),
    )
    await session.commit()

    payload = await list_review_articles(session)

    sentiment = payload["items"][0]["companies"][0]["sentiment"]
    assert sentiment["score"] == 0.8
    assert sentiment["label"] == "positive"
    assert sentiment["model"] == "model-b"


@pytest.mark.asyncio
async def test_model_filter_uses_latest_sentiment_for_selected_model(
    session: AsyncSession,
):
    article = await add_article(session, ticker="MOWI", url="https://example.com/model")
    await add_sentiment(
        session,
        article,
        score=-0.4,
        label="negative",
        model="model-a",
        scored_at=dt("2026-06-09T10:00:00"),
    )
    await add_sentiment(
        session,
        article,
        score=0.2,
        label="neutral",
        model="model-a",
        scored_at=dt("2026-06-09T12:00:00"),
    )
    await add_sentiment(
        session,
        article,
        score=0.9,
        label="positive",
        model="model-b",
        scored_at=dt("2026-06-09T13:00:00"),
    )
    await session.commit()

    payload = await list_review_articles(session, ReviewFilters(model="model-a"))

    sentiment = payload["items"][0]["companies"][0]["sentiment"]
    assert sentiment["score"] == 0.2
    assert sentiment["label"] == "neutral"
    assert sentiment["model"] == "model-a"


@pytest.mark.asyncio
async def test_selected_model_missing_score_renders_unscored(session: AsyncSession):
    article = await add_article(session, ticker="MOWI", url="https://example.com/unscored")
    await add_sentiment(session, article, score=0.5, label="positive", model="model-a")
    await session.commit()

    payload = await list_review_articles(
        session,
        ReviewFilters(model="model-b", score_state="unscored"),
    )

    assert payload["total"] == 1
    assert payload["items"][0]["companies"][0]["sentiment"] is None


@pytest.mark.asyncio
async def test_label_and_scored_filters_use_selected_latest_sentiment(
    session: AsyncSession,
):
    positive = await add_article(
        session,
        ticker="MOWI",
        source="newsweb",
        url="https://example.com/positive",
        title="Mowi result",
    )
    neutral = await add_article(
        session,
        ticker="SALM",
        source="gnews",
        url="https://example.com/neutral",
        title="SalMar result",
    )
    await add_sentiment(session, positive, score=0.7, label="positive", model="model-a")
    await add_sentiment(session, neutral, score=0.0, label="neutral", model="model-a")
    await session.commit()

    payload = await list_review_articles(
        session,
        ReviewFilters(source="newsweb", label="positive", score_state="scored", q="mowi"),
    )

    assert payload["total"] == 1
    assert payload["items"][0]["url"] == "https://example.com/positive"


@pytest.mark.asyncio
async def test_label_filter_paginates_in_sql(session: AsyncSession):
    """A label match buried behind newer non-matching articles must appear on
    page 1, and total must count only matching URLs (regression: label was
    filtered after pagination, leaving early pages empty)."""
    for i in range(3):
        negative = await add_article(
            session,
            ticker="MOWI",
            url=f"https://example.com/negative-{i}",
            published=dt(f"2026-06-0{i + 2}T10:00:00"),
        )
        await add_sentiment(session, negative, score=-1.0, label="negative")
    positive = await add_article(
        session,
        ticker="MOWI",
        url="https://example.com/old-positive",
        published=dt("2026-06-01T10:00:00"),
    )
    await add_sentiment(session, positive, score=1.0, label="positive")
    await session.commit()

    payload = await list_review_articles(
        session, ReviewFilters(label="positive"), limit=2, offset=0
    )

    assert payload["total"] == 1
    assert [item["url"] for item in payload["items"]] == [
        "https://example.com/old-positive"
    ]


@pytest.mark.asyncio
async def test_label_filter_uses_latest_sentiment_in_sql(session: AsyncSession):
    """An article re-scored from positive to negative no longer matches
    label=positive — only the latest row per article counts."""
    article = await add_article(
        session, ticker="MOWI", url="https://example.com/rescored"
    )
    await add_sentiment(
        session, article, score=1.0, label="positive",
        scored_at=dt("2026-06-09T10:00:00"),
    )
    await add_sentiment(
        session, article, score=-1.0, label="negative",
        scored_at=dt("2026-06-09T12:00:00"),
    )
    await session.commit()

    payload = await list_review_articles(session, ReviewFilters(label="positive"))

    assert payload["total"] == 0
    assert payload["items"] == []


@pytest.mark.asyncio
async def test_date_filter_excludes_null_published_when_active(session: AsyncSession):
    await add_article(
        session,
        ticker="MOWI",
        url="https://example.com/dated",
        published=dt("2026-06-08T09:00:00"),
    )
    await add_article(
        session,
        ticker="SALM",
        url="https://example.com/null-published",
        published=None,
    )
    await session.commit()

    payload = await list_review_articles(
        session,
        ReviewFilters(
            published_from=dt("2026-06-08T00:00:00").date(),
            published_to=dt("2026-06-08T00:00:00").date(),
        ),
    )

    assert payload["total"] == 1
    assert payload["items"][0]["url"] == "https://example.com/dated"


@pytest.mark.asyncio
async def test_pagination_and_sorting(session: AsyncSession):
    await add_article(
        session,
        ticker="MOWI",
        url="https://example.com/old",
        published=dt("2026-06-07T09:00:00"),
        fetched_at=dt("2026-06-09T09:00:00"),
    )
    await add_article(
        session,
        ticker="SALM",
        url="https://example.com/new",
        published=dt("2026-06-08T09:00:00"),
        fetched_at=dt("2026-06-09T08:00:00"),
    )
    await add_article(
        session,
        ticker="LSG",
        url="https://example.com/null",
        published=None,
        fetched_at=dt("2026-06-10T09:00:00"),
    )
    await session.commit()

    payload = await list_review_articles(session, limit=1, offset=1)

    assert payload["total"] == 3
    assert payload["limit"] == 1
    assert payload["offset"] == 1
    assert payload["items"][0]["url"] == "https://example.com/old"


@pytest.mark.asyncio
async def test_filter_options(session: AsyncSession):
    article = await add_article(
        session,
        ticker="MOWI",
        source="newsweb",
        url="https://example.com/options",
    )
    await add_sentiment(session, article, score=0.7, label="positive", model="model-a")
    await session.commit()

    payload = await get_review_filter_options(session)

    assert payload == {
        "tickers": ["MOWI"],
        "sources": ["newsweb"],
        "labels": ["positive"],
        "models": ["model-a"],
    }


@pytest.mark.asyncio
async def test_filter_options_exclude_inactive_ticker_sources(session: AsyncSession):
    article = await add_article(
        session,
        ticker="MOWI",
        source="newsweb",
        url="https://example.com/active",
    )
    await add_sentiment(session, article, score=0.7, label="positive", model="model-a")
    # ZZZZ is not in companies.json, so it counts as inactive.
    await add_article(
        session,
        ticker="ZZZZ",
        source="oldwire",
        url="https://example.com/inactive",
    )
    await session.commit()

    payload = await get_review_filter_options(session)

    assert payload["tickers"] == ["MOWI"]
    assert payload["sources"] == ["newsweb"]

