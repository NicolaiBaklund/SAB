"""Daily price (OHLCV) fetcher — Phase 3.1.

Fetches daily price bars for the active companies in ``companies.json`` and
upserts them into the ``prices`` table. The data feeds technical analysis
(Phase 3.2+): full OHLCV per trading day plus the dividend/split-adjusted close
(``adj_close``) that returns and indicators should be computed from.

## Source: Yahoo Finance v8 chart API

Like the Newsweb scraper, this calls a public JSON API directly with ``httpx``
— no SDK, no browser:

    GET https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>
        ?period1=<epoch>&period2=<epoch>&interval=1d&includeAdjustedClose=true
        -> {"chart": {"result": [{"meta":   {currency, gmtoffset, ...},
                                  "timestamp": [<epoch>, ...],
                                  "indicators": {
                                      "quote":    [{open, high, low, close, volume}],
                                      "adjclose": [{adjclose}]}}],
                      "error": null | {code, description}}}

Oslo Børs symbols carry the ``.OL`` suffix (``MOWI.OL``). The symbol is derived
from the ticker dynamically (``<ticker>.OL``), so adding a company to
``companies.json`` automatically fetches its prices; a ``price_symbol`` field on
the company overrides the derivation for differently-symbolled listings.
Source alternatives considered (Euronext, Stooq, keyed providers) are compared
in ``docs/data-sources.md``.

The endpoint is unofficial and rejects obviously-scripted user agents, so the
client sends a browser-like ``User-Agent`` (same approach the ``yfinance``
library relies on). Unknown/delisted symbols come back as a structured
``chart.error`` — surfaced per company without sinking the run.

## Upsert, not insert

Daily bars are *revised* by the source: a bar fetched while Oslo Børs is open is
partial (``close`` = last trade so far), and ``adj_close`` changes retroactively
for the whole history whenever a dividend or split lands. Rows are therefore
upserted on ``(ticker, date)`` — re-running a window overwrites stale bars.
Incremental runs only re-fetch the recent tail, so after a dividend the *older*
stored ``adj_close`` values go stale until the next ``--backfill`` refresh (see
``docs/data-sources.md`` for the recommended monthly refresh).

## Usage

    python -m src.data.prices --backfill        # last PRICE_BACKFILL_DAYS days
    python -m src.data.prices --incremental     # recent tail only (daily job)

Backfill defaults to ``settings.PRICE_BACKFILL_DAYS`` (2 years) rather than the
90-day news window: long technical indicators (200-day SMA) need ~290 calendar
days before their first value. Requires the database to exist first
(``alembic upgrade head``).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.config import get_active_companies
from src.data.db import get_db
from src.data.models import Price
from src.settings import get_settings

logger = logging.getLogger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
SOURCE = "yahoo"
# Oslo Børs listings on Yahoo Finance: <ticker>.OL
DEFAULT_SYMBOL_SUFFIX = ".OL"
BACKFILL_DAYS = get_settings().PRICE_BACKFILL_DAYS
# Incremental runs re-fetch a small tail so partial intraday bars and late
# corrections get overwritten by the (ticker, date) upsert.
INCREMENTAL_OVERLAP_DAYS = 5
# Columns the upsert overwrites on (ticker, date) conflict.
_UPSERT_COLUMNS = (
    "open", "high", "low", "close", "adj_close",
    "volume", "currency", "source", "fetched_at",
)
# Keep well under SQLite's per-statement bind-parameter limit (999 on older
# builds): 250 rows x 12 columns = 3000 parameters needs a modern SQLite, which
# Python 3.10+ bundles; chunking also keeps statements reasonably sized.
_UPSERT_CHUNK_ROWS = 250
# Yahoo serves 429/403 to obviously-scripted user agents; a browser-like UA is
# the documented workaround (what yfinance does).
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class PriceFetchError(Exception):
    """The source could not return usable bars for a symbol."""


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def _now_utc() -> datetime:
    """Timezone-naive UTC, matching the convention used elsewhere in the project."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _epoch(dt: datetime) -> int:
    """Naive-UTC datetime -> unix epoch seconds."""
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def price_symbol(company: dict) -> str:
    """Source symbol for a company: explicit ``price_symbol`` or ``<ticker>.OL``."""
    return company.get("price_symbol") or f"{company['ticker']}{DEFAULT_SYMBOL_SUFFIX}"


def _bar_date(ts: int, gmtoffset: int) -> date:
    """Trading day of a bar.

    Chart timestamps are the bar's open in epoch seconds; adding the exchange's
    ``gmtoffset`` (from meta) before taking the UTC date yields the
    exchange-local calendar day regardless of timezone.
    """
    return datetime.fromtimestamp(ts + gmtoffset, tz=timezone.utc).date()


def _at(values: list | None, index: int):
    """Safe positional lookup: the source's parallel arrays can be short or null."""
    if values is None or index >= len(values):
        return None
    return values[index]


def chart_to_rows(result: dict, ticker: str, fetched_at: datetime) -> list[dict]:
    """Map one chart ``result`` to ``prices`` row dicts (for the upsert).

    Bars without a ``close`` are dropped (halted days / unsettled bars come back
    as nulls). Duplicate timestamps for the same trading day (the live intraday
    bar can repeat the last daily bar) collapse to the last occurrence.
    """
    meta = result.get("meta") or {}
    gmtoffset = meta.get("gmtoffset") or 0
    currency = meta.get("currency")
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote_block = (indicators.get("quote") or [{}])[0] or {}
    adjclose = ((indicators.get("adjclose") or [{}])[0] or {}).get("adjclose")

    rows: dict[date, dict] = {}
    for index, ts in enumerate(timestamps):
        close = _at(quote_block.get("close"), index)
        if close is None:
            continue
        volume = _at(quote_block.get("volume"), index)
        day = _bar_date(ts, gmtoffset)
        rows[day] = {
            "ticker": ticker,
            "date": day,
            "open": _at(quote_block.get("open"), index),
            "high": _at(quote_block.get("high"), index),
            "low": _at(quote_block.get("low"), index),
            "close": close,
            "adj_close": _at(adjclose, index),
            "volume": int(volume) if volume is not None else None,
            "currency": currency,
            "source": SOURCE,
            "fetched_at": fetched_at,
        }
    return [rows[day] for day in sorted(rows)]


def _incremental_window(
    last_date: date | None,
    now: datetime,
    *,
    overlap_days: int = INCREMENTAL_OVERLAP_DAYS,
    backfill_days: int = BACKFILL_DAYS,
) -> tuple[datetime, datetime]:
    """Date window for an incremental run.

    Start a few days before the last stored bar (the upsert overwrites the
    re-fetched tail, catching partial bars and corrections). With nothing
    stored yet, fall back to a full backfill window.
    """
    if last_date is None:
        return now - timedelta(days=backfill_days), now
    start = datetime(last_date.year, last_date.month, last_date.day) - timedelta(
        days=overlap_days
    )
    return start, now


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #
class YahooClient:
    """Thin async wrapper over the Yahoo Finance v8 chart API.

    Takes an ``httpx.AsyncClient`` so callers (and tests) control transport,
    timeouts and headers.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch_daily_bars(
        self, symbol: str, start: datetime, end: datetime
    ) -> dict:
        """Fetch one symbol's daily chart for ``[start, end]``; returns the
        chart ``result`` dict.

        Raises ``PriceFetchError`` for structured source errors (unknown or
        delisted symbol — Yahoo sends those as ``chart.error``, typically with
        a 404 status) and lets transport/HTTP errors propagate as httpx
        exceptions.
        """
        params = {
            "period1": _epoch(start),
            "period2": _epoch(end),
            "interval": "1d",
            "includeAdjustedClose": "true",
        }
        resp = await self._client.get(
            CHART_URL.format(symbol=quote(symbol)), params=params
        )
        try:
            chart = resp.json().get("chart") or {}
        except ValueError:  # non-JSON body (blocked / HTML error page)
            resp.raise_for_status()
            raise PriceFetchError(f"{symbol}: non-JSON response from chart API")

        error = chart.get("error")
        if error:
            raise PriceFetchError(
                f"{symbol}: {error.get('description') or error.get('code')}"
            )
        resp.raise_for_status()

        results = chart.get("result") or []
        if not results:
            raise PriceFetchError(f"{symbol}: chart response carried no result")
        return results[0]


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #
async def _last_date(session, ticker: str) -> date | None:
    """Most recent trading day stored for a ticker."""
    result = await session.execute(
        select(func.max(Price.date)).where(Price.ticker == ticker)
    )
    return result.scalar()


async def upsert_prices(session, rows: list[dict]) -> int:
    """Insert-or-overwrite bars on ``(ticker, date)``; returns rows processed.

    SQLite-dialect upsert — fine here because the project is SQLite by design
    (see roadmap tech stack). Chunked to stay under bind-parameter limits.
    """
    for offset in range(0, len(rows), _UPSERT_CHUNK_ROWS):
        chunk = rows[offset : offset + _UPSERT_CHUNK_ROWS]
        stmt = sqlite_insert(Price).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["ticker", "date"],
            set_={column: stmt.excluded[column] for column in _UPSERT_COLUMNS},
        )
        await session.execute(stmt)
    return len(rows)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
async def fetch_company(
    client: YahooClient, session, company: dict, start: datetime, end: datetime
) -> int:
    """Fetch one company's bars for a window and upsert them; returns bar count."""
    symbol = price_symbol(company)
    result = await client.fetch_daily_bars(symbol, start, end)
    rows = chart_to_rows(result, company["ticker"], _now_utc())
    return await upsert_prices(session, rows)


# A window function decides the [start, end] range for a given company.
WindowFn = Callable[[object, dict], Awaitable[tuple[datetime, datetime]]]


async def _fetch_all(window: WindowFn) -> int:
    """Run ``window`` + ``fetch_company`` for every active company; return total bars.

    One failing symbol (delisted, renamed, blocked) logs a warning and the run
    continues — same isolation as the news scrapers.
    """
    total = 0
    async with httpx.AsyncClient(
        timeout=60, headers={"User-Agent": _USER_AGENT}
    ) as http:
        client = YahooClient(http)
        async with get_db() as session:
            for company in get_active_companies():
                start, end = await window(session, company)
                try:
                    count = await fetch_company(client, session, company, start, end)
                except (PriceFetchError, httpx.HTTPError) as exc:
                    logger.warning("%s: price fetch failed: %s", company["ticker"], exc)
                    continue
                await session.commit()
                total += count
                logger.info("%s: %d bar(s) upserted", company["ticker"], count)
    return total


async def run_backfill(*, days: int = BACKFILL_DAYS) -> int:
    """Fetch the last ``days`` days of bars for all active companies."""
    end = _now_utc()
    start = end - timedelta(days=days)

    async def window(session, company):
        return start, end

    return await _fetch_all(window)


async def run_incremental() -> int:
    """Fetch only the recent tail past each ticker's last stored bar (daily job)."""
    now = _now_utc()

    async def window(session, company):
        last = await _last_date(session, company["ticker"])
        return _incremental_window(last, now)

    return await _fetch_all(window)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Daily price (OHLCV) fetcher")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--backfill",
        action="store_true",
        help=f"Fetch the last {BACKFILL_DAYS} days for all active companies",
    )
    mode.add_argument(
        "--incremental",
        action="store_true",
        help="Fetch only bars newer than what is already stored (plus a small overlap)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=BACKFILL_DAYS,
        help="Backfill window in days (only with --backfill)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    if args.backfill:
        total = asyncio.run(run_backfill(days=args.days))
    else:
        total = asyncio.run(run_incremental())

    logger.info("Done. %d bar(s) upserted.", total)


if __name__ == "__main__":
    main()
