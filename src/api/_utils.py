"""Helpers shared by the review and sentiment routers."""

from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_active_companies
from src.data.db import get_db
from src.data.models import Article, Sentiment


def _active_tickers() -> set[str]:
    """Tickers of companies marked active in companies.json."""
    return {company["ticker"] for company in get_active_companies()}


def _latest_sentiment(article: Article, model: str | None = None) -> Sentiment | None:
    rows = article.sentiment
    if model:
        rows = [row for row in rows if row.model == model]
    if not rows:
        return None
    return max(rows, key=lambda row: (row.scored_at, row.id or 0))


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_db() as session:
        yield session
