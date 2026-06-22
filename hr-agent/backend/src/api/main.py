"""FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.gzip import GZipMiddleware

from src.api import (
    admin_config,
    agent_status,
    analytics,
    apply,
    candidate_portal,
    candidate_ranking,
    ceo_dashboard,
    dashboard,
    export,
    recruiter_chat,
    hr_dashboard,
    events as events_router,
    meeting_reschedule,
    meetings,
    panel_availability,
    panels,
    role_drafting,
    roles,
    settings as settings_router,
    policy_rules,
    supervisor as supervisor_api,
    talent_search,
    v1_campaigns,
    v1_dashboard,
    webhooks,
    webhooks_email,
    webhooks_inbound_call,
    webhooks_meeting,
    webhooks_sms,
    webhooks_voice,
    webhooks_whatsapp,
)
from src.config import get_settings
from src.services.config_store import run_invalidation_listener
from src.services.mail_ingest import POLLER_STATUS, run_mail_poller
from src.services.queue import close_arq_pool
from src.services.auto_nudge import run_auto_nudge_worker
from src.services.recruiter_nudge_worker import run_recruiter_nudge_worker
from src.services.stall_detector import run_stall_detector
from src.services.webhook_watchdog import run_webhook_watchdog
# [SCRAPE/RETIRED] supervisor engine disabled (superseded by domain_events).
# from src.supervisor.engine import run_supervisor_loop

_settings = get_settings()
logging.basicConfig(
    level=_settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Sentry init is best-effort: a malformed DSN must never crash the app.
# We strip whitespace + reject anything that doesn't look like a valid URL.
def _maybe_init_sentry() -> None:
    raw = (_settings.sentry_dsn or "").strip()
    if not raw or not raw.lower().startswith(("http://", "https://")):
        return
    try:
        sentry_sdk.init(dsn=raw, environment=_settings.app_env, send_default_pii=False)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("sentry init skipped: %s", exc)


_maybe_init_sentry()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Start the IMAP poller as a background task.
    poller = asyncio.create_task(run_mail_poller(), name="mail-poller")
    config_listener = asyncio.create_task(
        run_invalidation_listener(), name="config-invalidate"
    )
    nudge_worker = asyncio.create_task(
        run_recruiter_nudge_worker(), name="recruiter-nudges"
    )
    stall_worker = asyncio.create_task(
        run_stall_detector(), name="stall-detector"
    )
    auto_nudge = asyncio.create_task(
        run_auto_nudge_worker(), name="auto-nudge"
    )
    watchdog = asyncio.create_task(
        run_webhook_watchdog(), name="webhook-watchdog"
    )
    # [SCRAPE/RETIRED] supervisor engine disabled; domain_events replaces it.
    # supervisor = asyncio.create_task(
    #     run_supervisor_loop(), name="supervisor-engine"
    # )
    _all_tasks = (poller, config_listener, nudge_worker, stall_worker, auto_nudge, watchdog)
    try:
        yield
    finally:
        for t in _all_tasks:
            t.cancel()
        for t in _all_tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await close_arq_pool()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if _settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


app = FastAPI(
    title="GrabOn Hiring Agent",
    version="1.0.0",
    description="AI-powered recruitment automation — V1.",
    lifespan=lifespan,
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)

_cors_origin_regex = (
    r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    if _settings.app_env == "production"
    else r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$|^https://[a-z0-9-]+\.ngrok-free\.(dev|app)$|^https://[a-z0-9-]+\.ngrok\.io$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_cors_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Dashboard-Key", "Authorization", "X-Request-ID"],
    expose_headers=["Content-Length", "X-Request-ID"],
)

app.include_router(webhooks.router)
app.include_router(webhooks_voice.router)
app.include_router(webhooks_meeting.router)
app.include_router(webhooks_inbound_call.router)
app.include_router(webhooks_whatsapp.router)
app.include_router(webhooks_sms.router)
app.include_router(webhooks_email.router)
app.include_router(meetings.router)
app.include_router(meeting_reschedule.router)
app.include_router(apply.router)
app.include_router(recruiter_chat.router)
app.include_router(dashboard.router)
app.include_router(dashboard.actions_router)
app.include_router(roles.router)
app.include_router(roles.careers_router)
app.include_router(role_drafting.router)
app.include_router(settings_router.router)
app.include_router(v1_dashboard.router)
app.include_router(v1_campaigns.router)
app.include_router(ceo_dashboard.router)
app.include_router(hr_dashboard.router)
app.include_router(events_router.router)
app.include_router(agent_status.router)
app.include_router(panel_availability.router)
app.include_router(panels.router)
app.include_router(admin_config.router)
app.include_router(talent_search.router)
app.include_router(analytics.router)
app.include_router(export.router)
app.include_router(candidate_ranking.router)
app.include_router(supervisor_api.router)
app.include_router(policy_rules.router)
app.include_router(candidate_portal.router)


@app.get("/healthz", summary="Liveness probe")
async def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok", "env": _settings.app_env})


@app.get("/diagnostics/mail-poller", summary="Mail poller status")
async def mail_poller_diag() -> JSONResponse:
    return JSONResponse(POLLER_STATUS)


@app.get("/diagnostics/auth", summary="Dashboard-key load status (reveals no secret material)")
async def auth_diag() -> JSONResponse:
    from src.api.auth import auth_diagnostic

    return JSONResponse(auth_diagnostic())


@app.get("/diagnostics/circuits", summary="Circuit breaker states for external services")
async def circuit_diag() -> JSONResponse:
    from src.services.circuit_breaker import get_all_states

    states = await get_all_states()
    return JSONResponse([
        {
            "service": s.service,
            "failures": s.failures,
            "is_open": s.is_open,
        }
        for s in states
    ])
