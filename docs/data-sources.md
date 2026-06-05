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

**What it is:** Financial and industry news from Norwegian media.  
**Scraper:** RSS feed parser (feedparser) — no Playwright needed.  

| Source | Language | Focus |
|--------|----------|-------|
| E24.no | Norwegian | General financial news |
| DN.no (Dagens Næringsliv) | Norwegian | Business/financial newspaper |
| Intrafish.com | English | Salmon/aquaculture industry |

**Gotchas:**
- RSS feeds may not include full article body — may need follow-up HTTP fetch
- Must filter by company keyword (ticker or company name) since feeds are not ticker-specific
- Dedup against Newsweb content to avoid double-scoring the same event
