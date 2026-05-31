"""FastAPI app factory."""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import get_settings
from ..logging import configure as configure_logging
from .auth import require_api_key
from .routes import (
    analytics,
    approvals,
    blocklist,
    brands,
    chat,
    contacts,
    dossiers,
    export,
    feedback,
    lifecycle,
    notification_center,
    notifications,
    people,
    shares,
    signals,
    similar,
    sse,
    traces,
    webhooks_admin,
    workflows,
)


def create_app() -> FastAPI:
    configure_logging()
    s = get_settings()
    app = FastAPI(
        title="grabon-intel",
        version="0.2.0",
        description="Lead intelligence gateway",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.parsed_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "service": "grabon-intel"}

    @app.get("/me", dependencies=[Depends(require_api_key)])
    async def me() -> dict:
        return {"ok": True}

    deps = [Depends(require_api_key)]
    for r in (
        brands,
        dossiers,
        signals,
        approvals,
        workflows,
        chat,
        sse,
        similar,
        blocklist,
        webhooks_admin,
        feedback,
        analytics,
        shares,
        people,
        export,
        notifications,
        traces,
        contacts,
        notification_center,
        lifecycle,
    ):
        app.include_router(r.router, dependencies=deps)
    # Public (signed token only)
    app.include_router(shares.public_router)
    return app


app = create_app()
