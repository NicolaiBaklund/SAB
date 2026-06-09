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
  (whole-word, case-insensitive) on `title + summary` against *all* companies in
  `companies.json`. Items matching no tracked company are dropped.
- **One row per matched ticker:** an article naming several companies is stored
  once per company. The `articles` uniqueness is therefore on `(ticker, url)`, not
  `url` alone (see the Phase 1.5 migration). Same `url` under different tickers is
  allowed; exact `(ticker, url)` repeats are deduped, so runs are safe to repeat.
- **Body:** the cleaned RSS summary only (HTML stripped). No follow-up article
  fetch — DN/E24 are paywalled and the headline + summary carry the signal. Full
  article fetch can be added later if needed.
- **Which company a multi-company article is positive/negative about** is the
  sentiment scorer's job (Phase 2), not the scraper's.

**Gotchas:**
- Keyword quality drives false positives (a bare keyword like `Grieg` matches the
  *Edvard Grieg* oilfield). Tighten keywords, or let the scorer return *neutral*
  for off-topic items (Phase 2).
- **Cross-source dedup** (the same event in Newsweb *and* here under different
  URLs) is intentionally **not** handled yet — see roadmap "known issues".
