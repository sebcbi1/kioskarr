import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from kioskarr.api import grabs, jobs, publications, review, search, settings as settings_api
from kioskarr.app_settings import ensure_app_settings_seeded
from kioskarr.db import SessionLocal, init_db
from kioskarr.scheduler import start_scheduler, stop_scheduler
from kioskarr.templating import STATIC_DIR
from kioskarr.ui import grabs as ui_grabs
from kioskarr.ui import publications as ui_publications
from kioskarr.ui import review as ui_review
from kioskarr.ui import settings as ui_settings

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

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(ui_publications.router)
app.include_router(ui_review.router)
app.include_router(ui_grabs.router)
app.include_router(ui_settings.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/publications")
