"""Tests for the News RSS (Google News) scraper.

Network is stubbed with ``httpx.MockTransport`` (no real HTTP); feeds are parsed
from static RSS strings by the real ``feedparser``. DB tests use the shared
in-memory ``session`` fixture from ``conftest.py``.
"""
from datetime import datetime

import feedparser
import httpx
import pytest
from sqlalchemy import select

from src.data.models import Article
from src.data.rss import (
    RssClient,
    _clean_text,
    _parse_published,
    build_feeds,
    entry_to_article,
    match_companies,
    scrape,
)

COMPANIES = [
    {"ticker": "MOWI", "name": "Mowi ASA", "keywords": ["Mowi", "MOWI"], "active": True},
    {"ticker": "SALM", "name": "SalMar ASA", "keywords": ["SalMar", "SALM"], "active": True},
]


# --------------------------------------------------------------------------- #
# RSS fixtures / harness
# --------------------------------------------------------------------------- #
def rss(*items: dict) -> str:
    """Build an RSS 2.0 document from ``{title, link, summary, pubDate}`` items."""
    body = "".join(
        f"<item><title>{i['title']}</title>"
        f"<link>{i['link']}</link>"
        f"<description>{i.get('summary', '')}</description>"
        f"<pubDate>{i.get('pubDate', 'Tue, 09 Jun 2026 09:00:00 GMT')}</pubDate></item>"
        for i in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>'


def make_client(feed_xml: str, *, status: int = 200) -> RssClient:
    """RssClient whose every HTTP GET returns the same feed (or an error status)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(
            200, text=feed_xml, headers={"content-type": "application/rss+xml"}
        )

    return RssClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def entry(title="t", link="http://x/1", summary="", pubDate=None):
    doc = rss({"title": title, "link": link, "summary": summary,
               "pubDate": pubDate or "Tue, 09 Jun 2026 09:00:00 GMT"})
    return feedparser.parse(doc).entries[0]


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_clean_text_strips_html_and_entities():
    assert _clean_text('<a href="x">Mowi &amp; SalMar</a>  up') == "Mowi & SalMar up"


def test_clean_text_none_is_empty():
    assert _clean_text(None) == ""


def test_parse_published_from_pubdate():
    assert _parse_published(entry(pubDate="Tue, 09 Jun 2026 09:00:00 GMT")) == datetime(
        2026, 6, 9, 9, 0, 0
    )


def test_parse_published_missing_is_none():
    assert _parse_published({}) is None


def test_match_companies_multiple_hits():
    assert match_companies("Mowi and SalMar both rose", COMPANIES) == ["MOWI", "SALM"]


def test_match_companies_no_hit():
    assert match_companies("Grieg Seafood news", COMPANIES) == []


def test_match_companies_case_insensitive():
    assert match_companies("mowi gains", COMPANIES) == ["MOWI"]


def test_match_companies_respects_word_boundary():
    # "Mowi"/"MOWI" must not match inside "Mowinckel".
    assert match_companies("Mowinckel comments on market", COMPANIES) == []


def test_entry_to_article_maps_fields():
    art = entry_to_article(
        entry(title="Mowi Q1", link="http://x/9", summary="<b>beat</b>"),
        "gnews", "MOWI", datetime(2026, 6, 9, 12, 0, 0),
    )
    assert art.ticker == "MOWI"
    assert art.source == "gnews"
    assert art.url == "http://x/9"
    assert art.title == "Mowi Q1"
    assert art.body == "beat"
    assert art.published == datetime(2026, 6, 9, 9, 0, 0)
    assert art.fetched_at == datetime(2026, 6, 9, 12, 0, 0)


def test_build_feeds_one_query_per_company_per_locale():
    feeds = build_feeds([COMPANIES[0]])
    urls = [u for _, u in feeds]
    assert len(urls) == 2  # no + en
    assert all("q=%22Mowi%22" in u for u in urls)
    assert any("hl=no" in u for u in urls)
    assert any("hl=en" in u for u in urls)


def test_build_feeds_skips_company_without_keywords():
    assert build_feeds([{"ticker": "X", "keywords": []}]) == []


# --------------------------------------------------------------------------- #
# DB-backed orchestration
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_scrape_one_row_per_matched_ticker(session):
    # One article mentions both companies -> two rows, same url (composite key).
    client = make_client(rss({"title": "Mowi and SalMar climb", "link": "http://x/1"}))
    inserted = await scrape(client, session, COMPANIES)
    assert inserted == 2
    rows = (await session.execute(select(Article).order_by(Article.ticker))).scalars().all()
    assert {(r.ticker, r.url) for r in rows} == {("MOWI", "http://x/1"), ("SALM", "http://x/1")}
    assert {r.source for r in rows} == {"gnews"}


@pytest.mark.asyncio
async def test_scrape_drops_unmatched_items(session):
    client = make_client(rss({"title": "Grieg Seafood unrelated", "link": "http://x/2"}))
    assert await scrape(client, session, COMPANIES) == 0
    assert (await session.execute(select(Article))).scalars().all() == []


@pytest.mark.asyncio
async def test_scrape_dedups_existing_ticker_url(session):
    # MOWI+url already stored; the same item also matches SALM -> only SALM is new.
    session.add(
        Article(ticker="MOWI", source="gnews", url="http://x/1",
                published=datetime(2026, 6, 1), fetched_at=datetime(2026, 6, 1))
    )
    await session.flush()

    client = make_client(rss({"title": "Mowi and SalMar climb", "link": "http://x/1"}))
    inserted = await scrape(client, session, COMPANIES)
    assert inserted == 1
    rows = (await session.execute(select(Article))).scalars().all()
    assert {(r.ticker, r.url) for r in rows} == {("MOWI", "http://x/1"), ("SALM", "http://x/1")}


@pytest.mark.asyncio
async def test_scrape_continues_when_a_feed_fails(session):
    # Every feed errors -> no crash, nothing inserted.
    client = make_client("", status=500)
    assert await scrape(client, session, COMPANIES) == 0
    assert (await session.execute(select(Article))).scalars().all() == []
