"""Tests for the daily price fetcher (Phase 3.1).

Network is stubbed with ``httpx.MockTransport`` (no real HTTP). DB tests use
the shared in-memory ``session`` fixture from ``conftest.py``.
"""
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from src.data.models import Price
from src.data.prices import (
    BACKFILL_DAYS,
    PriceFetchError,
    YahooClient,
    _bar_date,
    _incremental_window,
    _last_date,
    chart_to_rows,
    fetch_company,
    price_symbol,
    upsert_prices,
)


# --------------------------------------------------------------------------- #
# Test harness: a YahooClient backed by a scripted MockTransport
# --------------------------------------------------------------------------- #
def make_client(handler):
    """Build a YahooClient whose HTTP calls are answered in-process.

    ``handler(request) -> httpx.Response`` answers every request.
    """
    return YahooClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def _ts(day: date, hour: int = 7) -> int:
    """Epoch seconds for an Oslo market-open bar (09:00 CEST = 07:00 UTC)."""
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).timestamp())


def chart_result(timestamps, closes, *, adj=None, volumes=None, currency="NOK",
                 gmtoffset=7200, opens=None, highs=None, lows=None):
    """Build a chart ``result`` dict shaped like the v8 API's."""
    quote = {
        "open": opens if opens is not None else closes,
        "high": highs if highs is not None else closes,
        "low": lows if lows is not None else closes,
        "close": closes,
        "volume": volumes if volumes is not None else [1000] * len(closes),
    }
    result = {
        "meta": {"currency": currency, "gmtoffset": gmtoffset},
        "timestamp": timestamps,
        "indicators": {"quote": [quote]},
    }
    if adj is not None:
        result["indicators"]["adjclose"] = [{"adjclose": adj}]
    return result


def chart_payload(result=None, error=None):
    return {"chart": {"result": [result] if result else None, "error": error}}


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_price_symbol_defaults_to_oslo_suffix():
    assert price_symbol({"ticker": "MOWI"}) == "MOWI.OL"


def test_price_symbol_override_wins():
    assert price_symbol({"ticker": "BAKKA", "price_symbol": "BAKKA.CO"}) == "BAKKA.CO"


def test_bar_date_applies_exchange_offset():
    # 23:30 UTC on Jan 1 is already Jan 2 at the exchange (offset +1h, CET)
    ts = int(datetime(2026, 1, 1, 23, 30, tzinfo=timezone.utc).timestamp())
    assert _bar_date(ts, 3600) == date(2026, 1, 2)
    assert _bar_date(ts, 0) == date(2026, 1, 1)


def test_chart_to_rows_maps_fields():
    day = date(2026, 6, 1)
    fetched = datetime(2026, 6, 2, 12, 0)
    result = chart_result(
        [_ts(day)], closes=[201.5], opens=[200.0], highs=[203.0], lows=[199.0],
        volumes=[123456], adj=[198.7],
    )
    rows = chart_to_rows(result, "MOWI", fetched)
    assert rows == [{
        "ticker": "MOWI", "date": day,
        "open": 200.0, "high": 203.0, "low": 199.0, "close": 201.5,
        "adj_close": 198.7, "volume": 123456, "currency": "NOK",
        "source": "yahoo", "fetched_at": fetched,
    }]


def test_chart_to_rows_skips_null_close():
    days = [date(2026, 6, 1), date(2026, 6, 2)]
    result = chart_result([_ts(d) for d in days], closes=[None, 100.0])
    rows = chart_to_rows(result, "MOWI", datetime(2026, 6, 3))
    assert [r["date"] for r in rows] == [days[1]]


def test_chart_to_rows_without_adjclose_block():
    result = chart_result([_ts(date(2026, 6, 1))], closes=[100.0])
    (row,) = chart_to_rows(result, "MOWI", datetime(2026, 6, 2))
    assert row["adj_close"] is None


def test_chart_to_rows_collapses_duplicate_day_to_last():
    # The live intraday bar can repeat the last daily bar's trading day with a
    # later timestamp — the last occurrence must win.
    day = date(2026, 6, 1)
    result = chart_result([_ts(day, 7), _ts(day, 14)], closes=[100.0, 101.0])
    rows = chart_to_rows(result, "MOWI", datetime(2026, 6, 1))
    assert len(rows) == 1
    assert rows[0]["close"] == 101.0


def test_incremental_window_first_run_is_backfill():
    now = datetime(2026, 6, 11)
    start, end = _incremental_window(None, now, backfill_days=BACKFILL_DAYS)
    assert start == now - timedelta(days=BACKFILL_DAYS)
    assert end == now


def test_incremental_window_overlaps_last_stored_day():
    now = datetime(2026, 6, 11)
    start, end = _incremental_window(date(2026, 6, 9), now, overlap_days=5)
    assert start == datetime(2026, 6, 4)
    assert end == now


# --------------------------------------------------------------------------- #
# Client: error shapes of the chart API
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_fetch_daily_bars_passes_window_params():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        seen["path"] = request.url.path
        return httpx.Response(
            200, json=chart_payload(chart_result([_ts(date(2026, 6, 1))], [100.0]))
        )

    client = make_client(handler)
    result = await client.fetch_daily_bars(
        "MOWI.OL", datetime(2026, 3, 1), datetime(2026, 6, 1)
    )
    assert seen["path"].endswith("/MOWI.OL")
    assert seen["interval"] == "1d"
    assert int(seen["period1"]) < int(seen["period2"])
    assert result["timestamp"]


@pytest.mark.asyncio
async def test_fetch_daily_bars_unknown_symbol_raises():
    # Yahoo answers unknown/delisted symbols with 404 + a structured chart.error
    def handler(request):
        return httpx.Response(404, json=chart_payload(
            error={"code": "Not Found", "description": "No data found, symbol may be delisted"}
        ))

    client = make_client(handler)
    with pytest.raises(PriceFetchError, match="delisted"):
        await client.fetch_daily_bars("NOPE.OL", datetime(2026, 1, 1), datetime(2026, 6, 1))


@pytest.mark.asyncio
async def test_fetch_daily_bars_non_json_block_raises_http_error():
    def handler(request):
        return httpx.Response(403, text="<html>blocked</html>")

    client = make_client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await client.fetch_daily_bars("MOWI.OL", datetime(2026, 1, 1), datetime(2026, 6, 1))


@pytest.mark.asyncio
async def test_fetch_daily_bars_empty_result_raises():
    def handler(request):
        return httpx.Response(200, json={"chart": {"result": [], "error": None}})

    client = make_client(handler)
    with pytest.raises(PriceFetchError, match="no result"):
        await client.fetch_daily_bars("MOWI.OL", datetime(2026, 1, 1), datetime(2026, 6, 1))


# --------------------------------------------------------------------------- #
# DB-backed orchestration: upsert semantics
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_fetch_company_inserts_bars(session):
    days = [date(2026, 6, 1), date(2026, 6, 2)]

    def handler(request):
        return httpx.Response(200, json=chart_payload(
            chart_result([_ts(d) for d in days], closes=[100.0, 102.0], adj=[99.0, 101.0])
        ))

    count = await fetch_company(
        make_client(handler), session, {"ticker": "MOWI"},
        datetime(2026, 5, 28), datetime(2026, 6, 3),
    )
    await session.flush()

    assert count == 2
    stored = (await session.execute(select(Price).order_by(Price.date))).scalars().all()
    assert [(p.ticker, p.date, p.close, p.adj_close) for p in stored] == [
        ("MOWI", days[0], 100.0, 99.0),
        ("MOWI", days[1], 102.0, 101.0),
    ]


@pytest.mark.asyncio
async def test_refetch_overwrites_revised_bar(session):
    # First fetch stores a partial intraday bar; the re-fetch must overwrite it
    # on (ticker, date) instead of duplicating or keeping the stale close.
    day = date(2026, 6, 1)
    closes = {"value": 100.0}

    def handler(request):
        return httpx.Response(200, json=chart_payload(
            chart_result([_ts(day)], closes=[closes["value"]])
        ))

    client = make_client(handler)
    window = (datetime(2026, 5, 28), datetime(2026, 6, 2))
    await fetch_company(client, session, {"ticker": "MOWI"}, *window)
    closes["value"] = 105.5  # the bar got revised at the source
    await fetch_company(client, session, {"ticker": "MOWI"}, *window)
    await session.flush()

    stored = (await session.execute(select(Price))).scalars().all()
    assert len(stored) == 1
    assert stored[0].close == 105.5


@pytest.mark.asyncio
async def test_upsert_same_day_different_tickers_coexist(session):
    day = date(2026, 6, 1)
    fetched = datetime(2026, 6, 2)
    base = {"date": day, "open": None, "high": None, "low": None, "adj_close": None,
            "volume": None, "currency": "NOK", "source": "yahoo", "fetched_at": fetched}
    await upsert_prices(session, [
        {**base, "ticker": "MOWI", "close": 100.0},
        {**base, "ticker": "SALM", "close": 200.0},
    ])
    await session.flush()
    stored = (await session.execute(select(Price.ticker, Price.close))).all()
    assert set(stored) == {("MOWI", 100.0), ("SALM", 200.0)}


@pytest.mark.asyncio
async def test_last_date_returns_max(session):
    fetched = datetime(2026, 6, 2)
    await upsert_prices(session, [
        {"ticker": "MOWI", "date": date(2026, 5, 30), "open": None, "high": None,
         "low": None, "close": 1.0, "adj_close": None, "volume": None,
         "currency": None, "source": "yahoo", "fetched_at": fetched},
        {"ticker": "MOWI", "date": date(2026, 6, 1), "open": None, "high": None,
         "low": None, "close": 2.0, "adj_close": None, "volume": None,
         "currency": None, "source": "yahoo", "fetched_at": fetched},
    ])
    await session.flush()
    assert await _last_date(session, "MOWI") == date(2026, 6, 1)
    assert await _last_date(session, "GSF") is None
