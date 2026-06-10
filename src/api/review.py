from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import AsyncIterator, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import get_active_companies
from src.data.db import get_db
from src.data.models import Article, Sentiment

ScoreState = Literal["scored", "unscored"]

router = APIRouter(prefix="/api/review", tags=["review"])


def _active_tickers() -> set[str]:
    """Tickers of companies marked active in companies.json."""
    return {company["ticker"] for company in get_active_companies()}


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


def _apply_sql_filters(stmt, filters: ReviewFilters):
    """Apply SQL-eligible filter conditions (ticker, source, published, q)."""
    if filters.ticker:
        stmt = stmt.where(Article.ticker == filters.ticker)
    if filters.source:
        stmt = stmt.where(Article.source == filters.source)
    if filters.published_from:
        stmt = stmt.where(func.date(Article.published) >= filters.published_from)
    if filters.published_to:
        stmt = stmt.where(func.date(Article.published) <= filters.published_to)
    if filters.q:
        stmt = stmt.where(func.lower(Article.title).like(f"%{filters.q.lower()}%"))
    return stmt


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
    active_tickers = _active_tickers()

    # Step 1: Paginate over URL groups in SQL.
    # SQLite places NULLs last in DESC order, so articles with no published date
    # sort after dated ones without an explicit NULLS LAST clause.
    # Only active companies (companies.json) are reviewed, so rows for inactive
    # tickers are excluded before grouping, counting, and paginating.
    url_page_stmt = _apply_sql_filters(
        select(Article.url)
        .where(Article.ticker.in_(active_tickers))
        .group_by(Article.url)
        .order_by(
            func.max(Article.published).desc(),
            func.max(Article.fetched_at).desc(),
        )
        .limit(limit)
        .offset(offset),
        filters,
    )
    total_stmt = _apply_sql_filters(
        select(func.count(distinct(Article.url))).where(
            Article.ticker.in_(active_tickers)
        ),
        filters,
    )

    url_result = await session.execute(url_page_stmt)
    total_result = await session.execute(total_stmt)
    page_urls = [row[0] for row in url_result]
    total = total_result.scalar() or 0

    if not page_urls:
        return {"items": [], "total": total, "limit": limit, "offset": offset}

    # Step 2: Load all rows for the page URLs (all tickers per article, not just
    # the filtered ticker) together with their sentiments.
    articles_result = await session.execute(
        select(Article)
        .where(Article.url.in_(page_urls))
        .where(Article.ticker.in_(active_tickers))
        .options(selectinload(Article.sentiment))
    )
    by_url: dict[str, list[Article]] = {}
    for article in articles_result.scalars().all():
        by_url.setdefault(article.url, []).append(article)

    # Step 3: Apply Python-only filters (label, score_state require loaded sentiments).
    # SQL total counts URLs matching the SQL filters; pages may be shorter when
    # sentiment filters further reduce results.
    has_python_filters = bool(filters.label or filters.score_state)
    items = []
    for url in page_urls:
        rows = by_url.get(url, [])
        if not rows:
            continue
        if has_python_filters and not any(_matches_filters(a, filters) for a in rows):
            continue
        items.append(_article_payload(rows, filters.model))

    return {"items": items, "total": total, "limit": limit, "offset": offset}


async def get_review_filter_options(session: AsyncSession) -> dict[str, list[str]]:
    active_tickers = _active_tickers()
    tickers_result = await session.execute(
        select(distinct(Article.ticker))
        .where(Article.ticker.in_(active_tickers))
        .order_by(Article.ticker)
    )
    sources_result = await session.execute(
        select(distinct(Article.source)).order_by(Article.source)
    )
    labels_result = await session.execute(
        select(distinct(Sentiment.label)).order_by(Sentiment.label)
    )
    models_result = await session.execute(
        select(distinct(Sentiment.model)).order_by(Sentiment.model)
    )
    return {
        "tickers": [row[0] for row in tickers_result],
        "sources": [row[0] for row in sources_result],
        "labels": [row[0] for row in labels_result],
        "models": [row[0] for row in models_result],
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
