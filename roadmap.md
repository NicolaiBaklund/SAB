# SAB — Sentiment Analysis Bot (Norwegian Stock Trading)

## Goal

Build a sentiment analysis pipeline for Norwegian salmon/aquaculture companies listed on Oslo Børs. Aggregate news sentiment into trading signals.

## What exists today

- Python venv, pytest wired up
- `.env.local` with IDUN API key and DATABASE_URL; `.env.production` template; env-file detection via `ENVIRONMENT` var
- `companies.json` — 6 Oslo Børs salmon companies with tickers, names, keywords, `newsweb_issuer_id`, active flag
- `src/config.py` — `load_companies()` and `get_active_companies()` with field validation
- `src/settings.py` — pydantic-settings `Settings` class with `get_settings()`
- `src/data/models.py` — SQLAlchemy ORM: `Article` + `Sentiment` tables
- `src/data/db.py` — async engine, `init_db()`, `get_db()` context manager
- `src/data/newsweb.py` — Newsweb (Oslo Børs) scraper via the public JSON API (`httpx`, no browser); `--backfill` (90 days) and `--incremental` (daily, dedup by URL) modes; PDF attachments converted to Markdown via `markitdown`
- `alembic/` — migration tooling; initial migration creates both tables
- `data/` — SQLite DB lives here (gitignored, created by `alembic upgrade head`)
- No sentiment scoring, RSS scraping, or signals yet

## Companies (initial scope)

Salmon/aquaculture sector, Oslo Børs listed:

| Ticker | Company                  | Active |
|--------|--------------------------|--------|
| MOWI   | Mowi ASA                 | yes    |
| SALM   | SalMar ASA               | yes    |
| LSG    | Lerøy Seafood Group ASA  | yes    |
| GSF    | Grieg Seafood ASA        | yes    |
| BAKKA  | Bakkafrost P/F           | yes    |
| AUSS   | Austevoll Seafood ASA    | yes    |


System is dynamic: companies defined in `companies.json`. Add/remove without code changes.

## Time Scope

- **Initial fetch:** 90 days lookback
- **Ongoing:** daily incremental fetch (new articles only, dedup by URL)
- **Signal window:** TBD in Phase 3 (likely 7-day rolling)

**Why 90 days:**
- Covers one full earnings quarter — at least one earnings report per company in the dataset, which are the highest-impact sentiment events
- Salmon companies generate ~5–20 articles/week combined → 90 days ≈ 200–500 items per company, enough for pattern detection without overwhelming first build
- Recent enough to reflect current market regime (spot prices, export conditions, lice regulations change — data older than ~6 months may not predict current stock behavior)
- Enables a train/validate split: train on day 1–60, validate on day 61–90
- Sentiment → price lag is typically 1–5 days; 90 days gives ~18 independent signal windows to measure correlation
- Easy to extend: just change one constant if 180 days (two quarters) is needed later

## NLP

- Model: **NorwAI Magistral 24B** on IDUN (Norwegian-aware, OpenAI-compatible, free)
- Endpoint: `https://llm.hpc.ntnu.no`
- Rate limit: 20 req/min, 300k tokens/min — run off-peak (18:00–06:00 / weekends)

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| 1.1 | Company config (`companies.json`) | done |
| 1.2 | SQLite schema + DB setup | done |
| 1.3 | Newsweb (Oslo Børs) scraper (JSON API + httpx) | done |
| 1.4 | Incremental fetch / dedup (+ documented cron scheduling) | done |
| 1.5 | News RSS scraper (E24, DN, Intrafish via RSS feeds) | not started |
| 2.1 | Sentiment scoring via IDUN (NorwAI) | not started |
| 2.2 | Score storage + aggregation | not started |
| 3.1 | Price data fetch (Yahoo Finance / Euronext) | not started |
| 3.2 | Sentiment–price correlation analysis | not started |
| 3.3 | Signal generation (rolling sentiment score) | not started |
| 4.1 | Dashboard / visualization | not started |

## Data schema (SQLite)

```sql
-- articles table
id          INTEGER PRIMARY KEY
ticker      TEXT        -- e.g. MOWI
source      TEXT        -- newsweb | e24 | dn
url         TEXT UNIQUE
published   DATETIME
title       TEXT
body        TEXT
fetched_at  DATETIME

-- sentiment table (Phase 2)
id          INTEGER PRIMARY KEY
article_id  INTEGER REFERENCES articles(id)
score       REAL        -- -1.0 to 1.0
label       TEXT        -- positive | negative | neutral
model       TEXT        -- model used
scored_at   DATETIME
```

## Known issues / next steps

- **Newsweb scraping resolved without Playwright.** The SPA is backed by a public JSON API (`api3.oslo.oslobors.no/v1/newsreader`) that we call directly with `httpx`. The earlier assumption that a headless browser was required turned out to be wrong — see `docs/data-sources.md`.
- **Newsweb filtering** is by numeric `issuer` id, not ticker string; ids are stored per company as `newsweb_issuer_id` in `companies.json`.
- **Attachments** are folded into the article `body` (message text + each PDF converted to Markdown) rather than a dedicated table — revisit if structured per-attachment metadata is needed later.
- **Scheduling** is via OS cron / Windows Task Scheduler invoking `--incremental` (documented in `docs/setup.md`); no bespoke in-process scheduler. Add APScheduler later only if a long-running daemon is wanted.
- The undocumented Newsweb API could change without notice — the scraper is isolated in `src/data/newsweb.py` and covered by tests using mocked transport, so breakage is easy to localize.
- Phase 1.5 news scraping via RSS (E24, DN, Intrafish) — RSS is simpler since these sites have proper feeds.
- IDUN off-peak scheduling important given 20 req/min limit

## Tech stack

| Layer | Choice | Reason |
|-------|--------|--------|
|| Language | Python 3.10+ | user preference |
| Storage | SQLite | zero ops, enough for this scale |
| HTTP | `httpx` | async-capable; used directly against the Newsweb JSON API |
| Scraping | Newsweb JSON API (no browser) | SPA is backed by a public JSON API — Playwright not needed |
| Attachments | `markitdown[pdf]` | convert PDF announcements to text for scoring |
| Scheduling | OS cron / Task Scheduler | run `--incremental` off-peak; no in-process daemon |
| NLP | IDUN NorwAI Magistral 24B | free, Norwegian-aware |
| Config | `companies.json` | dynamic, no code change to add company |
