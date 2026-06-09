from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import AsyncIterator, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.data.db import get_db
from src.data.models import Article, Sentiment

ScoreState = Literal["scored", "unscored"]

router = APIRouter(prefix="/api/review", tags=["review"])


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_db() as session:
        yield session


@dataclass(frozen=True)
class ReviewFilters:
    ticker: str | None = None
    source: str | None = None
    label: str | None = None
    score_state: ScoreState | None = None
    model: str | None = None
    published_from: date | None = None
    published_to: date | None = None
    q: str | None = None


def _latest_sentiment(article: Article, model: str | None = None) -> Sentiment | None:
    rows = article.sentiment
    if model:
        rows = [row for row in rows if row.model == model]
    if not rows:
        return None
    return max(rows, key=lambda row: (row.scored_at, row.id or 0))


def _sentiment_payload(sentiment: Sentiment | None) -> dict[str, object] | None:
    if sentiment is None:
        return None
    return {
        "score": sentiment.score,
        "label": sentiment.label,
        "model": sentiment.model,
        "scored_at": sentiment.scored_at,
    }


def _matches_filters(article: Article, filters: ReviewFilters) -> bool:
    sentiment = _latest_sentiment(article, filters.model)

    if filters.ticker and article.ticker != filters.ticker:
        return False
    if filters.source and article.source != filters.source:
        return False
    if filters.label and (sentiment is None or sentiment.label != filters.label):
        return False
    if filters.score_state == "scored" and sentiment is None:
        return False
    if filters.score_state == "unscored" and sentiment is not None:
        return False
    if filters.published_from or filters.published_to:
        if article.published is None:
            return False
        published_day = article.published.date()
        if filters.published_from and published_day < filters.published_from:
            return False
        if filters.published_to and published_day > filters.published_to:
            return False
    if filters.q:
        title = article.title or ""
        if filters.q.casefold() not in title.casefold():
            return False
    return True


def _article_sort_values(rows: list[Article]) -> tuple[bool, datetime, datetime]:
    published_values = [row.published for row in rows if row.published is not None]
    newest_published = max(published_values) if published_values else datetime.min
    newest_fetched = max(row.fetched_at for row in rows)
    return (bool(published_values), newest_published, newest_fetched)


def _representative_article(rows: list[Article]) -> Article:
    return sorted(rows, key=lambda row: _article_sort_values([row]), reverse=True)[0]


def _article_payload(rows: list[Article], model: str | None) -> dict[str, object]:
    article = _representative_article(rows)
    companies = sorted(rows, key=lambda row: row.ticker)
    return {
        "url": article.url,
        "source": article.source,
        "title": article.title,
        "body": article.body,
        "published": article.published,
        "fetched_at": article.fetched_at,
        "companies": [
            {
                "article_id": company.id,
                "ticker": company.ticker,
                "sentiment": _sentiment_payload(_latest_sentiment(company, model)),
            }
            for company in companies
        ],
    }


async def list_review_articles(
    session: AsyncSession,
    filters: ReviewFilters | None = None,
    *,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, object]:
    filters = filters or ReviewFilters()
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    result = await session.execute(
        select(Article).options(selectinload(Article.sentiment)).order_by(Article.id)
    )
    articles = list(result.scalars().all())

    matching_urls = {
        article.url for article in articles if _matches_filters(article, filters)
    }
    grouped: dict[str, list[Article]] = {
        url: [article for article in articles if article.url == url]
        for url in matching_urls
    }

    ordered_groups = sorted(
        grouped.values(),
        key=_article_sort_values,
        reverse=True,
    )
    page = ordered_groups[offset : offset + limit]

    return {
        "items": [_article_payload(rows, filters.model) for rows in page],
        "total": len(ordered_groups),
        "limit": limit,
        "offset": offset,
    }


async def get_review_filter_options(session: AsyncSession) -> dict[str, list[str]]:
    article_result = await session.execute(select(Article))
    sentiment_result = await session.execute(select(Sentiment))
    articles = list(article_result.scalars().all())
    sentiments = list(sentiment_result.scalars().all())
    return {
        "tickers": sorted({article.ticker for article in articles}),
        "sources": sorted({article.source for article in articles}),
        "labels": sorted({sentiment.label for sentiment in sentiments}),
        "models": sorted({sentiment.model for sentiment in sentiments}),
    }


@router.get("/articles")
async def articles(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ticker: str | None = None,
    source: str | None = None,
    label: str | None = None,
    score_state: ScoreState | None = None,
    model: str | None = None,
    published_from: date | None = None,
    published_to: date | None = None,
    q: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    filters = ReviewFilters(
        ticker=ticker,
        source=source,
        label=label,
        score_state=score_state,
        model=model,
        published_from=published_from,
        published_to=published_to,
        q=q,
    )
    return await list_review_articles(session, filters, limit=limit, offset=offset)


@router.get("/filter-options")
async def filter_options(
    session: AsyncSession = Depends(get_session),
) -> dict[str, list[str]]:
    return await get_review_filter_options(session)
