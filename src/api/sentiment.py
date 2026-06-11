"""Sentiment-over-time aggregates for the dashboard's Sentiment page.

Aggregation rules (see docs/sentiment.md "Visualization"):
- Only articles of companies marked active in companies.json.
- Only the latest sentiment row per article (re-scores supersede).
- Rows with relevance = off_topic are excluded: they are keyword false
  matches, not signal. The rows stay in the DB so the scrapers' dedup
  keeps them from being re-collected and re-scored.
- Articles without a published date can't be placed on a time axis and
  are skipped.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.review import _active_tickers, _latest_sentiment, get_session
from src.data.models import Article

ROLLING_WINDOW_DAYS = 7

router = APIRouter(prefix="/api/sentiment", tags=["sentiment"])


def _series_points(scores_by_day: dict[date, list[float]]) -> list[dict[str, object]]:
    """Daily mean + trailing rolling mean for one ticker.

    The rolling mean is article-weighted: mean of every score published in
    the trailing ROLLING_WINDOW_DAYS window (inclusive of the day itself),
    not a mean of daily means. Only days with at least one scored article
    are emitted.
    """
    days = sorted(scores_by_day)
    points: list[dict[str, object]] = []
    for day in days:
        scores = scores_by_day[day]
        window_start = day - timedelta(days=ROLLING_WINDOW_DAYS - 1)
        window_scores = [
            score
            for other_day in days
            if window_start <= other_day <= day
            for score in scores_by_day[other_day]
        ]
        points.append(
            {
                "date": day.isoformat(),
                "mean": sum(scores) / len(scores),
                "rolling": sum(window_scores) / len(window_scores),
                "count": len(scores),
            }
        )
    return points


async def sentiment_timeseries(session: AsyncSession) -> dict[str, object]:
    active_tickers = _active_tickers()

    articles_result = await session.execute(
        select(Article)
        .where(Article.ticker.in_(active_tickers))
        .where(Article.published.is_not(None))
        .options(selectinload(Article.sentiment))
    )

    scores: dict[str, dict[date, list[float]]] = defaultdict(lambda: defaultdict(list))
    for article in articles_result.scalars():
        sentiment = _latest_sentiment(article)
        if sentiment is None or sentiment.relevance == "off_topic":
            continue
        scores[article.ticker][article.published.date()].append(sentiment.score)

    # Every active ticker is returned (empty points when nothing is scored)
    # so the company filter in the GUI is stable regardless of data.
    return {
        "window_days": ROLLING_WINDOW_DAYS,
        "series": [
            {"ticker": ticker, "points": _series_points(scores.get(ticker, {}))}
            for ticker in sorted(active_tickers)
        ],
    }


@router.get("/timeseries")
async def timeseries(
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    return await sentiment_timeseries(session)
