"""Tests for the Newsweb scraper.

Network is stubbed with ``httpx.MockTransport`` (no real HTTP), and the PDF
converter is stubbed so ``markitdown`` never runs. DB tests use the shared
in-memory ``session`` fixture from ``conftest.py``.
"""
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from src.data.models import Article
from src.data.newsweb import (
    NewswebClient,
    _dedup_messages,
    _incremental_window,
    _last_published,
    _message_url,
    _parse_published,
    build_body,
    fetch_company,
    message_to_article,
)


# --------------------------------------------------------------------------- #
# Test harness: a NewswebClient backed by a scripted MockTransport
# --------------------------------------------------------------------------- #
def make_client(list_fn, details=None, attachment=b"%PDF-1.4 fake"):
    """Build a NewswebClient whose HTTP calls are answered in-process.

    ``list_fn(params) -> {"messages": [...], "overflow": bool}`` answers /list.
    ``details`` maps messageId -> message-detail dict for /message.
    """
    details = details or {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        if path.endswith("/list"):
            return httpx.Response(200, json={"data": list_fn(params)})
        if path.endswith("/message"):
            return httpx.Response(
                200, json={"data": {"message": details[int(params["messageId"])]}}
            )
        if path.endswith("/attachment"):
            return httpx.Response(200, content=attachment)
        return httpx.Response(404)

    return NewswebClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def detail(message_id, *, ticker="MOWI", title="t", body="b", published=None, attachments=None):
    return {
        "messageId": message_id,
        "issuerSign": ticker,
        "title": title,
        "body": body,
        "publishedTime": published or "2026-05-01T08:00:00.000Z",
        "attachments": attachments or [],
    }


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_parse_published_to_naive_utc():
    dt = _parse_published("2026-06-05T15:28:59.263Z")
    assert dt == datetime(2026, 6, 5, 15, 28, 59, 263000)
    assert dt.tzinfo is None


def test_parse_published_none():
    assert _parse_published(None) is None


def test_dedup_messages_keeps_first():
    msgs = [{"messageId": 1}, {"messageId": 2}, {"messageId": 1}]
    assert [m["messageId"] for m in _dedup_messages(msgs)] == [1, 2]


def test_message_to_article_maps_fields():
    fetched = datetime(2026, 6, 5, 12, 0, 0)
    art = message_to_article(detail(123, ticker="SALM", title="Q1"), [], fetched)
    assert art.ticker == "SALM"
    assert art.source == "newsweb"
    assert art.url == _message_url(123)
    assert art.title == "Q1"
    assert art.published == datetime(2026, 5, 1, 8, 0, 0)
    assert art.fetched_at == fetched


def test_build_body_folds_attachments():
    body = build_body(
        {"body": "Main text."},
        [("report.pdf", "Converted PDF body."), ("empty.pdf", "")],
    )
    assert "Main text." in body
    assert "## Attachment: report.pdf" in body
    assert "Converted PDF body." in body
    assert "empty.pdf" not in body  # blank conversions are dropped


def test_build_body_empty_returns_none():
    assert build_body({"body": ""}, []) is None


def test_incremental_window_first_run_is_backfill():
    now = datetime(2026, 6, 5)
    start, end = _incremental_window(None, now, backfill_days=90)
    assert start == now - timedelta(days=90)
    assert end == now


def test_incremental_window_uses_last_published_with_overlap():
    now = datetime(2026, 6, 5)
    last = datetime(2026, 6, 1)
    start, end = _incremental_window(last, now, overlap_days=2)
    assert start == datetime(2026, 5, 30)
    assert end == now


# --------------------------------------------------------------------------- #
# Client: pagination by date bisection
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_list_messages_no_overflow_no_split():
    def list_fn(params):
        return {"messages": [{"messageId": 1}, {"messageId": 2}], "overflow": False}

    client = make_client(list_fn)
    msgs = await client.list_messages(5063, datetime(2026, 1, 1), datetime(2026, 4, 1))
    assert {m["messageId"] for m in msgs} == {1, 2}


@pytest.mark.asyncio
async def test_list_messages_bisects_on_overflow():
    # Overflow keeps recursing until each window is a single calendar day; only
    # those single-day leaves return a message keyed by fromDate, so the id set
    # shows the range was split all the way down.
    def list_fn(params):
        fd = datetime.strptime(params["fromDate"], "%Y-%m-%d")
        td = datetime.strptime(params["toDate"], "%Y-%m-%d")
        if fd != td:
            return {"messages": [], "overflow": True}
        mid = int(params["fromDate"].replace("-", ""))
        return {"messages": [{"messageId": mid}], "overflow": True}

    client = make_client(list_fn)
    msgs = await client.list_messages(5063, datetime(2026, 1, 1), datetime(2026, 1, 4))
    ids = {m["messageId"] for m in msgs}
    assert ids == {20260101, 20260102, 20260103, 20260104}


@pytest.mark.asyncio
async def test_list_messages_splits_two_date_overflow():
    # Regression: a window whose datetime delta is exactly one day is still two
    # inclusive dates (fromDate=01-01, toDate=01-02). Overflow there must split
    # into two single-day requests, not return the capped response and drop day 2.
    def list_fn(params):
        if params["fromDate"] != params["toDate"]:
            return {"messages": [], "overflow": True}
        mid = int(params["fromDate"].replace("-", ""))
        return {"messages": [{"messageId": mid}], "overflow": True}

    client = make_client(list_fn)
    msgs = await client.list_messages(5063, datetime(2026, 1, 1), datetime(2026, 1, 2))
    assert {m["messageId"] for m in msgs} == {20260101, 20260102}


# --------------------------------------------------------------------------- #
# DB-backed orchestration
# --------------------------------------------------------------------------- #
def _now():
    return datetime(2026, 6, 5)


@pytest.mark.asyncio
async def test_last_published_returns_max(session):
    session.add_all(
        [
            Article(ticker="MOWI", source="newsweb", url=_message_url(1),
                    published=datetime(2026, 5, 1), fetched_at=_now()),
            Article(ticker="MOWI", source="newsweb", url=_message_url(2),
                    published=datetime(2026, 5, 9), fetched_at=_now()),
            Article(ticker="SALM", source="newsweb", url=_message_url(3),
                    published=datetime(2026, 5, 20), fetched_at=_now()),
        ]
    )
    await session.flush()
    assert await _last_published(session, "MOWI") == datetime(2026, 5, 9)
    assert await _last_published(session, "GSF") is None


@pytest.mark.asyncio
async def test_fetch_company_dedups_by_url(session):
    # messageId 111 already stored -> only 222 should be inserted, and 111's
    # detail must never be requested.
    session.add(
        Article(ticker="MOWI", source="newsweb", url=_message_url(111),
                published=datetime(2026, 5, 1), fetched_at=_now())
    )
    await session.flush()

    def list_fn(params):
        return {"messages": [{"messageId": 111}, {"messageId": 222}], "overflow": False}

    requested = []
    details = {111: detail(111), 222: detail(222)}
    client = make_client(list_fn, details)
    # wrap get_message to record what gets fetched
    orig = client.get_message

    async def spy(mid):
        requested.append(mid)
        return await orig(mid)

    client.get_message = spy

    company = {"ticker": "MOWI", "newsweb_issuer_id": 5063}
    inserted = await fetch_company(
        client, session, company, datetime(2026, 4, 1), _now(),
        converter=lambda data, name: "",
    )
    assert inserted == 1
    assert requested == [222]  # existing URL skipped before any detail fetch

    rows = (await session.execute(select(Article).order_by(Article.url))).scalars().all()
    assert {r.url for r in rows} == {_message_url(111), _message_url(222)}


@pytest.mark.asyncio
async def test_fetch_company_folds_attachment_text(session):
    def list_fn(params):
        return {"messages": [{"messageId": 1}], "overflow": False}

    details = {1: detail(1, attachments=[{"id": 9, "name": "q1.pdf"}])}
    client = make_client(list_fn, details)

    company = {"ticker": "MOWI", "newsweb_issuer_id": 5063}
    inserted = await fetch_company(
        client, session, company, datetime(2026, 4, 1), _now(),
        converter=lambda data, name: "EXTRACTED TEXT",
    )
    assert inserted == 1
    art = (await session.execute(select(Article))).scalars().one()
    assert "## Attachment: q1.pdf" in art.body
    assert "EXTRACTED TEXT" in art.body


@pytest.mark.asyncio
async def test_fetch_company_skips_without_issuer_id(session):
    client = make_client(lambda params: {"messages": [], "overflow": False})
    company = {"ticker": "XXXX"}  # no newsweb_issuer_id
    inserted = await fetch_company(client, session, company, datetime(2026, 4, 1), _now())
    assert inserted == 0


@pytest.mark.asyncio
async def test_fetch_company_continues_when_attachment_fails(session):
    # A failing attachment download must not sink the article.
    def list_fn(params):
        return {"messages": [{"messageId": 1}], "overflow": False}

    details = {1: detail(1, body="Body text", attachments=[{"id": 9, "name": "bad.pdf"}])}

    def boom(data, name):
        raise RuntimeError("conversion failed")

    client = make_client(list_fn, details)
    company = {"ticker": "MOWI", "newsweb_issuer_id": 5063}
    inserted = await fetch_company(
        client, session, company, datetime(2026, 4, 1), _now(), converter=boom
    )
    assert inserted == 1
    art = (await session.execute(select(Article))).scalars().one()
    assert art.body == "Body text"  # attachment skipped, body intact
