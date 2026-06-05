# SAB — Sentiment Analysis for Bergen (Norwegian Stock Trading)

## Goal

Build a sentiment analysis pipeline for Norwegian salmon/aquaculture companies listed on Oslo Børs. Aggregate news sentiment into trading signals.

## What exists today

- Python venv, pytest wired up
- `.env` with IDUN API key (`https://llm.hpc.ntnu.no`, OpenAI-compatible)
- No data pipeline, no models, no signals yet

## Companies (initial scope)

Salmon/aquaculture sector, Oslo Børs listed:

| Ticker | Company          | Active |
|--------|-----------------|--------|
| MOWI   | Mowi ASA        | yes    |
| SALM   | SalMar ASA      | yes    |
| LSG    | Lerøy Seafood   | yes    |
| GSF    | Grieg Seafood   | yes    |
| BAKKA  | Bakkafrost      | yes    |
| AUSS   | Austevoll       | yes    |
| NRS    | Norway Royal Salmon | yes |

System is dynamic: companies defined in `companies.json`. Add/remove without code changes.

## Timescope

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
| 1.1 | Company config (`companies.json`) | not started |
| 1.2 | SQLite schema + DB setup | not started |
| 1.3 | Newsweb (Oslo Børs) Playwright scraper | not started |
| 1.4 | Daily scheduler / dedup | not started |
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

- **Newsweb is a JS-rendered SPA** — no public API, no accessible HTML without JS. Must use Playwright (headless browser) to scrape. No other option for official Oslo Børs announcements.
- Newsweb ticker format must be verified (likely `MOWI` not `MOWI.OL`)
- Phase 1.5 news scraping via RSS (E24, DN, Intrafish) — RSS is simpler than Playwright for these sites since they have proper feeds. Playwright only needed for Newsweb.
- IDUN off-peak scheduling important given 20 req/min limit

## Tech stack

| Layer | Choice | Reason |
|-------|--------|--------|
| Language | Python 3.x | user preference |
| Storage | SQLite | zero ops, enough for this scale |
| HTTP | `httpx` | async-capable |
| Scraping | `playwright` | headless browser needed for JS-rendered Newsweb |
| Scheduling | simple cron / APScheduler | TBD |
| NLP | IDUN NorwAI Magistral 24B | free, Norwegian-aware |
| Config | `companies.json` | dynamic, no code change to add company |
