"""Sentiment scorer — Phase 2.1.

Reads articles that have no sentiment row yet *for the chosen model*, asks the
IDUN model to judge each one's price impact on its ticker, and writes the result
to the ``sentiment`` table. This is the orchestration layer; the moving parts
live elsewhere and are unit-tested in isolation:

- :mod:`src.nlp.prompt`  — builds the (deterministic, versioned) messages and
  parses/validates the model's JSON answer.
- :mod:`src.nlp.client`  — the rate-limited IDUN HTTP client.

## One row = one (article, ticker)

The scrapers already fan a multi-company article into one ``articles`` row per
ticker (uniqueness is ``(ticker, url)``). So scoring is simply "score each
unscored row", and the per-ticker framing the roadmap asks for falls out for
free: each row carries exactly one ticker, and the prompt judges sentiment toward
*that* company only.

## Re-runnable and model-scoped

"Unscored" means "no sentiment row with this ``model``". Re-running is therefore
safe and resumable: progress is committed every 25 rows (see :func:`score`), so a
crash/SIGINT/timeout mid-batch loses at most the handful of in-flight rows since
the last checkpoint — not the whole run — and they are simply rescored next time.
Scoring the same articles with a *second* model — the Phase 2 bake-off — inserts a
new set of rows rather than colliding with the first.

## Usage

    python -m src.nlp.scorer                       # score with settings.IDUN_MODEL
    python -m src.nlp.scorer --model <idun-model>  # pick a model (bake-off)
    python -m src.nlp.scorer --limit 50            # cap this run
    python -m src.nlp.scorer --dry-run --limit 3   # print prompts, call nothing

Requires the database (``alembic upgrade head``) and ``IDUN_KEY`` in the
environment / ``.env.local``. Run off-peak (18:00–06:00 / weekends).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from src.config import load_companies
from src.data.db import get_db
from src.data.models import Article, Sentiment
from src.nlp.client import IdunClient, RateLimiter
from src.nlp.prompt import (
    DEFAULT_MAX_BODY_CHARS,
    PROMPT_VERSION,
    ParseError,
    ScoreResult,
    build_messages,
    parse_response,
)
from src.settings import get_settings

logger = logging.getLogger(__name__)

# Nudge appended for a single re-ask when the first reply will not parse.
_REASK = (
    "Your previous reply could not be parsed. Reply with ONLY the JSON object "
    '{"label": "...", "relevance": "...", "rationale": "..."} and nothing else.'
)


def _now_utc() -> datetime:
    """Timezone-naive UTC, matching the convention used elsewhere in the project."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _company_names() -> dict[str, str]:
    """``ticker -> display name`` for every configured company (active or not)."""
    return {c["ticker"]: c["name"] for c in load_companies()}


async def _unscored_articles(session, model: str, limit: int | None) -> list[Article]:
    """Articles with no sentiment row for ``model``, oldest first."""
    scored = select(Sentiment.article_id).where(Sentiment.model == model)
    stmt = select(Article).where(Article.id.notin_(scored)).order_by(Article.id)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars())


async def score_article(
    client: IdunClient,
    article: Article,
    name: str,
    *,
    max_body_chars: int = DEFAULT_MAX_BODY_CHARS,
) -> ScoreResult:
    """Score one article for its ticker, with a single re-ask on a parse failure."""
    messages = build_messages(
        article.ticker, name, article.title, article.body, max_body_chars=max_body_chars
    )
    raw = await client.complete(messages)
    try:
        return parse_response(raw)
    except ParseError as first:
        logger.debug("reask article %s after parse error: %s", article.id, first)
        retry = [*messages, {"role": "assistant", "content": raw}, {"role": "user", "content": _REASK}]
        return parse_response(await client.complete(retry))


async def score(
    client: IdunClient,
    session,
    *,
    limit: int | None = None,
    max_body_chars: int = DEFAULT_MAX_BODY_CHARS,
    now: datetime | None = None,
) -> int:
    """Score all unscored articles for ``client.model``; return rows written.

    A row that fails twice (still unparseable, or an IDUN error) is logged and
    skipped — it stays unscored and is retried on the next run rather than
    poisoning the batch.

    Progress is committed every 25 rows so a crash mid-batch keeps the work done
    so far; ``get_db`` commits any final partial checkpoint on a clean exit.
    """
    now = now or _now_utc()
    names = _company_names()
    articles = await _unscored_articles(session, client.model, limit)
    logger.info("%d unscored article(s) for model %s", len(articles), client.model)

    written = 0
    for i, article in enumerate(articles, 1):
        name = names.get(article.ticker, article.ticker)
        try:
            result = await score_article(client, article, name, max_body_chars=max_body_chars)
        except Exception as exc:  # noqa: BLE001 — one bad row shouldn't sink the batch
            logger.warning("skip article %s (%s): %s", article.id, article.ticker, exc)
            continue
        session.add(
            Sentiment(
                article_id=article.id,
                score=result.score,
                label=result.label,
                relevance=result.relevance,
                rationale=result.rationale,
                model=client.model,
                prompt_version=PROMPT_VERSION,
                scored_at=now,
            )
        )
        written += 1
        if i % 25 == 0:
            # Checkpoint progress so a crash/SIGINT/timeout mid-batch keeps the
            # rows already scored instead of rolling back the whole run.
            await session.commit()
            logger.info("scored %d/%d", i, len(articles))
    logger.info("done: %d scored, %d skipped", written, len(articles) - written)
    return written


async def _dry_run(limit: int, model: str, max_body_chars: int) -> None:
    """Print the reconstructed prompt for up to ``limit`` unscored rows; no API."""
    # Article bodies (and the prompt) contain UTF-8 the Windows console codec
    # (cp1252) cannot encode; force UTF-8 so the dump never dies on a glyph.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    names = _company_names()
    async with get_db() as session:
        articles = await _unscored_articles(session, model, limit)
    for article in articles:
        name = names.get(article.ticker, article.ticker)
        messages = build_messages(
            article.ticker, name, article.title, article.body, max_body_chars=max_body_chars
        )
        print(f"\n===== article {article.id} [{article.ticker}] {article.url} =====")
        for m in messages:
            print(f"\n--- {m['role']} ---\n{m['content']}")


async def run(
    model: str,
    *,
    limit: int | None = None,
    max_body_chars: int = DEFAULT_MAX_BODY_CHARS,
    rpm: int | None = None,
    json_object: bool = True,
) -> int:
    """Open an HTTP client + DB session and score unscored articles for ``model``."""
    settings = get_settings()
    api_key = settings.IDUN_KEY.get_secret_value()
    if api_key in ("", "Not Set"):
        raise SystemExit("IDUN_KEY is not set — add it to .env.local")
    limiter = RateLimiter(rpm) if rpm else RateLimiter()
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as http:
        client = IdunClient(
            http, model=model, api_key=api_key, base_url=settings.IDUN_BASE_URL,
            limiter=limiter, json_object=json_object,
        )
        async with get_db() as session:
            return await score(client, session, limit=limit, max_body_chars=max_body_chars)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="IDUN sentiment scorer (Phase 2.1)")
    parser.add_argument("--model", default=get_settings().IDUN_MODEL, help="IDUN model id to score with")
    parser.add_argument("--limit", type=int, default=None, help="max articles this run")
    parser.add_argument(
        "--max-body-chars", type=int, default=DEFAULT_MAX_BODY_CHARS,
        help="truncate article body to this many characters before scoring",
    )
    parser.add_argument("--rpm", type=int, default=None, help="requests/min cap (default 18)")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the prompt for --limit rows and exit without calling IDUN",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.dry_run:
        asyncio.run(_dry_run(args.limit or 3, args.model, args.max_body_chars))
        return

    total = asyncio.run(
        run(
            args.model,
            limit=args.limit,
            max_body_chars=args.max_body_chars,
            rpm=args.rpm,
        )
    )
    logger.info("Done. %d sentiment row(s) written.", total)


if __name__ == "__main__":
    main()
