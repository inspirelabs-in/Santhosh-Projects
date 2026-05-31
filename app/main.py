import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.database import init_schema, close_pool
from app.scheduler import init_scheduler, shutdown_scheduler
from app.routes.dashboard import router as dashboard_router
from app.routes.api import router as api_router
from app.routes.logs import router as logs_router
import asyncio
from app.routes.auth import router as auth_router, cleanup_browsers, _warmup_camoufox

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("geo.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("GEO Agent starting up...")
    await init_schema()
    init_scheduler()
    asyncio.create_task(_warmup_camoufox())
    yield
    shutdown_scheduler()
    await cleanup_browsers()
    await close_pool()
    log.info("GEO Agent shut down")


app = FastAPI(title="GrabOn GEO Agent", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(dashboard_router)
app.include_router(api_router)
app.include_router(logs_router)
app.include_router(auth_router)
