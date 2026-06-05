# Data Sources

## Newsweb (Oslo Børs) — Phase 1.3

**What it is:** Official regulated announcements (børsmeldinger) from Oslo Børs.  
**Why priority:** Earnings, profit warnings, insider trades — highest market impact.  
**Scraper:** Playwright (headless browser) — site is JS-rendered SPA, no API or RSS available.  
**URL:** https://newsweb.oslobors.no  
**Filter:** By ticker symbol (e.g. MOWI, SALM)  
**Language:** Norwegian + some English  

**Gotchas:**
- JS-rendered — raw HTTP fetch returns empty page
- Ticker format TBD (verify MOWI vs MOWI.OL on the site)
- May require scroll/pagination for historical data

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
