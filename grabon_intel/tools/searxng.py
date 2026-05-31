"""SearXNG client with CloudProxy IP rotation.

Routes searches through CloudProxy (auto-scaling proxy VMs on AWS).
When an IP gets blocked, destroys the proxy instance via CloudProxy API
and a fresh one auto-provisions with a new IP.

Two-layer defense:
  1. SearXNG outgoing proxies are kept in sync with CloudProxy IPs
  2. On captcha/block, the offending IP is destroyed immediately and
     SearXNG is restarted with the fresh proxy list
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx

from ..config import get_settings
from ..logging import ErrorCategory, get_logger, record_error
from .base import Tool, ToolError, ToolResult

_log = get_logger("tool.searxng")

_CAPTCHA_MARKERS = ("captcha", "unusual traffic", "blocked", "please verify", "robot")

_last_request_ts: float = 0.0
_MIN_INTERVAL_SECONDS = 4.0
_MAX_RETRIES = 3

_LANG_ROTATION = ["en", "en-US", "en-GB", "en-AU", "en-CA"]

_blocked_engines: dict[str, float] = {}
_BLOCK_DURATION = 300.0

_engine_block_counts: dict[str, int] = {}

_engine_last_used: dict[str, float] = {}
_ENGINE_MIN_SPACING = 8.0

_ip_block_counts: dict[str, int] = {}
_IP_BLOCK_THRESHOLD = 2
_MIN_PROXY_COUNT = 3

_proxy_pool: list[str] = []
_proxy_pool_ts: float = 0.0
_PROXY_POOL_TTL = 60.0
_proxy_lock = asyncio.Lock()
_rotating_idx = 0


def _jitter(base: float) -> float:
    return base + random.uniform(0, base * 0.5)


# ---------------------------------------------------------------------------
# CloudProxy API helpers
# ---------------------------------------------------------------------------

async def _cloudproxy_list_ips() -> list[str]:
    url = get_settings().cloudproxy_url
    if not url:
        return []
    try:
        async with httpx.AsyncClient(timeout=10, verify=False) as c:
            r = await c.get(f"{url.rstrip('/')}/")
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and "ips" in data:
                    return data["ips"]
                if isinstance(data, list):
                    return [p for p in data if isinstance(p, str)]
    except Exception as exc:
        _log.warning("cloudproxy.list_failed", error=str(exc))
    return []


async def _cloudproxy_destroy_ip(ip: str) -> bool:
    url = get_settings().cloudproxy_url
    if not url:
        return False
    try:
        async with httpx.AsyncClient(timeout=15, verify=False) as c:
            r = await c.delete(
                f"{url.rstrip('/')}/destroy",
                params={"ip_address": ip},
            )
            if r.status_code in (200, 202):
                _log.info("cloudproxy.destroyed", ip=ip)
                record_error(
                    category=ErrorCategory.RATE_LIMIT,
                    source="cloudproxy",
                    message=f"Destroyed blocked proxy IP {ip} — fresh instance auto-provisioning",
                )
                return True
            _log.warning("cloudproxy.destroy_failed", ip=ip, status=r.status_code)
    except Exception as exc:
        _log.warning("cloudproxy.destroy_error", ip=ip, error=str(exc))
    return False


async def _cloudproxy_scale_up(target: int) -> bool:
    url = get_settings().cloudproxy_url
    if not url:
        return False
    try:
        async with httpx.AsyncClient(timeout=10, verify=False) as c:
            r = await c.patch(
                f"{url.rstrip('/')}/providers/aws",
                params={"min_scaling": target, "max_scaling": max(target, 5)},
            )
            if r.status_code == 200:
                _log.info("cloudproxy.scaled_up", target=target)
                return True
    except Exception as exc:
        _log.warning("cloudproxy.scale_error", error=str(exc))
    return False


def _extract_ip(proxy_url: str) -> str:
    """Extract bare IP from proxy URL like http://user:pass@1.2.3.4:8899"""
    try:
        at_idx = proxy_url.index("@")
        rest = proxy_url[at_idx + 1:]
        return rest.split(":")[0]
    except (ValueError, IndexError):
        return proxy_url.split("//")[-1].split(":")[0] if "//" in proxy_url else ""


# ---------------------------------------------------------------------------
# Proxy pool management
# ---------------------------------------------------------------------------

async def _refresh_proxy_pool(force: bool = False) -> list[str]:
    """Fetch fresh proxy list from CloudProxy, cache for TTL."""
    global _proxy_pool, _proxy_pool_ts, _rotating_idx

    now = time.monotonic()
    if not force and _proxy_pool and (now - _proxy_pool_ts) < _PROXY_POOL_TTL:
        return _proxy_pool

    async with _proxy_lock:
        if not force and _proxy_pool and (now - _proxy_pool_ts) < _PROXY_POOL_TTL:
            return _proxy_pool

        raw = await _cloudproxy_list_ips()
        if raw:
            _proxy_pool = raw
            _proxy_pool_ts = time.monotonic()
            _rotating_idx = 0
            _log.info("cloudproxy.pool_refreshed", count=len(raw))

            if len(raw) < _MIN_PROXY_COUNT:
                _log.info("cloudproxy.scaling_up", current=len(raw), target=_MIN_PROXY_COUNT)
                await _cloudproxy_scale_up(_MIN_PROXY_COUNT)

        return _proxy_pool


def _next_proxy() -> str | None:
    """Round-robin through available proxies."""
    global _rotating_idx
    if not _proxy_pool:
        return None
    proxy = _proxy_pool[_rotating_idx % len(_proxy_pool)]
    _rotating_idx += 1
    return proxy


async def _rotate_blocked_ip(blocked_proxy: str) -> str | None:
    """Destroy blocked IP, refresh pool, return next available proxy."""
    global _proxy_pool

    ip = _extract_ip(blocked_proxy)
    if ip:
        _log.warning("cloudproxy.rotating_blocked", ip=ip)
        await _cloudproxy_destroy_ip(ip)

    _proxy_pool = [p for p in _proxy_pool if _extract_ip(p) != ip]

    if len(_proxy_pool) < _MIN_PROXY_COUNT:
        await _cloudproxy_scale_up(_MIN_PROXY_COUNT + 1)

    await asyncio.sleep(2)
    await _refresh_proxy_pool(force=True)

    return _next_proxy()


def _proxy_as_httpx_dict(proxy_url: str) -> dict[str, str]:
    """Convert proxy URL to httpx proxy format."""
    return {"http://": proxy_url, "https://": proxy_url}


# ---------------------------------------------------------------------------
# SearXNG settings sync
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# SearXNG Tool
# ---------------------------------------------------------------------------

class SearXNGTool(Tool):
    name = "searxng"

    def __init__(self) -> None:
        super().__init__(rate=1, period=3.0)
        self._initialized = False

    @property
    def available(self) -> bool:
        return bool(get_settings().searxng_url)

    async def _ensure_init(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        if get_settings().cloudproxy_url:
            await _refresh_proxy_pool(force=True)
            _log.info("searxng.init", proxy_count=len(_proxy_pool))

    async def call(
        self,
        *,
        query: str,
        country: str = "IN",
        num: int = 10,
        categories: str = "general",
    ) -> ToolResult:
        global _last_request_ts

        await self._ensure_init()

        now = time.monotonic()
        wait = _MIN_INTERVAL_SECONDS - (now - _last_request_ts)
        if wait > 0:
            await asyncio.sleep(_jitter(wait))
        _last_request_ts = time.monotonic()

        base = get_settings().searxng_url.rstrip("/")
        lang = random.choice(_LANG_ROTATION)

        _clear_expired_blocks()

        _ALL_ENGINES = ["google", "bing", "duckduckgo", "brave", "qwant", "mojeek", "wikipedia"]
        now_mono = time.monotonic()
        active = [
            e for e in _ALL_ENGINES
            if e not in _blocked_engines
            and (now_mono - _engine_last_used.get(e, 0)) >= _ENGINE_MIN_SPACING
        ]
        if not active:
            active = [e for e in _ALL_ENGINES if e not in _blocked_engines]
        if not active:
            _log.warning("searxng.all_engines_blocked", clearing=True)
            _blocked_engines.clear()
            _engine_block_counts.clear()
            active = _ALL_ENGINES
        if len(active) < len(_ALL_ENGINES):
            _log.info("searxng.using_engines", active=active, blocked=list(_blocked_engines.keys()))

        for eng in active:
            _engine_last_used[eng] = now_mono

        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "language": lang,
            "safesearch": 0,
            "categories": categories,
            "pageno": 1,
            "engines": ",".join(active),
        }

        data: dict[str, Any] = {}
        last_err: Exception | None = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=60) as c:
                    r = await c.get(
                        f"{base}/search",
                        params=params,
                        headers={
                            "X-Forwarded-For": f"10.0.{random.randint(1,254)}.{random.randint(1,254)}",
                            "X-Real-IP": f"10.0.{random.randint(1,254)}.{random.randint(1,254)}",
                        },
                    )

                    if r.status_code == 429:
                        _log.warning("searxng.rate_limited", query=query[:60], attempt=attempt)
                        if attempt < _MAX_RETRIES:
                            await asyncio.sleep(_jitter(5.0 * (attempt + 1)))
                            continue
                        record_error(
                            category=ErrorCategory.RATE_LIMIT,
                            source="searxng",
                            message=f"SearXNG 429 after {_MAX_RETRIES + 1} attempts: {query[:80]}",
                        )
                        raise ToolError(f"searxng 429 rate limited: {query[:80]}")

                    if r.status_code >= 400:
                        _log.warning("searxng.http_error", status=r.status_code, query=query[:60])
                        record_error(
                            category=ErrorCategory.API_ERROR,
                            source="searxng",
                            message=f"SearXNG HTTP {r.status_code}",
                            detail=r.text[:300],
                        )
                        raise ToolError(f"searxng {r.status_code}: {r.text[:200]}")

                    data = r.json()
                    break

            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                last_err = exc
                _log.warning("searxng.network_error", error=str(exc), attempt=attempt)

                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_jitter(3.0 * (attempt + 1)))
                    continue
                record_error(
                    category=ErrorCategory.NETWORK,
                    source="searxng",
                    message=f"SearXNG network error after retries: {exc}",
                )
                raise ToolError(f"searxng network error: {exc}") from exc

        if not data and last_err:
            raise ToolError(f"searxng failed: {last_err}")

        results = data.get("results") or []
        unresponsive = data.get("unresponsive_engines") or []

        _should_rotate = False
        for engine_info in unresponsive:
            if isinstance(engine_info, (list, tuple)) and len(engine_info) >= 2:
                engine_name = engine_info[0]
                reason = str(engine_info[1]).lower()
                if "captcha" in reason or "too many" in reason:
                    _block_engine(engine_name)
                    _log.warning("searxng.engine_blocked", engine=engine_name, reason=reason)
                    _should_rotate = True
                elif "access denied" in reason:
                    _block_engine(engine_name)
                    _log.warning("searxng.engine_soft_blocked", engine=engine_name, reason=reason)

        captcha_engines: list[str] = []
        clean_results: list[dict] = []
        for item in results[:num]:
            snippet = (item.get("content") or "").lower()
            title = (item.get("title") or "").lower()
            if any(m in snippet or m in title for m in _CAPTCHA_MARKERS):
                captcha_engines.append(item.get("engine", "unknown"))
            else:
                clean_results.append({
                    "title": item.get("title"),
                    "link": item.get("url"),
                    "snippet": item.get("content"),
                    "engine": item.get("engine"),
                    "score": item.get("score"),
                })

        if captcha_engines:
            for eng in set(captcha_engines):
                _block_engine(eng)
            _log.warning("searxng.captcha_detected", engines=captcha_engines, query=query[:60])
            record_error(
                category=ErrorCategory.CAPTCHA,
                source="searxng",
                message=f"CAPTCHA from engines: {', '.join(set(captcha_engines))}",
                detail=f"Query: {query}",
            )
            _should_rotate = True

        if not clean_results and results:
            _log.warning("searxng.all_results_captcha", query=query[:60])
            record_error(
                category=ErrorCategory.CAPTCHA,
                source="searxng",
                message=f"All {len(results)} results are CAPTCHAs — no usable data",
                detail=f"Query: {query}",
            )

        if _should_rotate:
            _log.warning("searxng.captcha_or_block_detected", blocked_engines=list(_blocked_engines.keys()))

        if not clean_results and not results and attempt >= _MAX_RETRIES:
            return ToolResult(
                name=self.name,
                cost_cents=0,
                data={"query": query, "results": [], "degraded": True},
                degraded=True,
            )

        return ToolResult(
            name=self.name,
            cost_cents=0,
            data={
                "query": query,
                "country": country,
                "results": clean_results,
                "infoboxes": data.get("infoboxes"),
                "suggestions": data.get("suggestions"),
                "captcha_engines": captcha_engines or None,
                "blocked_engines": list(_blocked_engines.keys()) or None,
                "proxy_used": "direct",
            },
        )


def _block_engine(engine: str) -> None:
    """Block engine with exponential backoff — repeat offenders get longer cooldowns."""
    _engine_block_counts[engine] = _engine_block_counts.get(engine, 0) + 1
    repeat = _engine_block_counts[engine]
    duration = min(_BLOCK_DURATION * (2 ** (repeat - 1)), 1800.0)
    _blocked_engines[engine] = time.monotonic() + duration
    _log.info("searxng.engine_blocked_duration", engine=engine, duration_s=duration, repeat=repeat)


def _clear_expired_blocks() -> None:
    now = time.monotonic()
    expired = [k for k, v in _blocked_engines.items() if now >= v]
    for k in expired:
        del _blocked_engines[k]
        if k in _engine_block_counts:
            _engine_block_counts[k] = max(0, _engine_block_counts[k] - 1)
        _log.info("searxng.engine_unblocked", engine=k)
