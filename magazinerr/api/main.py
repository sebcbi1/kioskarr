from contextlib import asynccontextmanager

from fastapi import FastAPI

from magazinerr.api import grabs, jobs, publications, review, search
from magazinerr.config import settings
from magazinerr.db import init_db
from magazinerr.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast here rather than booting fine and only discovering a missing
    # credential later as a swallowed exception in the background scheduler.
    settings.require_download_client()
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Magazinerr", lifespan=lifespan)
app.include_router(publications.router)
app.include_router(review.router)
app.include_router(grabs.router)
app.include_router(search.router)
app.include_router(jobs.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
