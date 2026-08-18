import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from kioskarr.api import auth as auth_api
from kioskarr.api import grabs, jobs, opds, publications, review, search, settings as settings_api
from kioskarr.api.auth import require_auth, require_auth_or_basic
from kioskarr.app_settings import ensure_app_settings_seeded
from kioskarr.db import SessionLocal, init_db
from kioskarr.scheduler import start_scheduler, stop_scheduler

STATIC_DIR = Path(__file__).parent.parent / "static"

# Without this, every logger.info/warning/exception call anywhere in the app
# (scheduler ticks, failed grabs, duplicate detection, etc.) is silently
# swallowed — nothing configures logging output otherwise, and uvicorn only
# sets up its own "uvicorn.*" loggers, not application code's.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# Needs to happen before the app/middleware are constructed below: SessionMiddleware
# requires a secret_key at registration time, and that key lives in the DB-backed
# AppSettings row (so it survives restarts instead of invalidating every session).
init_db()
_db = SessionLocal()
try:
    _app_settings = ensure_app_settings_seeded(_db)
    _session_secret_key = _app_settings.session_secret_key
finally:
    _db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Deliberately does NOT fail startup if Prowlarr/qBittorrent credentials are
    # missing — settings are now edited live via the Settings page, which has to
    # be reachable to configure them in the first place. Scheduler ticks and the
    # search-now/import-now endpoints each check for this individually instead.
    start_scheduler(_app_settings)
    yield
    stop_scheduler()


app = FastAPI(title="Kioskarr", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret_key,
    session_cookie="kioskarr_session",
    same_site="lax",
    https_only=False,  # flip to True if this ends up behind an HTTPS reverse proxy
)
app.include_router(auth_api.router)
app.include_router(publications.router, dependencies=[Depends(require_auth)])
app.include_router(review.router, dependencies=[Depends(require_auth)])
app.include_router(grabs.router, dependencies=[Depends(require_auth)])
app.include_router(search.router, dependencies=[Depends(require_auth)])
app.include_router(jobs.router, dependencies=[Depends(require_auth)])
app.include_router(settings_api.router, dependencies=[Depends(require_auth)])
# OPDS clients (Komga, Kavita, e-reader apps) are non-browser and can't do the
# session-cookie login flow — this router accepts HTTP Basic too, not just a session.
app.include_router(opds.router, dependencies=[Depends(require_auth_or_basic)])
# Unprotected at the HTTP layer on purpose — every route validates its {token} path
# param against AppSettings.opds_token itself. For clients that can't answer a 401
# Basic Auth challenge at all (just send a bare URL), e.g. Mihon's Kavita extension.
app.include_router(opds.token_router)

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
