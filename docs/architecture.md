# Architecture

## System Overview

```
Data Sources → Scraper → SQLite DB → NLP Scorer → Signal Generator
```

## Components

### Data Layer
- **Newsweb scraper** (Playwright) — official Oslo Børs announcements per ticker
- **News RSS scraper** — E24, DN, Intrafish
- **SQLite** — stores raw articles and sentiment scores

### NLP Layer
- IDUN API (NorwAI Magistral 24B) via OpenAI-compatible endpoint
- Input: article title + body
- Output: sentiment score (−1.0 to 1.0) + label

### Signal Layer
- Aggregates sentiment scores per ticker over rolling window
- Compares against price data (Phase 3)

## Data Flow

```
1. Scraper fetches articles → dedup by URL → insert to articles table
2. Scorer reads unscored articles → calls IDUN API → writes to sentiment table
3. Signal generator reads sentiment table → computes rolling score per ticker
```

## File Structure

```
SAB/
  companies.json        — company registry (ticker, name, keywords, active)
  src/
    config.py           — load_companies() / get_active_companies() with validation
    data/
      db.py             — SQLite setup and queries (Phase 1.2)
      newsweb.py        — Playwright scraper for Oslo Børs (Phase 1.3)
      rss.py            — RSS scraper for news sites (Phase 1.5)
    nlp/
      scorer.py         — IDUN API calls and scoring logic (Phase 2)
    signals/
      generator.py      — rolling sentiment → trading signal (Phase 3)
  docs/
  roadmap.md
  .env                  — IDUN_KEY
```
