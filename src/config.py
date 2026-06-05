import json
from pathlib import Path
from typing import Any

_COMPANIES_PATH = Path(__file__).parent.parent / "companies.json"
_REQUIRED_FIELDS = {"ticker", "name", "keywords", "active"}


def load_companies(path: Path = _COMPANIES_PATH) -> list[dict[str, Any]]:
    """Load companies.json and validate all entries have the required fields."""
    with open(path) as f:
        companies = json.load(f)

    for company in companies:
        missing = _REQUIRED_FIELDS - company.keys()
        if missing:
            raise ValueError(f"Company entry missing fields {missing}: {company}")

    return companies


def get_active_companies(path: Path = _COMPANIES_PATH) -> list[dict[str, Any]]:
    """Return only companies with active: true."""
    return [c for c in load_companies(path) if c["active"]]
