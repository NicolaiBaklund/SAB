import os
from functools import lru_cache
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_env_file() -> str:
    env = os.getenv("ENVIRONMENT", "local")
    if env == "production":
        return ".env.production"
    return ".env.local"


class Settings(BaseSettings):
    # Storage
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/sab.db"

    # IDUN sentiment scoring (Phase 2)
    IDUN_KEY: SecretStr = SecretStr("Not Set")  # API secret; masked in logs/reprs
    IDUN_BASE_URL: str = "https://llm.hpc.ntnu.no"  # endpoint (override for a local vLLM)
    # Primary scorer model — chosen by the Phase 2 bake-off (see docs/sentiment.md);
    # the scorer's --model flag overrides per run.
    IDUN_MODEL: str = "mistralai/Mistral-Large-3-675B-Instruct-2512-NVFP4"

    # Ingestion: lookback window shared by both scrapers (roadmap "Time Scope")
    LOOKBACK_DAYS: int = 90

    # Price backfill window (Phase 3.1). Deliberately longer than LOOKBACK_DAYS:
    # the prices feed technical analysis, and long indicators (200-day SMA) need
    # ~290 calendar days of history before their first value. Two years gives a
    # full year of usable long-indicator values plus context across regimes.
    PRICE_BACKFILL_DAYS: int = 730

    # Dashboard API: browser origins allowed to call it (JSON list in the env file,
    # e.g. CORS_ORIGINS=["https://sab.example.com"])
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    ENVIRONMENT: str = "local"

    model_config = SettingsConfigDict(
        env_file=get_env_file(), env_file_encoding="utf-8", extra="ignore"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
