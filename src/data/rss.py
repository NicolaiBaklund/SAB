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
item is keyword-matched (title + feed text) against *all* companies in
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
are safe to repeat. Results are bounded to the last ``MAX_AGE_DAYS`` days (Google
News otherwise returns years-old articles ranked by relevance).

Requires the database to exist first (``alembic upgrade head``).
"""
from __future__ import annotations

import argparse
import asyncio
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urlparse

import feedparser
import httpx
from sqlalchemy import select

from src.config import get_active_companies
from src.data.db import get_db
from src.data.models import Article
from src.settings import get_settings

logger = logging.getLogger(__name__)

SOURCE = "gnews"
GNEWS_SEARCH_URL = "https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}"
# (hl, gl, ceid) locales to query per company. Norwegian for domestic press,
# English for the salmon trade press (Intrafish et al.).
GNEWS_LOCALES = [
    ("no", "NO", "NO:no"),
    ("en", "US", "US:en"),
]
# Google News ranks by relevance, not date, and will happily return years-old
# articles. We bound freshness two ways: the ``when:Nd`` query operator filters
# at the source, and an authoritative post-filter drops anything older than the
# window (and any undated item). 90 days matches the project's Newsweb time scope.
MAX_AGE_DAYS = get_settings().LOOKBACK_DAYS  # shared ingestion window (settings.LOOKBACK_DAYS)
MAX_ARTICLE_TEXT_CHARS = 6000
ARTICLE_FETCH_CONCURRENCY = 5
_USER_AGENT = "Mozilla/5.0 (compatible; SAB-rss-scraper/1.0; salmon sentiment research)"


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def _now_utc() -> datetime:
    """Timezone-naive UTC, matching the convention used elsewhere in the project."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clean_text(value: str | None) -> str:
    """Strip HTML tags and entities from text, collapsing whitespace.

    Google News summaries are small HTML blobs (anchor + source name); we only
    want plain text for the scorer.
    """
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _entry_text_values(entry: dict) -> list[str]:
    """Raw text-ish values exposed by feedparser for an item."""
    values = [entry.get("summary"), entry.get("description")]
    for content in entry.get("content") or []:
        if isinstance(content, dict):
            values.append(content.get("value"))
    return [value for value in values if isinstance(value, str) and value.strip()]


def _best_entry_body(entry: dict) -> str | None:
    """Best body text available inside the feed item itself."""
    cleaned = {_clean_text(value) for value in _entry_text_values(entry)}
    cleaned.discard("")
    if not cleaned:
        return None
    return max(cleaned, key=len)


class _ArticleTextParser(HTMLParser):
    """Small HTML text extractor for publisher pages.

    It intentionally keeps to metadata plus paragraphs. That avoids storing nav,
    cookie banners and script blobs while still capturing the useful teaser text
    many publishers expose outside paywalls.
    """

    DESCRIPTION_NAMES = {"description", "og:description", "twitter:description"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.descriptions: list[str] = []
        self.paragraphs: list[str] = []
        self._skip_depth = 0
        self._in_paragraph = False
        self._paragraph_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        attr_map = {name.lower(): value or "" for name, value in attrs}
        if tag == "meta":
            name = (attr_map.get("name") or attr_map.get("property") or "").lower()
            if name in self.DESCRIPTION_NAMES:
                self.descriptions.append(attr_map.get("content", ""))
        elif tag == "p" and self._skip_depth == 0:
            self._in_paragraph = True
            self._paragraph_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "p" and self._in_paragraph:
            text = _clean_text(" ".join(self._paragraph_parts))
            if len(text) >= 40:
                self.paragraphs.append(text)
            self._in_paragraph = False
            self._paragraph_parts = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and self._in_paragraph:
            self._paragraph_parts.append(data)


def _limit_text(text: str) -> str:
    text = text[:MAX_ARTICLE_TEXT_CHARS].strip()
    if len(text) == MAX_ARTICLE_TEXT_CHARS and " " in text:
        text = text.rsplit(" ", 1)[0].strip()
    return text


def _extract_article_text(document: str) -> str | None:
    """Extract useful article text from an HTML document."""
    parser = _ArticleTextParser()
    parser.feed(document)

    seen: set[str] = set()
    parts: list[str] = []
    for value in [*parser.descriptions, *parser.paragraphs[:20]]:
        text = _clean_text(value)
        key = text.casefold()
        if text and key not in seen:
            parts.append(text)
            seen.add(key)

    if not parts:
        return None
    return _limit_text("\n\n".join(parts))


def _parse_published(entry: dict) -> datetime | None:
    """Convert a feedparser entry's parsed time (UTC ``struct_time``) to naive UTC."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6])


def _within_window(
    published: datetime | None, now: datetime, *, max_age_days: int = MAX_AGE_DAYS
) -> bool:
    """Whether ``published`` is recent enough to keep.

    Undated items (``None``) are dropped: we cannot prove they are fresh, and
    Google News always dates its items.
    """
    if published is None:
        return False
    return published >= now - timedelta(days=max_age_days)


def build_feeds(companies: list[dict]) -> list[tuple[str, str]]:
    """Build the ``(source, url)`` feeds to fetch: one Google News query per
    company per locale.

    The query term is the company's first keyword (its common name), wrapped in
    quotes for a phrase search to cut obvious noise (e.g. the *Grieg* oilfield vs
    *Grieg Seafood*), plus a ``when:Nd`` operator so Google bounds results to the
    freshness window at the source (the post-filter in ``scrape`` is authoritative).
    """
    feeds: list[tuple[str, str]] = []
    for company in companies:
        keywords = company.get("keywords") or []
        if not keywords:
            logger.warning("%s has no keywords; skipping", company.get("ticker"))
            continue
        query = quote(f'"{keywords[0]}" when:{MAX_AGE_DAYS}d')
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
    entry: dict, url: str, source: str, ticker: str, fetched_at: datetime
) -> Article:
    """Map a feedparser entry to an ``Article`` row for one ticker.

    ``url`` must be a non-empty string; the caller is responsible for
    filtering entries without a link before calling this function.
    """
    return Article(
        ticker=ticker,
        source=source,
        url=url,
        published=_parse_published(entry),
        title=_clean_text(entry.get("title")) or None,
        body=_best_entry_body(entry),
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

    async def fetch_article_text(self, url: str) -> str | None:
        """Fetch an article page and extract useful metadata/paragraph text."""
        resp = await self._client.get(url, follow_redirects=True)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "").lower()
        if content_type and "html" not in content_type:
            return None

        final_host = urlparse(str(resp.url)).netloc.lower()
        if final_host in {"consent.google.com", "news.google.com"}:
            return None

        return _extract_article_text(resp.text)


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


def _prefer_longer_body(current: str | None, fetched: str | None) -> str | None:
    """Keep whichever body gives the scorer more text."""
    if not fetched:
        return current
    if not current or len(fetched) > len(current):
        return fetched
    return current


async def _enrich_article_bodies(client: RssClient, articles: list[Article]) -> None:
    """Best-effort article-page enrichment for newly inserted rows."""
    urls = sorted({article.url for article in articles})
    if not urls:
        return

    semaphore = asyncio.Semaphore(ARTICLE_FETCH_CONCURRENCY)

    async def _fetch(url: str) -> tuple[str, str | None]:
        async with semaphore:
            try:
                return url, await client.fetch_article_text(url)
            except Exception as exc:  # noqa: BLE001 - enrichment is optional
                logger.debug("article enrichment failed (%s): %s", url, exc)
                return url, None

    enriched = dict(await asyncio.gather(*(_fetch(url) for url in urls)))
    for article in articles:
        article.body = _prefer_longer_body(article.body, enriched.get(article.url))


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
async def scrape(
    client: RssClient, session, companies: list[dict], *, now: datetime | None = None
) -> int:
    """Fetch every feed, match items to companies, insert the new rows.

    Returns the number of articles inserted. Items older than ``MAX_AGE_DAYS``
    (or undated) are dropped — Google News ranks by relevance and returns
    years-old hits. Dedup is by ``(ticker, url)``: an item returned by several
    feeds (or already stored) is inserted at most once per ticker. ``now`` is
    injectable for tests.
    """
    now = now or _now_utc()
    fetched_at = now
    # (ticker, url) -> Article, deduping within this run before we hit the DB.
    candidates: dict[tuple[str, str], Article] = {}

    feeds = build_feeds(companies)

    async def _fetch_one(url: str) -> list[dict]:
        try:
            return await client.fetch_entries(url)
        except Exception as exc:  # noqa: BLE001 — one bad feed shouldn't sink the run
            logger.warning("feed failed (%s): %s", url, exc)
            return []

    results = await asyncio.gather(*(_fetch_one(url) for _source, url in feeds))

    for (source, _url), entries in zip(feeds, results):
        for entry in entries:
            link = entry.get("link")
            if not link:
                continue
            if not _within_window(_parse_published(entry), now):
                continue
            text = " ".join(
                [str(entry.get("title") or ""), *_entry_text_values(entry)]
            )
            for ticker in match_companies(text, companies):
                key = (ticker, link)
                if key not in candidates:
                    candidates[key] = entry_to_article(entry, link, source, ticker, fetched_at)

    existing = await _existing_ticker_urls(session, {url for _, url in candidates})
    new = [art for key, art in candidates.items() if key not in existing]
    await _enrich_article_bodies(client, new)
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
