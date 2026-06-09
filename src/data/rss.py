"""News RSS scraper — Phase 1.5.

Fetches general news about the active companies and stores it in the ``articles``
table alongside the Newsweb announcements (Phase 1.3 / 1.4).

## Source: Google News RSS

The originally-planned sources (E24, DN, Intrafish) do not all expose a usable
RSS feed — DN and Intrafish are paywalled (NHST) and serve no public feed. Rather
than scrape three different sites, we use **Google News' RSS search** as a single
aggregator: one query per company returns recent articles about that company from
*all* indexed media (E24, DN, Intrafish, NTB, local and international press):

    https://news.google.com/rss/search?q="<term>"&hl=<lang>&gl=<country>&ceid=<...>

We query each company in Norwegian *and* English (the salmon trade press, e.g.
Intrafish, is English) — see ``GNEWS_LOCALES``. The trade-off is that Google News
``link`` values are Google redirect URLs, not canonical publisher links, and the
endpoint is unofficial. If clean canonical URLs become important, swap this module
for native aggregator feeds (E24, DN/FA, …) — the rest of the pipeline is unchanged.

## How an item becomes article rows

Google News queries are company-scoped but not authoritative, so every returned
item is keyword-matched (title + summary) against *all* companies in
``companies.json``. A single article can mention several companies, so it is
stored **once per matched ticker** — that is why the ``articles`` uniqueness is on
``(ticker, url)`` rather than ``url`` alone (see the 1.5 migration). Items that
match no tracked company are dropped.

Distinguishing *which* company a multi-company article is positive/negative about
is the sentiment scorer's job (Phase 2), not this scraper's.

Cross-source dedup (the same event appearing here *and* in Newsweb under a
different URL) is intentionally **not** handled yet — see roadmap "known issues".

## Usage

    python -m src.data.rss --backfill        # all active companies
    python -m src.data.rss --incremental     # daily job (dedup drops seen rows)

For RSS the two modes do the same fetch: a feed only exposes its current window,
so there is no historical backfill to do. Both flags exist for parity with the
Newsweb CLI and cron scheduling. Deduplication is by ``(ticker, url)``, so runs
are safe to repeat.

Requires the database to exist first (``alembic upgrade head``).
"""
from __future__ import annotations

import argparse
import asyncio
import html
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote

import feedparser
import httpx
from sqlalchemy import select

from src.config import get_active_companies
from src.data.db import get_db
from src.data.models import Article

logger = logging.getLogger(__name__)

SOURCE = "gnews"
GNEWS_SEARCH_URL = "https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}"
# (hl, gl, ceid) locales to query per company. Norwegian for domestic press,
# English for the salmon trade press (Intrafish et al.).
GNEWS_LOCALES = [
    ("no", "NO", "NO:no"),
    ("en", "US", "US:en"),
]
_USER_AGENT = "Mozilla/5.0 (compatible; SAB-rss-scraper/1.0; salmon sentiment research)"


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def _now_utc() -> datetime:
    """Timezone-naive UTC, matching the convention used elsewhere in the project."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clean_text(value: str | None) -> str:
    """Strip HTML tags and entities from a feed summary, collapsing whitespace.

    Google News summaries are small HTML blobs (anchor + source name); we only
    want plain text for the scorer.
    """
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _parse_published(entry: dict) -> datetime | None:
    """Convert a feedparser entry's parsed time (UTC ``struct_time``) to naive UTC."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6])


def build_feeds(companies: list[dict]) -> list[tuple[str, str]]:
    """Build the ``(source, url)`` feeds to fetch: one Google News query per
    company per locale.

    The query term is the company's first keyword (its common name), wrapped in
    quotes for a phrase search to cut obvious noise (e.g. the *Grieg* oilfield vs
    *Grieg Seafood*).
    """
    feeds: list[tuple[str, str]] = []
    for company in companies:
        keywords = company.get("keywords") or []
        if not keywords:
            logger.warning("%s has no keywords; skipping", company.get("ticker"))
            continue
        query = quote(f'"{keywords[0]}"')
        for hl, gl, ceid in GNEWS_LOCALES:
            feeds.append(
                (SOURCE, GNEWS_SEARCH_URL.format(query=query, hl=hl, gl=gl, ceid=ceid))
            )
    return feeds


def match_companies(text: str, companies: list[dict]) -> list[str]:
    """Tickers whose keywords appear (whole-word, case-insensitive) in ``text``.

    Returns one ticker per matching company, in ``companies`` order. Empty when
    nothing matches (the article is then dropped).
    """
    hits: list[str] = []
    for company in companies:
        for keyword in company.get("keywords") or []:
            if re.search(rf"\b{re.escape(keyword)}\b", text or "", re.IGNORECASE):
                hits.append(company["ticker"])
                break
    return hits


def entry_to_article(
    entry: dict, source: str, ticker: str, fetched_at: datetime
) -> Article:
    """Map a feedparser entry to an ``Article`` row for one ticker."""
    return Article(
        ticker=ticker,
        source=source,
        url=entry.get("link"),
        published=_parse_published(entry),
        title=entry.get("title"),
        body=_clean_text(entry.get("summary")) or None,
        fetched_at=fetched_at,
    )


# --------------------------------------------------------------------------- #
# Feed client
# --------------------------------------------------------------------------- #
class RssClient:
    """Thin async wrapper that fetches a feed URL and parses it with feedparser.

    Takes an ``httpx.AsyncClient`` so callers (and tests) control transport,
    timeouts and headers.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch_entries(self, url: str) -> list[dict]:
        """Fetch and parse one feed; returns its entries (possibly empty)."""
        resp = await self._client.get(url, follow_redirects=True)
        resp.raise_for_status()
        return feedparser.parse(resp.text).entries


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #
async def _existing_ticker_urls(session, urls: set[str]) -> set[tuple[str, str]]:
    """Return the ``(ticker, url)`` pairs already stored among ``urls``."""
    if not urls:
        return set()
    result = await session.execute(
        select(Article.ticker, Article.url).where(Article.url.in_(urls))
    )
    return set(result.all())


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
async def scrape(client: RssClient, session, companies: list[dict]) -> int:
    """Fetch every feed, match items to companies, insert the new rows.

    Returns the number of articles inserted. Dedup is by ``(ticker, url)``: an
    item returned by several feeds (or already stored) is inserted at most once
    per ticker.
    """
    fetched_at = _now_utc()
    # (ticker, url) -> Article, deduping within this run before we hit the DB.
    candidates: dict[tuple[str, str], Article] = {}

    for _source, url in build_feeds(companies):
        try:
            entries = await client.fetch_entries(url)
        except Exception as exc:  # noqa: BLE001 — one bad feed shouldn't sink the run
            logger.warning("feed failed (%s): %s", url, exc)
            continue
        for entry in entries:
            link = entry.get("link")
            if not link:
                continue
            text = f"{entry.get('title', '')} {entry.get('summary', '')}"
            for ticker in match_companies(text, companies):
                key = (ticker, link)
                if key not in candidates:
                    candidates[key] = entry_to_article(entry, SOURCE, ticker, fetched_at)

    existing = await _existing_ticker_urls(session, {url for _, url in candidates})
    new = [art for key, art in candidates.items() if key not in existing]
    session.add_all(new)
    logger.info("%d new article(s) from %d candidate(s)", len(new), len(candidates))
    return len(new)


async def run() -> int:
    """Fetch all active companies' Google News feeds and store new articles."""
    companies = get_active_companies()
    async with httpx.AsyncClient(
        timeout=60, headers={"User-Agent": _USER_AGENT}
    ) as http:
        client = RssClient(http)
        async with get_db() as session:
            return await scrape(client, session, companies)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="News RSS (Google News) scraper")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--backfill",
        action="store_true",
        help="Fetch current feeds for all active companies",
    )
    mode.add_argument(
        "--incremental",
        action="store_true",
        help="Same fetch as --backfill (RSS exposes no history); dedup drops seen rows",
    )
    parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    total = asyncio.run(run())
    logger.info("Done. %d new article(s) inserted.", total)


if __name__ == "__main__":
    main()
