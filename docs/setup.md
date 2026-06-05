# Setup

## Requirements

- Python 3.10+
- Playwright (for Newsweb scraping)

## Install

```bash
pip install -r requirements.txt
playwright install chromium
```

## Environment Variables

Create `.env` in project root:

```
IDUN_KEY=sk-...
```

IDUN API endpoint: `https://llm.hpc.ntnu.no`  
Rate limit: 20 req/min, 300k tokens/min. Run off-peak (18:00–06:00 / weekends).

## First Run

```bash
# Fetch last 90 days of Newsweb announcements for all active companies
python -m src.data.newsweb --backfill

# Score unscored articles (Phase 2)
python -m src.nlp.scorer
```

## Adding a Company

Edit `companies.json`:

```json
{
  "ticker": "XXXX",
  "name": "Company Name",
  "keywords": ["Company Name", "XXXX"],
  "active": true
}
```

No code changes needed.
