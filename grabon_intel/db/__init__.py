"""Async DB engine + session factory."""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_db_available: bool | None = None
_db_unavailable_since: float = 0.0
_DB_RETRY_AFTER_S = 30.0


async def _quick_port_check() -> bool:
    """Async check if DB port is reachable (< 1.5s)."""
    url = get_settings().database_url
    parsed = urlparse(url.replace("+asyncpg", ""))
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=1.5
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError, ConnectionRefusedError):
        return False


def engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            get_settings().database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            connect_args={"timeout": 5, "command_timeout": 10},
            pool_timeout=5,
        )
    return _engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(engine(), expire_on_commit=False)
    return _sessionmaker


class DatabaseUnavailable(RuntimeError):
    pass


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    global _db_available, _db_unavailable_since
    if _db_available is False:
        if time.monotonic() - _db_unavailable_since < _DB_RETRY_AFTER_S:
            raise DatabaseUnavailable("database previously unreachable (cached)")
        _db_available = None
    if _db_available is None:
        if not await _quick_port_check():
            _db_available = False
            raise DatabaseUnavailable("database port unreachable")
        _db_available = True
    try:
        async with sessionmaker()() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise
    except DatabaseUnavailable:
        raise
    except (OSError, ConnectionRefusedError, TimeoutError, asyncio.TimeoutError) as exc:
        _db_available = False
        _db_unavailable_since = time.monotonic()
        raise DatabaseUnavailable(f"database unreachable: {exc}") from exc
    except Exception as exc:
        if "connect" in type(exc).__name__.lower() or "refused" in str(exc).lower():
            _db_available = False
            _db_unavailable_since = time.monotonic()
            raise DatabaseUnavailable(f"database unreachable: {exc}") from exc
        raise


def reset_db_state() -> None:
    """Allow retrying DB connection after a failure."""
    global _db_available
    _db_available = None
