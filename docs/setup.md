# Setup

## Requirements

- Python 3.10+

## Install

```bash
pip install -r requirements.txt
```

The Newsweb scraper uses `markitdown` (with the `[pdf]` extra) to convert PDF
attachments to text — installed by `requirements.txt`. No headless browser is
needed; the scraper talks to the Newsweb JSON API directly with `httpx`.

## Environment Variables

Create `.env` in project root:

```
IDUN_KEY=sk-...
```

IDUN API endpoint: `https://llm.hpc.ntnu.no`  
Rate limit: 20 req/min, 300k tokens/min. Run off-peak (18:00–06:00 / weekends).

## First Run

```bash
# 0. Create the database (one-time)
alembic upgrade head

# 1. Fetch last 90 days of Newsweb announcements for all active companies
python -m src.data.newsweb --backfill

# 2. Score unscored articles (Phase 2)
python -m src.nlp.scorer
```

## Daily Updates (incremental)

After the initial backfill, fetch only new announcements:

```bash
python -m src.data.newsweb --incremental
```

This fetches from each ticker's most-recent stored announcement forward;
deduplication is by URL, so it is safe to run repeatedly.

### Scheduling

Run the incremental job daily off-peak via your OS scheduler (no in-process
daemon ships with the project):

- **Linux/macOS (cron)** — e.g. 03:00 nightly:
  ```cron
  0 3 * * *  cd /path/to/SAB && .venv/bin/python -m src.data.newsweb --incremental
  ```
- **Windows (Task Scheduler)** — daily trigger running
  `…\.venv\Scripts\python.exe -m src.data.newsweb --incremental` in the repo dir.

## Adding a Company

Edit `companies.json`:

```json
{
  "ticker": "XXXX",
  "name": "Company Name",
  "keywords": ["Company Name", "XXXX"],
  "newsweb_issuer_id": 1234,
  "active": true
}
```

`newsweb_issuer_id` is Oslo Børs's numeric issuer id (used to filter the Newsweb
API). To find it, fetch the recent message list and read `issuerId` off any row
for that ticker, e.g.:

```bash
python -c "import httpx; from datetime import date, timedelta; \
t=date.today(); f=t-timedelta(days=120); \
ms=httpx.get('https://api3.oslo.oslobors.no/v1/newsreader/list', \
params={'fromDate':f.isoformat(),'toDate':t.isoformat()}, \
headers={'User-Agent':'Mozilla/5.0'}, timeout=60).json()['data']['messages']; \
print({m['issuerSign']: m['issuerId'] for m in ms if m['issuerSign']=='XXXX'})"
```

A company without `newsweb_issuer_id` is simply skipped by the Newsweb scraper
(logged as a warning); no code changes are needed.
