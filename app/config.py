from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5432/grabon_geo"
    openai_api_key: str = ""
    cloudproxy_url: str = ""
    teams_webhook_url: str = ""
    cron_hour: int = 0
    cron_minute: int = 0
    continuous_batch_size: int = 350
    continuous_concurrency: int = 8
    keywords_excel_path: str = "Public/Rankings Agent Keywords.xlsx"
    credentials_key: str = "grabon-geo-agent-creds-key-2025"

    # SERP Crawler
    serp_batch_size: int = 300
    serp_interval_hours: int = 4
    serp_concurrency: int = 4
    serp_delay_min: float = 3.0
    serp_delay_max: float = 7.0
    serp_use_curl: bool = True
    target_domain: str = "grabon.in"

    # Multi-account pool
    max_account_slots: int = 5

    # Pipeline optimization
    parallel_engines: bool = True
    skip_fresh_data: bool = True
    freshness_hours: int = 24
    use_dedup_cache: bool = True
    concurrency_per_engine: int = 4

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
