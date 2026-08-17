from contextlib import asynccontextmanager

from fastapi import FastAPI

from kioskarr.api import grabs, jobs, publications, review, search
from kioskarr.config import settings
from kioskarr.db import init_db
from kioskarr.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast here rather than booting fine and only discovering a missing
    # credential later as a swallowed exception in the background scheduler.
    settings.require_download_client()
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Kioskarr", lifespan=lifespan)
app.include_router(publications.router)
app.include_router(review.router)
app.include_router(grabs.router)
app.include_router(search.router)
app.include_router(jobs.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
