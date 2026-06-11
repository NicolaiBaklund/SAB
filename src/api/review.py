from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api._utils import _active_tickers, _latest_sentiment, get_session
from src.data.models import Article, Sentiment

ScoreState = Literal["scored", "unscored"]

router = APIRouter(prefix="/api/review", tags=["review"])


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


def _sentiment_payload(sentiment: Sentiment | None) -> dict[str, object] | None:
    if sentiment is None:
        return None
    return {
        "score": sentiment.score,
        "label": sentiment.label,
        "model": sentiment.model,
        "scored_at": sentiment.scored_at,
    }


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


def _latest_label_sql(model: str | None):
    """Label of an article's latest sentiment row, as a correlated subquery.

    Must mirror :func:`_latest_sentiment` (used for display): newest ``scored_at``
    wins, ``id`` breaks ties, optionally scoped to one model.
    """
    stmt = (
        select(Sentiment.label)
        .where(Sentiment.article_id == Article.id)
        .order_by(Sentiment.scored_at.desc(), Sentiment.id.desc())
        .limit(1)
    )
    if model:
        stmt = stmt.where(Sentiment.model == model)
    return stmt.scalar_subquery()


def _apply_sql_filters(stmt, filters: ReviewFilters):
    """Apply every filter as SQL so pagination and total stay exact.

    label/score_state are evaluated against the latest sentiment per article
    (scoped to the model filter when set), matching what the payload displays.
    """
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
    if filters.label:
        stmt = stmt.where(_latest_label_sql(filters.model) == filters.label)
    if filters.score_state:
        scored = select(Sentiment.id).where(Sentiment.article_id == Article.id)
        if filters.model:
            scored = scored.where(Sentiment.model == filters.model)
        stmt = stmt.where(
            scored.exists() if filters.score_state == "scored" else ~scored.exists()
        )
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
    # Filters apply per article row before GROUP BY, so a URL is kept when *any*
    # of its (ticker, url) rows matches — a multi-company article surfaces if at
    # least one of its companies passes the filter.
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

    items = [
        _article_payload(by_url[url], filters.model) for url in page_urls if url in by_url
    ]

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
