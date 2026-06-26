"""
Brand-level response deduplication cache.
If brand X already scraped via "X coupon code" on engine Y,
skip "X promo code" for the same engine within the TTL window.
Uses the response_cache table for persistence across restarts.
"""
import logging
import time

from app.database import run_db

log = logging.getLogger("geo.dedup_cache")

_CACHE_TTL_HOURS = 6
_memory_cache: dict[str, float] = {}


def _cache_key(brand_key: str, engine_name: str) -> str:
    return f"{brand_key}::{engine_name}"


def is_cached(brand_key: str, engine_name: str) -> bool:
    if not brand_key:
        return False
    key = _cache_key(brand_key, engine_name)
    cached_at = _memory_cache.get(key, 0)
    if time.time() - cached_at < _CACHE_TTL_HOURS * 3600:
        return True
    return False


def mark_cached(brand_key: str, engine_name: str):
    if not brand_key:
        return
    key = _cache_key(brand_key, engine_name)
    _memory_cache[key] = time.time()


async def mark_cached_db(brand_key: str, engine_name: str, prompt_id: str):
    """Persist cache entry to DB for cross-restart durability."""
    mark_cached(brand_key, engine_name)

    def _upsert(conn):
        conn.execute(
            """INSERT INTO response_cache (brand_key, engine_name, prompt_id, cached_at)
               VALUES (%s, %s, %s::uuid, NOW())
               ON CONFLICT (brand_key, engine_name) DO UPDATE SET
                   prompt_id = EXCLUDED.prompt_id,
                   cached_at = NOW()""",
            (brand_key, engine_name, prompt_id),
        )
        conn.commit()

    try:
        await run_db(_upsert)
    except Exception as e:
        log.debug(f"Cache DB write failed (non-fatal): {e}")


async def load_cache_from_db():
    """Load recent cache entries from DB into memory on startup."""
    def _load(conn):
        return conn.execute(
            """SELECT brand_key, engine_name, cached_at
               FROM response_cache
               WHERE cached_at >= NOW() - make_interval(hours => %s)""",
            (_CACHE_TTL_HOURS,),
        ).fetchall()

    try:
        rows = await run_db(_load)
        loaded = 0
        for row in rows:
            key = _cache_key(row["brand_key"], row["engine_name"])
            ts = row["cached_at"].timestamp() if hasattr(row["cached_at"], "timestamp") else time.time()
            _memory_cache[key] = ts
            loaded += 1
        if loaded:
            log.info(f"Loaded {loaded} cache entries from DB")
    except Exception as e:
        log.debug(f"Cache DB load failed (non-fatal, table may not exist yet): {e}")


def get_cache_stats() -> dict:
    now = time.time()
    active = sum(1 for ts in _memory_cache.values() if now - ts < _CACHE_TTL_HOURS * 3600)
    return {
        "total_entries": len(_memory_cache),
        "active_entries": active,
        "ttl_hours": _CACHE_TTL_HOURS,
    }


def clear_cache():
    _memory_cache.clear()
    log.info("Dedup cache cleared")
