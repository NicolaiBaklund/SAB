# Data Sources

## Newsweb (Oslo Børs) — Phase 1.3 / 1.4

**What it is:** Official regulated announcements (børsmeldinger) from Oslo Børs.  
**Why priority:** Earnings, profit warnings, insider trades — highest market impact.  
**Scraper:** `httpx` against the JSON API the Newsweb SPA itself calls — **no headless browser**.  
**Public site:** https://newsweb.oslobors.no (JS-rendered SPA, empty without JS)  
**API base:** `https://api3.oslo.oslobors.no/v1/newsreader`  
**Filter:** By numeric `issuer` id (stored per company as `newsweb_issuer_id` in `companies.json`)  
**Language:** Norwegian + some English  

### Why not Playwright

The site is a JS-rendered SPA, but it is backed by an undocumented-but-public JSON
API (the same data the public website renders). Calling it directly with `httpx`
is simpler, faster and far less brittle than driving a headless browser, so
Playwright was dropped before implementation.

### Endpoints (verified)

| Endpoint | Returns |
|----------|---------|
| `GET /list?issuer=<id>&fromDate=YYYY-MM-DD&toDate=YYYY-MM-DD` | `data.messages[]` (id, title, `issuerSign`, `publishedTime`, `numbAttachments`, …) + `data.overflow` bool |
| `GET /message?messageId=<id>` | `data.message` with plain-text `body` and `attachments[] {id, name}` |
| `GET /attachment?messageId=<id>&attachmentId=<aid>` | raw attachment bytes (usually PDF) |

### Behaviour

- **Filtering:** the `issuer` filter is the numeric issuer id, *not* the ticker string. Each company's id lives in `companies.json` (see `setup.md` for how to look up a new one).
- **Pagination:** the list endpoint has no offset/page param; it returns one batch and sets `overflow: true` when more rows exist. The scraper paginates by **bisecting the date range** on overflow. Per-issuer quarterly windows are small, so this almost never fires.
- **Body + attachments:** the article `body` is the message body followed by each attachment converted to Markdown (`markitdown`) under a `## Attachment: <name>` header — folded into the single `body` column (no separate attachments table).
- **Dedup:** by article URL (`https://newsweb.oslobors.no/message/<messageId>`), so overlapping/incremental runs are safe to re-run.

---

## News RSS — Phase 1.5

**What it is:** General financial and industry news about each company.  
**Scraper:** `src/data/rss.py` — `httpx` + `feedparser`, no Playwright.  
**Source:** **Google News RSS search**, one query per company per locale.

### Why Google News (and not E24 / DN / Intrafish directly)

The original plan named three native feeds. A spike (June 2026) found that does
not hold:

| Source | Native RSS? | Finding |
|--------|-------------|---------|
| E24.no | yes | `https://e24.no/rss` — one general feed (section feeds 404); salmon mentions sparse |
| DN.no | partial | `https://services.dn.no/api/feed/rss/` works, but general; topic filters too narrow |
| Intrafish.com | **no** | NHST paywall, no public feed |

A general feed is a lottery (you hope a salmon company shows up in the latest ~30
items). **Google News RSS search** instead is a per-company aggregator:

    https://news.google.com/rss/search?q="<term>"&hl=<lang>&gl=<country>&ceid=<...>

One query per company returns up to ~100 recent articles *about that company*
from all indexed media (E24, DN, Intrafish, NTB, local + international). We query
each company in **Norwegian and English** (`GNEWS_LOCALES`) so the English trade
press (Intrafish) is covered. Term = the company's first keyword, phrase-quoted to
cut obvious noise (e.g. the *Grieg* oilfield vs *Grieg Seafood*).

**Trade-off:** item `link` is a Google redirect URL, not a canonical publisher
link, and the endpoint is unofficial. If clean canonical URLs matter later, swap
in native aggregator feeds (E24, DN/FA, …) — the rest of the pipeline is unchanged.

### How an item becomes article rows

- **No server-side ticker filter:** every returned item is keyword-matched
  (whole-word, case-insensitive) on `title + feed text` against *all* companies in
  `companies.json`. Items matching no tracked company are dropped.
- **One row per matched ticker:** an article naming several companies is stored
  once per company. The `articles` uniqueness is therefore on `(ticker, url)`, not
  `url` alone (see the Phase 1.5 migration). Same `url` under different tickers is
  allowed; exact `(ticker, url)` repeats are deduped, so runs are safe to repeat.
- **Body:** the best text exposed by the feed item (`content:encoded` when
  present, otherwise summary/description), with a best-effort follow-up fetch of
  new article URLs to capture publisher metadata and paragraph text. If the fetch
  fails, hits Google consent, or returns no useful HTML, the RSS text is kept.
- **Freshness window:** Google News ranks by relevance, not date, and returns
  years-old articles. Results are bounded to the last `MAX_AGE_DAYS` (90) days two
  ways: the `when:90d` query operator filters at the source, and a post-filter on
  `published` drops anything older (and any undated item) — the post-filter is
  authoritative.
- **Which company a multi-company article is positive/negative about** is the
  sentiment scorer's job (Phase 2), not the scraper's.

**Gotchas:**
- Keyword quality drives false positives (a bare keyword like `Grieg` matches the
  *Edvard Grieg* oilfield). Tighten keywords, or let the scorer return *neutral*
  for off-topic items (Phase 2).
- **Cross-source dedup** (the same event in Newsweb *and* here under different
  URLs) is intentionally **not** handled yet — see roadmap "known issues".

---

## Daily prices (OHLCV) — Phase 3.1

**What it is:** Daily price bars (open/high/low/close/volume + dividend/split-adjusted close) per active company, for technical analysis (Phase 3.2+).
**Scraper:** `src/data/prices.py` — `httpx` against the Yahoo Finance v8 chart JSON API, no SDK, no browser.
**Endpoint:** `https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>?period1=…&period2=…&interval=1d&includeAdjustedClose=true`
**Symbols:** Oslo Børs listings carry the `.OL` suffix (`MOWI.OL`, `SALM.OL`, `LSG.OL`, `GSF.OL`, `BAKKA.OL`, `AUSS.OL`).

### Source comparison (June 2026)

The roadmap named "Yahoo Finance / Euronext" as candidates; the full field
considered:

| Source | Free, no key? | Oslo Børs OHLCV? | Adjusted close? | History depth | Verdict |
|--------|---------------|------------------|-----------------|---------------|---------|
| **Yahoo Finance v8 chart API** | yes | yes (`.OL` symbols) | yes (`adjclose` series) | decades | **chosen** |
| Euronext (`live.euronext.com`) | yes | yes (official operator) | no | limited windows | undocumented POST endpoints keyed by ISIN+MIC, Cloudflare-fronted; no adjusted series — more brittle for no data advantage |
| Stooq (CSV) | yes | **no** (US/PL/DE/UK/JP coverage) | partial | — | no Oslo coverage |
| Alpha Vantage / Twelve Data / Marketstack / EODHD | API key | partial/paid | varies | varies | free tiers are heavily capped (e.g. 25 req/day) and Oslo coverage is paid or spotty |

Yahoo wins on every axis that matters here and matches the project's existing
pattern (Newsweb): a public JSON API the vendor's own site uses, called directly
with `httpx`. The endpoint is **unofficial** — same standing as the Newsweb API —
so it can change without notice; the fetcher is isolated in `src/data/prices.py`
with mocked-transport tests, so breakage is easy to localize. If it dies,
Euronext is the documented fallback (requires adding ISINs to `companies.json`
and computing adjustment factors ourselves).

> **Verification caveat:** this comparison was made from documented API
> behaviour (the v8 chart API is also what the `yfinance` library uses); the
> development sandbox had **no outbound network**, so per-symbol responses could
> not be live-tested before merge. The first `--backfill` run on a normal
> machine is the live test — per-symbol failures surface as warnings, not
> crashes.

### Behaviour

- **Dynamic symbol mapping:** symbol = `<ticker>.OL` by default, overridable per
  company via an optional `price_symbol` field in `companies.json` — adding or
  deactivating a company needs no code change.
- **Upsert, not insert:** bars are written with an upsert on `(ticker, date)`.
  Two reasons: a bar fetched while the market is open is partial (`close` = last
  trade so far), and `adj_close` is revised retroactively for the *whole history*
  whenever a dividend/split lands. Incremental runs re-fetch a few days of tail
  (`INCREMENTAL_OVERLAP_DAYS`), so recent revisions self-heal.
- **`adj_close` staleness:** incremental runs only touch the tail, so after a
  dividend the *older* stored `adj_close` values are stale until the next full
  `--backfill` (which overwrites everything). Recommended: monthly `--backfill`
  in the scheduler (see `setup.md`). Compute returns/indicators from `adj_close`;
  `close` is what actually traded.
- **Backfill window:** `PRICE_BACKFILL_DAYS` (default 730) — deliberately longer
  than the 90-day news window because long technical indicators (200-day SMA)
  need ~290 calendar days before their first value.
- **Null bars** (halted days) come back as nulls in the parallel arrays and are
  dropped; bar timestamps are converted to exchange-local trading days using the
  `gmtoffset` from the response metadata.
- **User-Agent:** Yahoo rejects obviously-scripted UAs, so the client sends a
  browser-like one (the same workaround `yfinance` relies on).
- **Failure isolation:** an unknown/delisted symbol returns a structured
  `chart.error` (HTTP 404) → logged warning for that company, run continues.
