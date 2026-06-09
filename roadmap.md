# SAB — Sentiment Analysis Bot (Norwegian Stock Trading)

## Goal

Build a sentiment analysis and financial-analysis pipeline for Norwegian salmon/aquaculture companies listed on Oslo Børs. Use financial context as the baseline, then evaluate whether news sentiment improves trading signals.

## What exists today

- Python venv, pytest wired up
- `.env.local` with IDUN API key and DATABASE_URL; `.env.production` template; env-file detection via `ENVIRONMENT` var
- `companies.json` — 6 Oslo Børs salmon companies with tickers, names, keywords, `newsweb_issuer_id`, active flag
- `src/config.py` — `load_companies()` and `get_active_companies()` with field validation
- `src/settings.py` — pydantic-settings `Settings` class with `get_settings()`
- `src/data/models.py` — SQLAlchemy ORM: `Article` + `Sentiment` tables
- `src/data/db.py` — async engine, `init_db()`, `get_db()` context manager
- `src/data/newsweb.py` — Newsweb (Oslo Børs) scraper via the public JSON API (`httpx`, no browser); `--backfill` (90 days) and `--incremental` (daily, dedup by URL) modes; PDF attachments converted to Markdown via `markitdown`
- `src/data/rss.py` — News scraper via **Google News RSS search** (`httpx` + `feedparser`); one query per company per locale (no/en), keyword-matched to tickers, **one article row per matched company**; `--backfill`/`--incremental` (same fetch for RSS), dedup by `(ticker, url)`
- `alembic/` — migration tooling; initial migration creates both tables, a second swaps `articles` uniqueness from `url` to `(ticker, url)`
- `data/` — SQLite DB lives here (gitignored, created by `alembic upgrade head`)
- `frontend/` — React + Vite review dashboard (read-only article/sentiment review with filters and pagination); dark/light theming with a VS Code-style dark palette and bright cyan accents (`src/styles.css`), theme preference persisted in `localStorage`; SVG terminal-prompt logo (`public/favicon.svg`) used in the sidebar brand and as the favicon
- No sentiment scoring or signals yet

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
| 1.5 | News RSS scraper (Google News RSS, per company) | done |
| 2.1 | Sentiment scoring via IDUN (NorwAI) | not started |
| 2.2 | Score storage + aggregation | not started |
| 3.1 | Price data fetch (Yahoo Finance / Euronext) | not started |
| 3.2 | Financial baseline analysis (returns, volatility, volume, fundamentals where available) | not started |
| 3.3 | Sentiment–price / sentiment–financial baseline correlation analysis | not started |
| 3.4 | Signal generation (financial baseline + rolling sentiment overlay) | not started |
| 4.1 | Dashboard / visualization | in progress |

## Data schema (SQLite)

```sql
-- articles table
id          INTEGER PRIMARY KEY
ticker      TEXT        -- e.g. MOWI
source      TEXT        -- newsweb | gnews
url         TEXT        -- canonical link (Newsweb) or Google News redirect (gnews)
published   DATETIME
title       TEXT
body        TEXT
fetched_at  DATETIME
-- UNIQUE(ticker, url): a multi-company news article is stored once per company

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
- **Cross-source dedup not implemented.** The same event can appear in Newsweb *and* via Google News under different URLs, so it would be scored twice. Deferred — best handled at scoring time (Phase 2) by fuzzy match on ticker + title + publish date.
- **RSS source = Google News (unofficial).** E24/DN/Intrafish lack usable native feeds (DN/Intrafish paywalled), so `src/data/rss.py` uses Google News RSS search. Caveats: item URLs are Google redirect links (not canonical), the endpoint is unofficial, and a feed only exposes its current window (no historical backfill). Swap to native aggregator feeds later if canonical URLs are needed.
- **RSS keyword false positives.** A bare keyword (e.g. `Grieg`) can match unrelated items (the *Edvard Grieg* oilfield). Tighten `companies.json` keywords, add a relevance step, or rely on the scorer returning *neutral* (Phase 2).
- **Sentiment must be attributed per company (Phase 2).** A single RSS article can produce rows for several tickers; the scorer needs to judge sentiment *toward each ticker*, not the article overall.
- **Phase 2 audit constraint.** The scorer input must be built *only* from the stored `title` + `body` (plus per-ticker framing) — no extra fetch/enrichment at score time — so the review GUI can reconstruct exactly what the model received without storing it. Prompt text stays a deterministic, versioned template in code; if prompt-editing moves to the GUI later, store prompts in the DB and record `sentiment.prompt_version`.
- **Financial analysis baseline before signals.** Sentiment should not be the whole strategy. Add a reusable financial context layer (price history, returns, volatility, volume, valuation/fundamental metrics where available, earnings dates, and salmon-sector indicators if accessible), then measure whether sentiment improves that baseline.
- IDUN off-peak scheduling important given 20 req/min limit

## Tech stack

| Layer | Choice | Reason |
|-------|--------|--------|
|| Language | Python 3.10+ | user preference |
| Storage | SQLite | zero ops, enough for this scale |
| HTTP | `httpx` | async-capable; used directly against the Newsweb JSON API |
| Scraping | Newsweb JSON API (no browser) | SPA is backed by a public JSON API — Playwright not needed |
| News RSS | Google News RSS + `feedparser` | per-company aggregator; native E24/DN/Intrafish feeds unusable (paywalled) |
| Attachments | `markitdown[pdf]` | convert PDF announcements to text for scoring |
| Scheduling | OS cron / Task Scheduler | run `--incremental` off-peak; no in-process daemon |
| NLP | IDUN NorwAI Magistral 24B | free, Norwegian-aware |
| Config | `companies.json` | dynamic, no code change to add company |
