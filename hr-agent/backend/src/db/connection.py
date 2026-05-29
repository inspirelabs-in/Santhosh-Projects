"""Async Postgres engine + Redis connection factories."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings

_settings = get_settings()


def create_engine() -> AsyncEngine:
    return create_async_engine(
        _settings.database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=5,
        echo=False,
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
