# Setup

## Requirements

- Python 3.10+
- Node.js 20+ for the dashboard frontend

## Install

```bash
pip install -r requirements.txt
```

Install dashboard frontend dependencies separately:

```bash
cd frontend
npm install
```

The Newsweb scraper uses `markitdown` (with the `[pdf]` extra) to convert PDF
attachments to text — installed by `requirements.txt`. No headless browser is
needed; the scraper talks to the Newsweb JSON API directly with `httpx`.

## Environment Variables

Create `.env.local` in project root:

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

# 2. Fetch news about all active companies via Google News RSS
python -m src.data.rss --backfill

# 3. Score unscored articles (Phase 2) — see "Sentiment scoring" below
python -m src.nlp.scorer
```

## Sentiment scoring (Phase 2)

Full design in `docs/sentiment.md`. Scoring needs `IDUN_KEY` set and should run
off-peak (18:00–06:00 / weekends) given the 20 req/min limit.

```bash
# Inspect the exact prompt the model will receive for real articles (no API call)
python -m src.nlp.scorer --dry-run --limit 3

# Pick the model empirically: sample a gold set, hand-label gold_label in the
# JSONL, then compare candidate models (accuracy / macro-F1 / consistency / κ)
python -m src.nlp.eval sample --n 30
python -m src.nlp.eval run --k 2

# Score all unscored (article, ticker) rows with the chosen model. Re-runnable:
# "unscored" means "no row for this model", so a second model re-scores in full.
python -m src.nlp.scorer --model NorwAI/NorwAI-Magistral-24B-reasoning
python -m src.nlp.scorer --limit 50        # cap a run
```

## Daily Updates (incremental)

After the initial backfill, fetch only new announcements:

```bash
python -m src.data.newsweb --incremental   # Oslo Børs announcements
python -m src.data.rss --incremental        # Google News (same fetch as --backfill)
```

Newsweb fetches from each ticker's most-recent stored announcement forward.
For RSS, `--incremental` and `--backfill` do the same fetch — a feed only exposes
its current window, so there is no historical backfill; both flags exist for cron
parity. Deduplication (URL for Newsweb, `(ticker, url)` for RSS) makes both safe
to run repeatedly.

## Dashboard

Run the read-only API from the repository root:

```bash
uvicorn src.api.main:app --reload
```

Run the React/Vite frontend in another terminal:

```bash
cd frontend
npm run dev
```

Open `http://localhost:5173/review`. Vite proxies `/api` requests to
`http://127.0.0.1:8000`.

### Scheduling

Run the incremental job daily off-peak via your OS scheduler (no in-process
daemon ships with the project):

- **Linux/macOS (cron)** — e.g. 03:00 nightly:
  ```cron
  0 3 * * *  cd /path/to/SAB && .venv/bin/python -m src.data.newsweb --incremental
  5 3 * * *  cd /path/to/SAB && .venv/bin/python -m src.data.rss --incremental
  ```
- **Windows (Task Scheduler)** — daily triggers running
  `…\.venv\Scripts\python.exe -m src.data.newsweb --incremental` and
  `…\.venv\Scripts\python.exe -m src.data.rss --incremental` in the repo dir.

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
