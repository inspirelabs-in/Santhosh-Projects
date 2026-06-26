"""
Multi-account cookie rotation pool.
Manages N storage states per engine, round-robin with rate-limit auto-switch.
DB keys: {engine}_cookies (default/slot 0), {engine}_cookies_1, {engine}_cookies_2, etc.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field

from app.database import run_db

log = logging.getLogger("geo.account_pool")

_COOLDOWN_ON_RATE_LIMIT = 1800  # 30 min default per account on rate limit
_COOLDOWN_ON_AUTH_FAIL = 300    # 5 min on auth failure (reactive refresh usually fixes it)

_ENGINE_COOLDOWNS: dict[str, int] = {
    "claude": 18000,     # 5 hours — Claude free plan resets on 5h rolling window
    "chatgpt": 3600,     # 1 hour
    "perplexity": 1800,  # 30 min
    "gemini": 1800,      # 30 min
    "google": 1800,      # 30 min
}


def _get_cooldown(engine_name: str) -> int:
    return _ENGINE_COOLDOWNS.get(engine_name, _COOLDOWN_ON_RATE_LIMIT)


@dataclass
class AccountSlot:
    db_key: str
    slot_index: int
    cooldown_until: float = 0.0
    consecutive_fails: int = 0
    total_requests: int = 0
    last_used: float = 0.0
    account_label: str = ""
    last_health_check: float = 0.0
    health_status: str = "unknown"


@dataclass
class EnginePool:
    engine_name: str
    slots: list[AccountSlot] = field(default_factory=list)
    _current_index: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def _next_available(self) -> AccountSlot | None:
        now = time.time()
        n = len(self.slots)
        for offset in range(n):
            idx = (self._current_index + offset) % n
            slot = self.slots[idx]
            if now >= slot.cooldown_until:
                self._current_index = (idx + 1) % n
                return slot
        return None


_pools: dict[str, EnginePool] = {}
_initialized = False


def _db_keys_for_engine(engine_name: str, max_slots: int = 5) -> list[str]:
    base = f"{engine_name}_cookies"
    keys = [base]
    for i in range(1, max_slots):
        keys.append(f"{base}_{i}")
    return keys


async def init_pool(engine_name: str, max_slots: int = 5):
    """Discover available account slots from DB for an engine."""
    db_keys = _db_keys_for_engine(engine_name, max_slots)

    def _check(conn):
        found = []
        for key in db_keys:
            row = conn.execute(
                "SELECT key FROM app_settings WHERE key = %s",
                (key,),
            ).fetchone()
            if row:
                found.append(key)
        return found

    found_keys = await run_db(_check)

    if not found_keys:
        _pools[engine_name] = EnginePool(
            engine_name=engine_name,
            slots=[AccountSlot(db_key=db_keys[0], slot_index=0)],
        )
        log.info(f"[pool:{engine_name}] No accounts found, using default slot")
        return

    slots = [AccountSlot(db_key=k, slot_index=i) for i, k in enumerate(found_keys)]
    _pools[engine_name] = EnginePool(engine_name=engine_name, slots=slots)
    log.info(f"[pool:{engine_name}] Initialized {len(slots)} account(s): {found_keys}")


async def init_all_pools():
    """Initialize pools for all engines that use auth."""
    global _initialized
    if _initialized:
        return
    auth_engines = ["google", "chatgpt", "claude", "gemini", "perplexity"]
    for eng in auth_engines:
        await init_pool(eng)
    _initialized = True
    log.info("All account pools initialized")


async def get_storage_state(engine_name: str) -> tuple[dict | None, str]:
    """Get next available storage state for engine.

    Returns (state_dict, db_key) or (None, db_key) if no state stored.
    """
    if engine_name not in _pools:
        await init_pool(engine_name)

    pool = _pools[engine_name]
    async with pool._lock:
        slot = pool._next_available()

    if not slot:
        log.warning(f"[pool:{engine_name}] All accounts on cooldown")
        return None, ""

    def _load(conn):
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = %s",
            (slot.db_key,),
        ).fetchone()
        return row["value"] if row else None

    state = await run_db(_load)
    slot.last_used = time.time()
    slot.total_requests += 1

    if not state or not isinstance(state, dict):
        return None, slot.db_key

    return state, slot.db_key


async def get_cookies(engine_name: str) -> tuple[list[dict] | None, str]:
    """Get cookies array from next available account slot."""
    state, db_key = await get_storage_state(engine_name)
    if not state:
        return None, db_key
    return state.get("cookies", []), db_key


def _resolve_pool(engine_name: str) -> EnginePool | None:
    pool = _pools.get(engine_name)
    if not pool and engine_name in ("google_aio", "google_ai_mode"):
        pool = _pools.get("google")
    return pool


def report_success(engine_name: str, db_key: str):
    """Report successful scrape for a slot."""
    pool = _resolve_pool(engine_name)
    if not pool:
        return
    for slot in pool.slots:
        if slot.db_key == db_key:
            slot.consecutive_fails = 0
            break


def report_rate_limit(engine_name: str, db_key: str):
    """Put slot on cooldown after rate limit. Next call uses different account."""
    pool = _resolve_pool(engine_name)
    if not pool:
        return
    cooldown = _get_cooldown(engine_name)
    for slot in pool.slots:
        if slot.db_key == db_key:
            slot.consecutive_fails += 1
            slot.cooldown_until = time.time() + cooldown
            log.warning(
                f"[pool:{engine_name}] Slot {slot.slot_index} ({db_key}) rate-limited, "
                f"cooldown {cooldown}s ({cooldown // 60}min)"
            )
            break


def report_auth_failure(engine_name: str, db_key: str):
    """Put slot on longer cooldown after auth failure."""
    pool = _resolve_pool(engine_name)
    if not pool:
        return
    for slot in pool.slots:
        if slot.db_key == db_key:
            slot.consecutive_fails += 1
            slot.cooldown_until = time.time() + _COOLDOWN_ON_AUTH_FAIL
            log.warning(
                f"[pool:{engine_name}] Slot {slot.slot_index} ({db_key}) auth failed, "
                f"cooldown {_COOLDOWN_ON_AUTH_FAIL}s"
            )
            break


def clear_cooldown(engine_name: str, db_key: str | None = None):
    """Clear cooldown for a specific slot or all slots of an engine."""
    pool = _resolve_pool(engine_name)
    if not pool:
        return
    for slot in pool.slots:
        if db_key is None or slot.db_key == db_key:
            slot.cooldown_until = 0
            slot.consecutive_fails = 0
    log.info(f"[pool:{engine_name}] Cooldown cleared for {db_key or 'all slots'}")


def has_available_slot(engine_name: str) -> bool:
    """Check if engine has at least one non-cooldown slot right now."""
    pool = _resolve_pool(engine_name)
    if not pool:
        return False
    now = time.time()
    return any(now >= s.cooldown_until for s in pool.slots)


def time_until_next_available(engine_name: str) -> float:
    """Seconds until the soonest account slot recovers. 0 if one is available now."""
    pool = _resolve_pool(engine_name)
    if not pool or not pool.slots:
        return 0
    now = time.time()
    soonest = min(s.cooldown_until for s in pool.slots)
    return max(0, soonest - now)


def get_pool_status() -> dict:
    """Return status of all pools for API/dashboard."""
    now = time.time()
    result = {}
    for eng, pool in _pools.items():
        slots_info = []
        for slot in pool.slots:
            remaining = max(0, slot.cooldown_until - now)
            slots_info.append({
                "db_key": slot.db_key,
                "slot_index": slot.slot_index,
                "available": remaining == 0,
                "cooldown_remaining": int(remaining),
                "consecutive_fails": slot.consecutive_fails,
                "total_requests": slot.total_requests,
                "account_label": slot.account_label,
                "health_status": slot.health_status,
                "last_used": int(now - slot.last_used) if slot.last_used > 0 else None,
            })
        available_count = sum(1 for s in slots_info if s["available"])
        result[eng] = {
            "total_slots": len(slots_info),
            "available_slots": available_count,
            "slots": slots_info,
        }
    return result


async def get_full_account_status() -> dict:
    """Merged view: pool status + cookie health + account labels per engine per slot."""
    import time as _time

    now = _time.time()
    pool_status = get_pool_status()

    session_keys = {
        "chatgpt": ["__Secure-next-auth.session-token", "_account"],
        "claude": ["sessionKey", "lastActiveOrg"],
        "perplexity": ["next-auth.session-token", "__Secure-next-auth.session-token"],
        "gemini": ["SID", "__Secure-1PSID"],
        "google": ["SID", "__Secure-1PSID", "HSID"],
    }
    domain_filters = {
        "chatgpt": ["openai", "chatgpt", "google"],
        "claude": ["claude"],
        "perplexity": ["perplexity"],
        "gemini": ["google"],
        "google": ["google"],
    }

    all_db_keys = []
    for eng, info in pool_status.items():
        for s in info["slots"]:
            all_db_keys.append(s["db_key"])

    def _fetch_all(conn):
        if not all_db_keys:
            return {}
        placeholders = ",".join(["%s"] * len(all_db_keys))
        rows = conn.execute(
            f"SELECT key, value, updated_at::text as updated_at FROM app_settings WHERE key IN ({placeholders})",
            tuple(all_db_keys),
        ).fetchall()
        return {r["key"]: {"value": r["value"], "updated_at": r["updated_at"]} for r in rows}

    db_data = await run_db(_fetch_all)

    result = {}
    for eng, info in pool_status.items():
        engine_slots = []
        for slot_info in info["slots"]:
            db_key = slot_info["db_key"]
            entry = db_data.get(db_key)

            cookie_count = 0
            expired_count = 0
            has_session = False
            updated_at = None
            health = "missing"

            domains = {}
            session_cookie_names = []
            earliest_expiry = None
            total_cookies_raw = 0

            if entry:
                updated_at = entry["updated_at"]
                state = entry["value"]
                cookie_list = state.get("cookies", []) if isinstance(state, dict) else []
                total_cookies_raw = len(cookie_list)
                filters = domain_filters.get(eng, [eng])
                matched = [c for c in cookie_list if any(f in c.get("domain", "") for f in filters)]
                cookie_count = len(matched)

                critical = session_keys.get(eng, [])
                has_session = any(c.get("name") in critical for c in matched)
                if not has_session and eng == "chatgpt":
                    has_session = any("chatgpt" in c.get("domain", "") for c in matched)
                session_cookie_names = [c["name"] for c in matched if c.get("name") in critical]
                expired_count = sum(1 for c in matched if c.get("expires", -1) > 0 and c["expires"] < now)

                for c in matched:
                    d = c.get("domain", "unknown")
                    domains[d] = domains.get(d, 0) + 1
                    exp = c.get("expires", -1)
                    if exp > 0 and exp > now:
                        if earliest_expiry is None or exp < earliest_expiry:
                            earliest_expiry = exp

                too_many_expired = cookie_count > 0 and expired_count > cookie_count * 0.5
                if has_session and not too_many_expired:
                    health = "healthy"
                elif has_session:
                    health = "degraded"
                elif cookie_count > 0:
                    health = "stale"

            pool = _pools.get(eng)
            if pool:
                for ps in pool.slots:
                    if ps.db_key == db_key:
                        ps.health_status = health
                        break

            slot_info["cookie_health"] = {
                "status": health,
                "cookie_count": cookie_count,
                "total_cookies_raw": total_cookies_raw,
                "expired_count": expired_count,
                "has_session_cookie": has_session,
                "session_cookie_names": session_cookie_names,
                "domains": domains,
                "earliest_expiry": earliest_expiry,
                "updated_at": updated_at,
            }
            engine_slots.append(slot_info)

        all_healthy = all(s["cookie_health"]["status"] == "healthy" for s in engine_slots)
        any_healthy = any(s["cookie_health"]["status"] == "healthy" for s in engine_slots)

        result[eng] = {
            "total_slots": info["total_slots"],
            "available_slots": info["available_slots"],
            "overall_health": "healthy" if all_healthy else ("partial" if any_healthy else "unhealthy"),
            "slots": engine_slots,
        }

    return result


def update_slot_label(engine_name: str, slot_index: int, label: str):
    """Set account label (email) for a slot."""
    pool = _pools.get(engine_name)
    if not pool:
        return
    for slot in pool.slots:
        if slot.slot_index == slot_index:
            slot.account_label = label
            break


async def save_to_slot(engine_name: str, slot_index: int, state_json: str):
    """Save storage state to a specific slot. Creates the key if needed."""
    db_keys = _db_keys_for_engine(engine_name)
    if slot_index >= len(db_keys):
        raise ValueError(f"Slot index {slot_index} exceeds max slots")
    db_key = db_keys[slot_index]

    def _save(conn):
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (%s, %s::jsonb, NOW())
               ON CONFLICT (key) DO UPDATE SET value = %s::jsonb, updated_at = NOW()""",
            (db_key, state_json, state_json),
        )
        conn.commit()

    await run_db(_save)
    log.info(f"[pool:{engine_name}] Saved state to slot {slot_index} ({db_key})")

    # Re-init pool to pick up new slot
    await init_pool(engine_name)
