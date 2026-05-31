from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5432/grabon_geo"
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openai_api_key: str = ""
    parser_provider: str = "groq"
    cloudproxy_url: str = ""
    slack_webhook_url: str = ""
    cron_hour: int = 0
    cron_minute: int = 0
    tier1_batch_size: int = 100
    tier2_batch_size: int = 100
    tier3_batch_size: int = 100
    tier1_concurrency: int = 3
    tier2_concurrency: int = 3
    tier3_concurrency: int = 2
    tier1_interval_hours: int = 4
    keywords_excel_path: str = "Public/Rankings Agent Keywords.xlsx"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
