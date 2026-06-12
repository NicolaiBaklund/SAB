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
- **Sentiment page** (`frontend/src/pages/SentimentPage.tsx` + `src/api/sentiment.py`) — Plotly time-series of predicted sentiment per active company: daily mean (dots) + 7-day article-weighted rolling mean (line), combined view with company checkboxes or per-company small multiples; `off_topic` scores excluded; Plotly loaded lazily so other pages don't pay for the bundle. Empty until the first scoring run.
- `src/nlp/` — sentiment scoring stack (Phase 2.1): `prompt.py` (versioned, deterministic price-impact template + JSON parser), `client.py` (rate-limited IDUN OpenAI-compatible client, guided JSON on by default + self-heal), `scorer.py` (scores unscored `(article, ticker)` rows of **active companies only** → `sentiment` table; `--dry-run` prints the reconstructed prompt), `eval.py` (model bake-off: gold-set sampler + accuracy/macro-F1/self-consistency/κ metrics). See `docs/sentiment.md`. **Built, unit-tested, and the bake-off has chosen Mistral-Large-3-675B; the first full scoring run over the stored articles is still pending.**
- `src/data/prices.py` — daily price (OHLCV + adjusted close) fetcher (Phase 3.1) via the **Yahoo Finance v8 chart API** (`httpx`, no SDK); symbols derived dynamically as `<ticker>.OL` (optional `price_symbol` override in `companies.json`); `--backfill` (2 years, `PRICE_BACKFILL_DAYS`) and `--incremental` (recent tail) modes; bars **upserted** on `(ticker, date)` so partial intraday bars and retroactive `adj_close` revisions self-heal. Source comparison + decision in `docs/data-sources.md`. **Built and unit-tested; the first live backfill is still pending** (the dev sandbox had no outbound network).
- No signals yet

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

- **Lens:** price-impact / investor — "does this news raise or lower the expected share price of *this* company?" — not editorial tone.
- **Scale:** 3-point categorical — `negative` / `neutral` / `positive` → score `−1 / 0 / +1`. The model emits the label; the float is derived (no false-precision regression). Plus a `relevance` (`direct` / `mentioned` / `off_topic`) that guards keyword false matches, and a one-line `rationale`.
- **Model:** **Mistral-Large-3-675B** (`settings.IDUN_MODEL`), chosen by an empirical bake-off over a 40-item gold set (`src/nlp/eval.py`): best off-topic recall (1.00) and 4.5× faster than runner-up GLM-4.7, with accuracy (0.93) within labeling noise. Guided JSON (`response_format`) is required — reasoning models (NorwAI, Qwen) otherwise emit prose/empty. Full results: `docs/sentiment.md`.
- Endpoint: `https://llm.hpc.ntnu.no` (OpenAI-compatible)
- Rate limit: 20 req/min, 300k tokens/min — run off-peak (18:00–06:00 / weekends)
- Full design: `docs/sentiment.md`

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| 1.1 | Company config (`companies.json`) | done |
| 1.2 | SQLite schema + DB setup | done |
| 1.3 | Newsweb (Oslo Børs) scraper (JSON API + httpx) | done |
| 1.4 | Incremental fetch / dedup (+ documented cron scheduling) | done |
| 1.5 | News RSS scraper (Google News RSS, per company) | done |
| 2.1 | Sentiment scoring via IDUN (prompt + client + scorer + bake-off harness) | code done; first run pending |
| 2.2 | Score storage (+ aggregation) | storage done (schema + scorer writes); viz aggregation done (`/api/sentiment/timeseries`: daily mean + 7d rolling, request-time); signal aggregation pending |
| 3.1 | Price data fetch (Yahoo Finance / Euronext) | code done (Yahoo chosen); first live backfill pending |
| 3.2 | Financial baseline analysis (returns, volatility, volume, fundamentals where available) | not started |
| 3.3 | Sentiment–price / sentiment–financial baseline correlation analysis | not started |
| 3.4 | Signal generation (financial baseline + rolling sentiment overlay) | not started |
| 4.1 | Dashboard / visualization | in progress (Review page + Sentiment time-series page done; Signals/Projections pages pending) |

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
id              INTEGER PRIMARY KEY
article_id      INTEGER REFERENCES articles(id)
score           REAL        -- 3-point: -1.0 | 0.0 | +1.0 (derived from label)
label           TEXT        -- positive | negative | neutral
relevance       TEXT        -- direct | mentioned | off_topic (keyword-match quality)
rationale       TEXT        -- one-line model justification
model           TEXT        -- IDUN model id used
prompt_version  TEXT        -- template version (e.g. price-impact-v1)
scored_at       DATETIME

-- prices table (Phase 3.1)
id          INTEGER PRIMARY KEY
ticker      TEXT        -- company ticker (MOWI), not the source symbol (MOWI.OL)
date        DATE        -- trading day, exchange-local
open        REAL
high        REAL
low         REAL
close       REAL        -- required; bars without a close are dropped
adj_close   REAL        -- dividend/split-adjusted; use for returns/indicators
volume      INTEGER
currency    TEXT        -- e.g. NOK (from source metadata)
source      TEXT        -- yahoo
fetched_at  DATETIME
-- UNIQUE(ticker, date): bars are UPSERTED (intraday bars are partial; adj_close
-- is revised retroactively on dividends/splits)
```

## Known issues / next steps

- **Newsweb scraping resolved without Playwright.** The SPA is backed by a public JSON API (`api3.oslo.oslobors.no/v1/newsreader`) that we call directly with `httpx`. The earlier assumption that a headless browser was required turned out to be wrong — see `docs/data-sources.md`.
- **Newsweb filtering** is by numeric `issuer` id, not ticker string; ids are stored per company as `newsweb_issuer_id` in `companies.json`.
- **Attachments** are folded into the article `body` (message text + each PDF converted to Markdown) rather than a dedicated table — revisit if structured per-attachment metadata is needed later.
- **Scheduling** is via OS cron / Windows Task Scheduler invoking `--incremental` (documented in `docs/setup.md`); no bespoke in-process scheduler. Add APScheduler later only if a long-running daemon is wanted.
- The undocumented Newsweb API could change without notice — the scraper is isolated in `src/data/newsweb.py` and covered by tests using mocked transport, so breakage is easy to localize.
- **Cross-source dedup not implemented.** The same event can appear in Newsweb *and* via Google News under different URLs, so it would be scored twice. Deferred — best handled at scoring time (Phase 2) by fuzzy match on ticker + title + publish date.
- **RSS source = Google News (unofficial).** E24/DN/Intrafish lack usable native feeds (DN/Intrafish paywalled), so `src/data/rss.py` uses Google News RSS search. Caveats: item URLs are Google redirect links (not canonical), the endpoint is unofficial, and a feed only exposes its current window (no historical backfill). Results are bounded to the last `MAX_AGE_DAYS` (90) days via the `when:90d` query operator **and** an authoritative `published` post-filter, since Google News otherwise ranks years-old articles by relevance. Swap to native aggregator feeds later if canonical URLs are needed.
- **RSS keyword false positives.** A bare keyword (e.g. `Grieg`) can match unrelated items (the *Edvard Grieg* oilfield). *Addressed (Phase 2.1):* the scorer returns `relevance: off_topic` (coerced to neutral/0) and stores the flag so the GUI can surface bad matches; still worth tightening `companies.json` keywords as they show up.
- **Sentiment attributed per company.** *Resolved (Phase 2.1):* the scrapers already store one row per `(ticker, url)`, and the scorer's prompt judges price impact toward that single ticker — see `docs/sentiment.md`.
- **Prompt versioning persistence + GUI editing (future).** Today each `sentiment` row stores a `prompt_version` *tag* and the template text lives in code (old versions recoverable via git). Planned upgrade: a `prompt_versions` table holding each version's full text (system prompt, few-shot, user template, `max_body_chars`) keyed by the version string, so every version is reconstructable from the DB without git — and the prerequisite for editing prompts in the GUI (save a new `prompt_versions` row, bump the tag). Fold the `--max-body-chars` runtime knob into the version at that point (it currently changes the model input but is not captured by the tag).
- **Phase 2 audit constraint.** *Implemented (Phase 2.1):* `src/nlp/prompt.py` builds the model input *only* from the stored `title` + `body` plus per-ticker framing (no fetch/enrichment at score time), as a deterministic versioned template; `sentiment.prompt_version` records which template produced each score, so the GUI can reconstruct the exact input. `--dry-run` prints it.
- **Financial analysis baseline before signals.** Sentiment should not be the whole strategy. Add a reusable financial context layer (price history, returns, volatility, volume, valuation/fundamental metrics where available, earnings dates, and salmon-sector indicators if accessible), then measure whether sentiment improves that baseline.
- **Review page scoped to active companies.** *Resolved:* `src/api/review.py` restricts the article listing, per-article sentiment bubbles, and the Company filter dropdown to tickers marked `active` in `companies.json` (intersected with what's actually in the DB), so rows for inactive/delisted tickers no longer surface in the GUI.
- **`off_topic` rows excluded from viz/analytics but kept in the DB.** The Sentiment page (and any future analytics) drops `relevance = off_topic` scores — they are keyword false matches, not signal. The rows are deliberately **not deleted**: scraper dedup works against stored `(ticker, url)` rows, so deleting them would just re-collect (and re-score) the same articles on the next incremental run. See `docs/sentiment.md` "Visualization".
- **Sentiment page is empty until the first scoring run** (`python -m src.nlp.scorer`, off-peak). The page shows an explicit empty-state message until then.
- IDUN off-peak scheduling important given 20 req/min limit
- **Price source = Yahoo Finance v8 chart API (unofficial).** Chosen over Euronext (undocumented POST endpoints, no adjusted close), Stooq (no Oslo coverage) and keyed providers (capped free tiers) — full comparison in `docs/data-sources.md`. Same standing as the Newsweb API: public but undocumented, isolated in `src/data/prices.py` with mocked-transport tests. **First live backfill pending** — the dev sandbox had no outbound network, so per-symbol responses were verified against the documented shape only; run `python -m src.data.prices --backfill` after `alembic upgrade head` to validate live.
- **`adj_close` goes stale after dividends on incremental runs.** Incremental price runs only re-fetch the recent tail, but a dividend revises the adjusted series for the whole history. Mitigation: monthly `--backfill` overwrites everything (documented in `setup.md` scheduling). Compute returns from `adj_close`, not `close`.
- **Data gaps toward the predictive model (Phase 3.2+).** The `prices` table covers daily-horizon technical analysis in full (OHLCV + `adj_close`, 2y history). Known missing inputs, in priority order — add only when the 3.2 baseline shows they're needed: **OSEBX benchmark index** (separate market moves from company signals; ~free via the existing fetcher's `price_symbol` mechanism), **Fish Pool salmon spot index** (the sector's dominant fundamental; small new scraper), **EUR/NOK** (`EURNOK=X`, same API), structured earnings calendar, and price sanity checks (Yahoo Oslo data occasionally glitches). Full table in `docs/data-sources.md`.
- **Sample size is the binding constraint for Phase 3.3/3.4, not features.** Sentiment–price overlap is ~90 days × 6 tickers ≈ 540 daily observations (~18 independent weekly windows) — enough for correlation/event studies, not for parameter-hungry ML. Start with simple models and extend the news window before anything bigger.

## Tech stack

| Layer | Choice | Reason |
|-------|--------|--------|
|| Language | Python 3.10+ | user preference |
| Storage | SQLite | zero ops, enough for this scale |
| HTTP | `httpx` | async-capable; used directly against the Newsweb JSON API |
| Scraping | Newsweb JSON API (no browser) | SPA is backed by a public JSON API — Playwright not needed |
| News RSS | Google News RSS + `feedparser` | per-company aggregator; native E24/DN/Intrafish feeds unusable (paywalled) |
| Prices | Yahoo Finance v8 chart API (no SDK) | free, keyless, Oslo Børs OHLCV + adjusted close + deep history; Euronext/Stooq/keyed providers ruled out (see `docs/data-sources.md`) |
| Attachments | `markitdown[pdf]` | convert PDF announcements to text for scoring |
| Scheduling | OS cron / Task Scheduler | run `--incremental` off-peak; no in-process daemon |
| NLP | IDUN (OpenAI-compatible) via `httpx`; model chosen by bake-off | free; Norwegian-capable; no extra SDK dependency |
| Charts | Plotly (`plotly.js-basic-dist-min` + thin React wrapper) | basic dist covers line/scatter at a fraction of the full bundle; `react-plotly.js` unmaintained vs React 19 |
| Config | `companies.json` | dynamic, no code change to add company |
