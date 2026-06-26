"""Thin re-export shim — all implementation lives in app.agent.scrapers."""

from app.engines import ALL_ENGINES, VISIBLE_ENGINES, AUTH_ENGINES, UNAUTH_ENGINES  # noqa: F401
from app.agent.scrapers import (  # noqa: F401
    ENGINES, ENGINE_SCRAPERS,
    run_scrape,
    scrape_google_aio, scrape_google_ai_mode,
    scrape_perplexity, scrape_chatgpt,
    scrape_gemini_web, scrape_claude,
    get_ip_stats, pause_scraping, resume_scraping,
    rotate_serp_proxy, rotate_engine_proxy, reset_proxy_fails,
    _validate_response,
)
from app.agent.scrapers.base import (  # noqa: F401
    _get_proxy, _is_captcha_page, _run_browser_scrape,
    _get_browser_sem, _get_login_gate,
    _HEADLESS, _BROWSER_SCRAPE_TIMEOUT,
    load_google_cookies,
)
