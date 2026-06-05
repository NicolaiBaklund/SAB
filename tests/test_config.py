import json
import pytest
from pathlib import Path
from src.config import load_companies, get_active_companies


def test_companies_json_loads():
    companies = load_companies()
    assert len(companies) > 0


def test_required_fields_present():
    required = {"ticker", "name", "keywords", "active"}
    for company in load_companies():
        missing = required - company.keys()
        assert not missing, f"{company['ticker']} missing {missing}"


def test_keywords_are_lists():
    for company in load_companies():
        assert isinstance(company["keywords"], list), f"{company['ticker']}: keywords must be list"


def test_active_is_bool():
    for company in load_companies():
        assert isinstance(company["active"], bool), f"{company['ticker']}: active must be bool"


def test_tickers_are_uppercase():
    for company in load_companies():
        assert company["ticker"] == company["ticker"].upper(), \
            f"Ticker {company['ticker']!r} must be uppercase"


def test_get_active_companies_only_active():
    active = get_active_companies()
    assert all(c["active"] for c in active)


def test_nrs_not_present():
    # NRS (Norway Royal Salmon) delisted 2022 after SalMar acquisition
    tickers = {c["ticker"] for c in load_companies()}
    assert "NRS" not in tickers


def test_missing_field_raises(tmp_path):
    bad = [{"ticker": "TST", "name": "Test Co", "active": True}]  # missing keywords
    bad_file = tmp_path / "bad_companies.json"
    bad_file.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="missing fields"):
        load_companies(bad_file)
