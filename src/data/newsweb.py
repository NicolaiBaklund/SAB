"""Newsweb (Oslo Børs) scraper — Phase 1.3 / 1.4.

Fetches official børsmeldinger (regulated stock-exchange announcements) for the
active companies in ``companies.json`` and stores them in the ``articles`` table.

The public site ``newsweb.oslobors.no`` is a JS-rendered SPA, but it is backed by
a plain JSON API that we call directly with ``httpx`` — no headless browser
needed. These endpoints are undocumented but public (they serve the exact data
the public website renders):

    GET /v1/newsreader/list?issuer=<id>&fromDate=YYYY-MM-DD&toDate=YYYY-MM-DD
        -> {"data": {"messages": [{messageId, title, issuerSign, issuerId,
                                   publishedTime, numbAttachments, ...}],
                     "overflow": bool}}

    GET /v1/newsreader/message?messageId=<id>
        -> {"data": {"message": {..., "body": <plain text>,
                                 "attachments": [{"id": <int>, "name": <str>}]}}}

    GET /v1/newsreader/attachment?messageId=<id>&attachmentId=<aid>
        -> raw attachment bytes (typically PDF)

## Filtering and pagination

Messages are filtered server-side by numeric ``issuer`` id (the ticker string is
*not* a valid filter). Each active company carries its id as
``newsweb_issuer_id`` in ``companies.json``.

The list endpoint has no offset/page parameter; it returns a single (large) batch
and sets ``overflow: true`` when more rows exist for the window. We therefore
paginate by **bisecting the date range** whenever ``overflow`` is set. Per-issuer
quarterly windows are small (well under the cap), so this rarely triggers.

## Body and attachments

Each article's ``body`` holds the message's plain-text body followed by every
attachment converted to Markdown (via ``markitdown``), each under a
``## Attachment: <name>`` header. Attachments are folded into the single ``body``
column rather than a separate table — the downstream sentiment scorer (Phase 2)
just needs the text.

## Usage

    python -m src.data.newsweb --backfill        # last 90 days, all active companies
    python -m src.data.newsweb --incremental     # only new since last run (daily job)

Requires the database to exist first (``alembic upgrade head``).
Deduplication is by article URL, so overlapping windows are safe to re-run.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select

from src.config import get_active_companies
from src.data.db import get_db
from src.data.models import Article

logger = logging.getLogger(__name__)

API_BASE = "https://api3.oslo.oslobors.no/v1/newsreader"
SITE_MESSAGE_URL = "https://newsweb.oslobors.no/message/{message_id}"
SOURCE = "newsweb"
BACKFILL_DAYS = 90
INCREMENTAL_OVERLAP_DAYS = 2  # re-scan a couple of days each run; URL dedup drops repeats
_USER_AGENT = "SAB-newsweb-scraper/1.0 (salmon sentiment research)"

# A converter turns attachment bytes + filename into Markdown text. Injectable so
# tests can stub it without running markitdown.
Converter = Callable[[bytes, str], str]


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def _now_utc() -> datetime:
    """Timezone-naive UTC, matching the convention used elsewhere in the project."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _parse_published(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp (e.g. ``2026-06-05T15:28:59.263Z``) to naive UTC."""
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _message_url(message_id: int) -> str:
    return SITE_MESSAGE_URL.format(message_id=message_id)


def _dedup_messages(messages: Iterable[dict]) -> list[dict]:
    """Drop duplicate messages by ``messageId``, preserving first-seen order."""
    seen: set[int] = set()
    out: list[dict] = []
    for m in messages:
        mid = m["messageId"]
        if mid not in seen:
            seen.add(mid)
            out.append(m)
    return out


def build_body(message: dict, attachments_md: list[tuple[str, str]]) -> str | None:
    """Combine the message body and converted attachments into one text blob."""
    parts: list[str] = []
    body = (message.get("body") or "").strip()
    if body:
        parts.append(body)
    for name, markdown in attachments_md:
        markdown = (markdown or "").strip()
        if markdown:
            parts.append(f"## Attachment: {name}\n\n{markdown}")
    return "\n\n".join(parts) or None


def message_to_article(
    message: dict, attachments_md: list[tuple[str, str]], fetched_at: datetime
) -> Article:
    """Map a Newsweb message-detail dict to an ``Article`` ORM row."""
    return Article(
        ticker=message["issuerSign"],
        source=SOURCE,
        url=_message_url(message["messageId"]),
        published=_parse_published(message.get("publishedTime")),
        title=message.get("title"),
        body=build_body(message, attachments_md),
        fetched_at=fetched_at,
    )


def _incremental_window(
    last_published: datetime | None,
    now: datetime,
    *,
    overlap_days: int = INCREMENTAL_OVERLAP_DAYS,
    backfill_days: int = BACKFILL_DAYS,
) -> tuple[datetime, datetime]:
    """Date window for an incremental run.

    Start from the last stored publish time minus a small overlap (URL dedup
    handles the re-scanned tail). With nothing stored yet, fall back to a full
    backfill window.
    """
    if last_published is None:
        return now - timedelta(days=backfill_days), now
    return last_published - timedelta(days=overlap_days), now


def pdf_to_markdown(data: bytes, name: str) -> str:
    """Default converter: PDF (or other supported) attachment bytes -> Markdown."""
    from markitdown import MarkItDown

    result = MarkItDown().convert_stream(io.BytesIO(data), file_extension=".pdf")
    return (result.text_content or "").strip()


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #
class NewswebClient:
    """Thin async wrapper over the Newsweb JSON API.

    Takes an ``httpx.AsyncClient`` so callers (and tests) control transport,
    timeouts and headers.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def list_messages(
        self, issuer_id: int, from_date: datetime, to_date: datetime
    ) -> list[dict]:
        """List an issuer's messages in ``[from_date, to_date]``.

        Handles the API's lack of offset paging by bisecting the date range when
        the server reports ``overflow``.
        """
        params = {
            "issuer": issuer_id,
            "fromDate": _fmt_date(from_date),
            "toDate": _fmt_date(to_date),
        }
        resp = await self._client.get(f"{API_BASE}/list", params=params)
        resp.raise_for_status()
        data = resp.json()["data"]
        messages = data.get("messages", [])

        if data.get("overflow") and (to_date - from_date) > timedelta(days=1):
            # The API has no offset paging, so split the window into two
            # non-overlapping inclusive date ranges and recurse. Snap the
            # midpoint to midnight for clean day-granular halves.
            mid = from_date + (to_date - from_date) / 2
            mid = datetime(mid.year, mid.month, mid.day)
            left = await self.list_messages(issuer_id, from_date, mid)
            right = await self.list_messages(issuer_id, mid + timedelta(days=1), to_date)
            return _dedup_messages(left + right)

        return messages

    async def get_message(self, message_id: int) -> dict:
        """Fetch full message detail (body + attachment list)."""
        resp = await self._client.get(
            f"{API_BASE}/message", params={"messageId": message_id}
        )
        resp.raise_for_status()
        return resp.json()["data"]["message"]

    async def get_attachment(self, message_id: int, attachment_id: int) -> bytes:
        """Download an attachment's raw bytes."""
        resp = await self._client.get(
            f"{API_BASE}/attachment",
            params={"messageId": message_id, "attachmentId": attachment_id},
        )
        resp.raise_for_status()
        return resp.content


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #
async def _existing_urls(session, urls: set[str]) -> set[str]:
    """Return the subset of ``urls`` already present in the articles table."""
    if not urls:
        return set()
    result = await session.execute(select(Article.url).where(Article.url.in_(urls)))
    return set(result.scalars().all())


async def _last_published(session, ticker: str) -> datetime | None:
    """Most recent ``published`` time stored for a ticker's Newsweb articles."""
    result = await session.execute(
        select(func.max(Article.published)).where(
            Article.ticker == ticker, Article.source == SOURCE
        )
    )
    return result.scalar()


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
async def fetch_company(
    client: NewswebClient,
    session,
    company: dict,
    from_date: datetime,
    to_date: datetime,
    *,
    converter: Converter = pdf_to_markdown,
) -> int:
    """Fetch one company's announcements for a window and insert the new ones.

    Returns the number of articles inserted. Messages whose URL already exists
    are skipped before any detail/attachment request, so re-runs are cheap.
    """
    issuer_id = company.get("newsweb_issuer_id")
    if not issuer_id:
        logger.warning(
            "%s has no newsweb_issuer_id in companies.json; skipping", company["ticker"]
        )
        return 0

    summaries = await client.list_messages(issuer_id, from_date, to_date)
    urls = {_message_url(m["messageId"]) for m in summaries}
    known = await _existing_urls(session, urls)
    new = [m for m in summaries if _message_url(m["messageId"]) not in known]

    inserted = 0
    for summary in new:
        detail = await client.get_message(summary["messageId"])
        attachments_md = await _convert_attachments(client, detail, converter)
        session.add(message_to_article(detail, attachments_md, _now_utc()))
        inserted += 1
    return inserted


async def _convert_attachments(
    client: NewswebClient, detail: dict, converter: Converter
) -> list[tuple[str, str]]:
    """Download and convert every attachment; skip (with a warning) any that fail."""
    out: list[tuple[str, str]] = []
    for att in detail.get("attachments") or []:
        name = att.get("name", "")
        try:
            data = await client.get_attachment(detail["messageId"], att["id"])
            markdown = await asyncio.to_thread(converter, data, name)
            out.append((name, markdown))
        except Exception as exc:  # noqa: BLE001 — one bad PDF shouldn't sink the run
            logger.warning(
                "attachment %s of message %s failed: %s",
                att.get("id"),
                detail.get("messageId"),
                exc,
            )
    return out


# A window function decides the [from, to] range for a given company.
WindowFn = Callable[[object, dict], Awaitable[tuple[datetime, datetime]]]


async def _scrape_all(window: WindowFn, converter: Converter) -> int:
    """Run ``window`` + ``fetch_company`` for every active company; return total inserted."""
    total = 0
    async with httpx.AsyncClient(
        timeout=60, headers={"User-Agent": _USER_AGENT}
    ) as http:
        client = NewswebClient(http)
        async with get_db() as session:
            for company in get_active_companies():
                from_date, to_date = await window(session, company)
                count = await fetch_company(
                    client, session, company, from_date, to_date, converter=converter
                )
                total += count
                logger.info("%s: %d new article(s)", company["ticker"], count)
    return total


async def run_backfill(
    *, days: int = BACKFILL_DAYS, converter: Converter = pdf_to_markdown
) -> int:
    """Fetch the last ``days`` days for all active companies."""
    to_date = _now_utc()
    from_date = to_date - timedelta(days=days)

    async def window(session, company):
        return from_date, to_date

    return await _scrape_all(window, converter)


async def run_incremental(*, converter: Converter = pdf_to_markdown) -> int:
    """Fetch only announcements newer than what is already stored (daily job)."""
    now = _now_utc()

    async def window(session, company):
        last = await _last_published(session, company["ticker"])
        return _incremental_window(last, now)

    return await _scrape_all(window, converter)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Newsweb (Oslo Børs) scraper")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--backfill",
        action="store_true",
        help=f"Fetch the last {BACKFILL_DAYS} days for all active companies",
    )
    mode.add_argument(
        "--incremental",
        action="store_true",
        help="Fetch only announcements newer than what is already stored",
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

    logger.info("Done. %d new article(s) inserted.", total)


if __name__ == "__main__":
    main()
