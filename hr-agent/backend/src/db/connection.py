"""Async Postgres engine + Redis connection factories."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings

_settings = get_settings()


def _prepare_asyncpg_url(url: str) -> tuple[str, dict]:
    """Strip libpq-only query params (``sslmode``, ``channel_binding``) that
    asyncpg does not accept and translate them to asyncpg ``connect_args``.

    Managed Postgres (Neon, Supabase, etc.) hand out one connection string
    with ``?sslmode=require&channel_binding=require`` appended. That is valid
    for psycopg (the sync URL) but asyncpg raises
    ``TypeError: connect() got an unexpected keyword argument 'sslmode'``.
    Local docker Postgres has no such params, so this is a no-op there.
    asyncpg negotiates SCRAM channel binding on its own, so we only carry the
    SSL intent across as a plain ``ssl`` flag.
    """
    parts = urlsplit(url)
    if "asyncpg" not in parts.scheme:
        return url, {}

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    connect_args: dict = {}
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)  # asyncpg handles channel binding itself
    if sslmode in {"require", "verify-ca", "verify-full", "prefer", "allow"}:
        connect_args["ssl"] = True
    elif sslmode == "disable":
        connect_args["ssl"] = False

    new_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    return new_url, connect_args


def create_engine() -> AsyncEngine:
    url, connect_args = _prepare_asyncpg_url(_settings.database_url)
    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=5,
        echo=False,
        connect_args=connect_args,
    )


engine: AsyncEngine = create_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Async context manager. Commits on clean exit, rolls back on exception."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


_redis_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(_settings.redis_url, decode_responses=True)
    return _redis_client
