import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_env_file() -> str:
    env = os.getenv("ENVIRONMENT", "local")
    if env == "production":
        return ".env.production"
    return ".env.local"


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/sab.db"
    IDUN_KEY: str = "Not Set"
    ENVIRONMENT: str = "local"

    model_config = SettingsConfigDict(env_file=get_env_file())


@lru_cache
def get_settings() -> Settings:
    return Settings()
