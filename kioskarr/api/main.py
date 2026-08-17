import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from kioskarr.api import grabs, jobs, publications, review, search, settings as settings_api
from kioskarr.app_settings import ensure_app_settings_seeded
from kioskarr.db import SessionLocal, init_db
from kioskarr.scheduler import start_scheduler, stop_scheduler

STATIC_DIR = Path(__file__).parent.parent / "static"

# Without this, every logger.info/warning/exception call anywhere in the app
# (scheduler ticks, failed grabs, duplicate detection, etc.) is silently
# swallowed — nothing configures logging output otherwise, and uvicorn only
# sets up its own "uvicorn.*" loggers, not application code's.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        app_settings = ensure_app_settings_seeded(db)
    finally:
        db.close()
    # Deliberately does NOT fail startup if Prowlarr/qBittorrent credentials are
    # missing — settings are now edited live via the Settings page, which has to
    # be reachable to configure them in the first place. Scheduler ticks and the
    # search-now/import-now endpoints each check for this individually instead.
    start_scheduler(app_settings)
    yield
    stop_scheduler()


app = FastAPI(title="Kioskarr", lifespan=lifespan)
app.include_router(publications.router)
app.include_router(review.router)
app.include_router(grabs.router)
app.include_router(search.router)
app.include_router(jobs.router)
app.include_router(settings_api.router)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# The SPA has no build step / cache-busted filenames, so without this browsers
# can keep serving a stale index.html/app.js/style.css from disk cache after an
# upgrade (confirmed live during development — editing these files had no
# visible effect until the browser was forced to refetch them).
@app.middleware("http")
async def no_cache_for_frontend_assets(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
