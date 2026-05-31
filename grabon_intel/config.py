"""Centralised settings — env-driven via pydantic-settings.

LLM stack (your team's):
  - NVIDIA NIM (dev / free)
  - OpenAI (prod)
  - Anthropic (optional)
  - Ollama (optional fully-local dev)

Free / OSS swaps for paid services we used to call:
  - SERP:       SearXNG (self-hosted)        instead of Serper
  - Tech stack: HTTP pattern matching (local) instead of BuiltWith
  - Embeddings: fastembed (local, 384-dim)   instead of OpenAI embeddings
  - Outreach:   raw SMTP + IMAP poll          instead of Smartlead
  - Vision:     OpenAI gpt-4o → Ollama llava  (no Anthropic vision required)
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[1] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database ------------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://grabon:grabon@localhost:5432/grabon",
        alias="GRABON_DATABASE_URL",
    )

    # --- Meta Ad Library (free) ---------------------------------------------
    meta_ad_library_token: SecretStr = Field(default=SecretStr(""), alias="META_AD_LIBRARY_TOKEN")
    meta_ad_library_base: str = Field(
        default="https://graph.facebook.com/v23.0/ads_archive",
        alias="META_AD_LIBRARY_BASE",
    )

    # --- Logging -------------------------------------------------------------
    log_level: str = Field(default="INFO", alias="GRABON_LOG_LEVEL")
    log_json: bool = Field(default=False, alias="GRABON_LOG_JSON")

    # --- Budget --------------------------------------------------------------
    daily_cost_cap_cents: int = Field(default=500, alias="GRABON_DAILY_COST_CAP_CENTS")

    # --- Kill switch ---------------------------------------------------------
    paused: bool = Field(default=False, alias="GRABON_PAUSED")

    # --- Temporal ------------------------------------------------------------
    temporal_address: str = Field(default="localhost:7233", alias="TEMPORAL_ADDRESS")
    temporal_namespace: str = Field(default="default", alias="TEMPORAL_NAMESPACE")
    temporal_task_queue: str = Field(default="grabon-intel", alias="TEMPORAL_TASK_QUEUE")
    temporal_tls: bool = Field(default=False, alias="TEMPORAL_TLS")

    # --- LLM providers -------------------------------------------------------
    # Primary dev: NVIDIA NIM free tier (OpenAI-compatible).
    nvidia_api_key: SecretStr = Field(default=SecretStr(""), alias="NVIDIA_API_KEY")
    nvidia_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1", alias="NVIDIA_BASE_URL"
    )
    # Production: OpenAI.
    openai_api_key: SecretStr = Field(default=SecretStr(""), alias="OPENAI_API_KEY")
    # Optional: Anthropic.
    anthropic_api_key: SecretStr = Field(default=SecretStr(""), alias="ANTHROPIC_API_KEY")
    # Optional fully-local: Ollama (OpenAI-compatible at /v1).
    ollama_base_url: str = Field(default="http://localhost:11434/v1", alias="OLLAMA_BASE_URL")
    ollama_api_key: str = Field(default="ollama", alias="OLLAMA_API_KEY")
    ollama_vision_model: str = Field(default="llava:13b", alias="OLLAMA_VISION_MODEL")

    # SSL verification for LLM API calls (disable for corporate proxy / dev).
    llm_ssl_verify: bool = Field(default=True, alias="GRABON_LLM_SSL_VERIFY")

    # Tier → model id. Default to dev (NVIDIA free) + OpenAI mid for smart.
    llm_tier_cheap: str = Field(default="nvidia/llama-3.3-70b", alias="GRABON_LLM_TIER_CHEAP")
    llm_tier_fast: str = Field(default="openai/gpt-4o-mini", alias="GRABON_LLM_TIER_FAST")
    llm_tier_smart: str = Field(default="openai/gpt-4o", alias="GRABON_LLM_TIER_SMART")
    llm_tier_fallback: str = Field(default="nvidia/llama-3.1-70b", alias="GRABON_LLM_TIER_FALLBACK")

    # --- Google APIs (free tiers) --------------------------------------------
    youtube_api_key: str = Field(default="", alias="YOUTUBE_API_KEY")
    google_pagespeed_api_key: str = Field(default="", alias="GOOGLE_PAGESPEED_API_KEY")

    # --- urlscan.io (free tier — 1K searches/day) -----------------------------
    urlscan_api_key: str = Field(default="", alias="URLSCAN_API_KEY")

    # --- IPinfo (free tier — unlimited with token) ---------------------------
    ipinfo_token: str = Field(default="", alias="IPINFO_TOKEN")

    # --- HubSpot CRM (free tier) ---------------------------------------------
    hubspot_api_key: str = Field(default="", alias="HUBSPOT_API_KEY")

    # --- SearXNG (free SERP) -------------------------------------------------
    searxng_url: str = Field(default="http://localhost:8888", alias="SEARXNG_URL")

    # --- Vane (self-hosted AI search synthesis) --------------------------------
    vane_url: str = Field(default="", alias="VANE_URL")

    # --- CloudProxy (IP rotation) --------------------------------------------
    cloudproxy_url: str = Field(default="", alias="CLOUDPROXY_URL")

    # --- API gateway ---------------------------------------------------------
    api_keys: str = Field(default="", alias="GRABON_API_KEYS")
    api_cors_origins: str = Field(default="http://localhost:3000", alias="GRABON_API_CORS_ORIGINS")

    def parsed_api_keys(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    def parsed_cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    # --- Notifiers (free) ----------------------------------------------------
    teams_webhook_url: SecretStr = Field(default=SecretStr(""), alias="TEAMS_WEBHOOK_URL")
    digest_email_to: str = Field(default="", alias="DIGEST_EMAIL_TO")
    digest_smtp_host: str = Field(default="", alias="DIGEST_SMTP_HOST")
    digest_smtp_port: int = Field(default=587, alias="DIGEST_SMTP_PORT")
    digest_smtp_user: str = Field(default="", alias="DIGEST_SMTP_USER")
    digest_smtp_pass: SecretStr = Field(default=SecretStr(""), alias="DIGEST_SMTP_PASS")
    digest_from: str = Field(default="", alias="DIGEST_FROM")

    # --- Outbound mailbox: SMTP send + IMAP poll (replaces Smartlead) -------
    outbound_smtp_host: str = Field(default="", alias="OUTBOUND_SMTP_HOST")
    outbound_smtp_port: int = Field(default=587, alias="OUTBOUND_SMTP_PORT")
    outbound_smtp_user: str = Field(default="", alias="OUTBOUND_SMTP_USER")
    outbound_smtp_pass: SecretStr = Field(default=SecretStr(""), alias="OUTBOUND_SMTP_PASS")
    outbound_from: str = Field(default="", alias="OUTBOUND_FROM")
    outbound_reply_to: str = Field(default="", alias="OUTBOUND_REPLY_TO")
    inbound_imap_host: str = Field(default="", alias="INBOUND_IMAP_HOST")
    inbound_imap_port: int = Field(default=993, alias="INBOUND_IMAP_PORT")
    inbound_imap_user: str = Field(default="", alias="INBOUND_IMAP_USER")
    inbound_imap_pass: SecretStr = Field(default=SecretStr(""), alias="INBOUND_IMAP_PASS")
    inbound_imap_folder: str = Field(default="INBOX", alias="INBOUND_IMAP_FOLDER")

    # --- Agent tuning knobs ---------------------------------------------------
    score_llm_tier: str = Field(
        default="fast", alias="GRABON_SCORE_LLM_TIER",
        description="LLM tier for score node: cheap/fast/smart",
    )
    outreach_llm_tier: str = Field(
        default="fast", alias="GRABON_OUTREACH_LLM_TIER",
        description="LLM tier for outreach node: cheap/fast/smart",
    )
    max_research_retries: int = Field(
        default=1, alias="GRABON_MAX_RESEARCH_RETRIES",
        description="Max retries for research node on missing fields",
    )
    services_list: str = Field(
        default=(
            "performance_marketing,seo,social_media,email_marketing,"
            "influencer,content,affiliate,programmatic,web_design,aso,analytics"
        ),
        alias="GRABON_SERVICES_LIST",
        description="Comma-separated list of Grabon services for scoring",
    )

    def parsed_services(self) -> list[str]:
        return [s.strip() for s in self.services_list.split(",") if s.strip()]

    # --- ICP / Autonomous Discovery ------------------------------------------
    icp_geos: str = Field(
        default="IN",
        alias="GRABON_ICP_GEOS",
        description="Comma-separated ISO-2 country codes, priority order",
    )
    icp_discovery_interval_hours: int = Field(
        default=1, alias="GRABON_ICP_DISCOVERY_INTERVAL_HOURS",
    )
    icp_max_fan_out: int = Field(
        default=5, alias="GRABON_ICP_MAX_FAN_OUT",
        description="Max brands to research per discovery cycle",
    )
    icp_queries: str = Field(
        default=(
            # High-intent: brands running coupon/offer campaigns (GrabOn's core value prop)
            "Indian brand coupon code offers discount site:mysmartprice.com OR site:desidime.com,"
            "D2C brand cashback offers India 2026,"
            "Indian ecommerce brand affiliate program launch,"
            # Category: active D2C spenders by vertical
            "Indian D2C beauty brand running Google Ads 2026,"
            "Indian D2C fashion brand Facebook Instagram ads 2026,"
            "Indian health supplement brand online store,"
            "Indian snack food D2C brand website,"
            "Indian electronics accessories brand own website,"
            "Indian pet care D2C brand India,"
            "Indian home fitness brand ecommerce,"
            # Trigger: funding (means they'll spend on growth)
            "Indian D2C brand raised Series A 2026,"
            "India consumer brand seed funding 2026,"
            "D2C brand raised growth capital India 2026,"
            # Trigger: scaling (need traffic/coupons)
            "Indian D2C brand launched new website 2026,"
            "Indian brand first online store launch 2026,"
            "D2C brand scaling digital marketing India 2026,"
            # Trigger: hiring marketers (signal they're investing in growth)
            "Indian D2C brand hiring performance marketing manager,"
            "ecommerce brand hiring affiliate manager India,"
            # Competitor gap: brands on competitor coupon sites but NOT on GrabOn
            "Indian brand coupons site:coupondunia.in -site:grabon.in,"
            "D2C brand offers site:magicpin.in OR site:cashkaro.com"
        ),
        alias="GRABON_ICP_QUERIES",
        description="Comma-separated discovery seed queries",
    )

    def parsed_icp_geos(self) -> list[str]:
        return [g.strip().upper() for g in self.icp_geos.split(",") if g.strip()]

    def parsed_icp_queries(self) -> list[str]:
        return [q.strip() for q in self.icp_queries.split(",") if q.strip()]

    # --- Langfuse (optional, self-hostable) ---------------------------------
    langfuse_public_key: SecretStr = Field(default=SecretStr(""), alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: SecretStr = Field(default=SecretStr(""), alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="http://localhost:3001", alias="LANGFUSE_HOST")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
