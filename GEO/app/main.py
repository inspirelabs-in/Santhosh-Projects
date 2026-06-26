import subprocess
import sys
import asyncio
import concurrent.futures
import os
import time
import warnings

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import urllib3
warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from app.database import init_schema, close_pool
from app.scheduler import init_scheduler, shutdown_scheduler
from app.routes.dashboard import router as dashboard_router
from app.routes.api import router as api_router
from app.routes.api_analytics import router as api_analytics_router
from app.routes.api_serp import router as api_serp_router
from app.routes.api_insights import router as api_insights_router
from app.routes.logs import router as logs_router
from app.routes.auth import router as auth_router, cleanup_browsers, warmup_camoufox
from app.routes.verification import router as verification_router
from app.routes.reports import router as reports_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("geo.main")

_CRASH_MARKER = "/tmp/geo_last_crash"
_BOOT_TIME = time.time()

def _kill_orphan_browsers():
    """Kill leftover Camoufox browser processes from previous runs."""
    killed = 0

    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["taskkill", "/f", "/im", "camoufox.exe"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                killed = result.stdout.count("SUCCESS")
        else:
            result = subprocess.run(
                ["pkill", "-f", "camoufox"],
                capture_output=True, timeout=10,
            )
            if result.returncode == 0:
                killed += 1
    except Exception:
        pass

    if killed:
        log.info(f"Orphan browser cleanup: killed orphan camoufox process(es)")
    else:
        log.info("Orphan browser cleanup: no leftover processes found")


async def _detect_crash_recovery():
    """Check if previous instance crashed and send alert."""
    try:
        if os.path.exists(_CRASH_MARKER):
            with open(_CRASH_MARKER) as f:
                crash_ts = f.read().strip()
            os.remove(_CRASH_MARKER)
            from app.notifications import send_alert
            await send_alert(
                "GEO Agent Auto-Recovered",
                f"Application crashed at {crash_ts} and has been automatically restarted by Docker. "
                f"Previous crash was likely caused by browser memory corruption (malloc/tcache).",
                severity="critical",
                ntype="system",
            )
            log.warning(f"Crash recovery detected — previous crash at {crash_ts}")
    except Exception as e:
        log.error(f"Crash recovery detection failed: {e}")


def _write_crash_marker():
    """Write crash timestamp so next boot knows we crashed."""
    try:
        from datetime import datetime
        with open(_CRASH_MARKER, "w") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass


def _remove_crash_marker():
    """Remove crash marker on clean shutdown."""
    try:
        if os.path.exists(_CRASH_MARKER):
            os.remove(_CRASH_MARKER)
    except Exception:
        pass


async def _watchdog_loop():
    """Periodic self-check: verify DB connectivity, event loop health, and engine auto-heal."""
    await asyncio.sleep(60)
    _heal_check_counter = 0
    while True:
        try:
            from app.database import run_db
            t0 = time.time()
            def _ping(conn):
                return conn.execute("SELECT 1").fetchone()
            await asyncio.wait_for(
                run_db(_ping),
                timeout=15,
            )
            elapsed = time.time() - t0
            if elapsed > 5:
                log.warning(f"Watchdog: DB ping slow ({elapsed:.1f}s)")
        except asyncio.TimeoutError:
            log.error("Watchdog: DB ping timed out (15s) — event loop may be stuck")
            from app.notifications import send_alert
            await send_alert(
                "GEO Agent Watchdog Alert",
                "Database ping timed out after 15s. Event loop may be stuck or DB unreachable.",
                severity="critical",
                ntype="system",
            )
        except Exception as e:
            log.error(f"Watchdog check failed: {e}")

        _heal_check_counter += 1
        if _heal_check_counter % 5 == 0:
            try:
                await _watchdog_engine_heal()
            except Exception as e:
                log.error(f"Watchdog engine heal check failed: {e}")

        await asyncio.sleep(60)


async def _watchdog_engine_heal():
    """Check engine error rates and trigger auto-heal for degraded engines."""
    from app.database import run_db
    from app.agent.pipeline import (
        _auto_heal_in_progress, _auto_heal_engine,
        _engine_fail_count, CONSECUTIVE_FAIL_THRESHOLD,
    )
    from app.agent.scraper import VISIBLE_ENGINES

    def _check(conn):
        return conn.execute("""
            SELECT engine_name,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE raw_response_text LIKE 'Error:%%') AS errors
            FROM execution_logs
            WHERE captured_at >= NOW() - INTERVAL '1 hour'
              AND engine_name = ANY(%s)
            GROUP BY engine_name
        """, (list(VISIBLE_ENGINES),)).fetchall()

    rows = await run_db(_check)
    for r in rows:
        eng = r["engine_name"]
        if r["total"] < 5:
            continue
        error_rate = r["errors"] / r["total"]
        if error_rate >= 0.6 and eng not in _auto_heal_in_progress:
            _engine_fail_count[eng] = max(_engine_fail_count.get(eng, 0), CONSECUTIVE_FAIL_THRESHOLD)
            log.warning(f"Watchdog: {eng} has {error_rate:.0%} error rate in last hour — triggering auto-heal")
            asyncio.ensure_future(_auto_heal_engine(eng))


def _handle_unhandled_exception(loop, context):
    msg = context.get("message", "")
    exc = context.get("exception")
    if exc and "Connection closed while reading from the driver" in str(exc):
        log.debug("Browser driver connection closed (non-fatal)")
        return
    if exc:
        import traceback as _tb
        tb_str = ''.join(_tb.format_exception(type(exc), exc, exc.__traceback__)) if exc.__traceback__ else ''
        log.warning(f"Unhandled async exception: {type(exc).__name__}: {exc}\n{tb_str}")
    else:
        log.warning(f"Unhandled async event: {msg}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("GEO Agent starting up...")
    _kill_orphan_browsers()
    loop = asyncio.get_event_loop()
    log.info(f"Event loop type: {type(loop).__name__}")
    loop.set_exception_handler(_handle_unhandled_exception)
    loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=20))
    await init_schema()
    await _detect_crash_recovery()
    _write_crash_marker()
    from app.agent.account_pool import init_all_pools
    await init_all_pools()
    init_scheduler()
    asyncio.create_task(warmup_camoufox())
    watchdog_task = asyncio.create_task(_watchdog_loop())
    yield
    watchdog_task.cancel()
    _remove_crash_marker()
    shutdown_scheduler()
    await cleanup_browsers()
    await close_pool()
    log.info("GEO Agent shut down")


app = FastAPI(title="GrabOn GEO Agent", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=500)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def static_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=86400, immutable"
    return response

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("app/static/logos/google_aio.svg", media_type="image/svg+xml")

app.include_router(dashboard_router)
app.include_router(api_router)
app.include_router(api_analytics_router)
app.include_router(api_serp_router)
app.include_router(api_insights_router)
app.include_router(logs_router)
app.include_router(auth_router)
app.include_router(verification_router)
app.include_router(reports_router)
