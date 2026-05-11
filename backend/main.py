import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from backend.database import (
    init_db,
    get_all_assignments,
    set_submitted,
    upsert_assignments,
    log_scrape,
    get_last_scrape,
)
from backend.scraper import get_scraper, shutdown_scraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

REFRESH_INTERVAL = 300  # seconds (5 min)


async def background_refresh():
    """Periodically scrape WebClass in the background."""
    while True:
        try:
            scraper = await get_scraper()
            logger.info("Starting background scrape...")
            assignments = await scraper.scrape()
            if assignments:
                upsert_assignments(assignments)
            log_scrape(len(assignments), scraper.last_error)
            logger.info(f"Scrape complete: {len(assignments)} assignments")
        except Exception as e:
            logger.error(f"Background scrape error: {e}")
            log_scrape(0, str(e))
        await asyncio.sleep(REFRESH_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(background_refresh())
    yield
    task.cancel()
    await shutdown_scraper()


app = FastAPI(title="WebClass Dashboard", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


# ------------------------------------------------------------------
# API routes
# ------------------------------------------------------------------

@app.get("/api/assignments")
def api_assignments():
    return get_all_assignments()


@app.post("/api/assignments/{assignment_id}/submit")
def api_submit(assignment_id: str):
    ok = set_submitted(assignment_id, True)
    if not ok:
        raise HTTPException(404, "Assignment not found")
    return {"ok": True}


@app.post("/api/assignments/{assignment_id}/unsubmit")
def api_unsubmit(assignment_id: str):
    ok = set_submitted(assignment_id, False)
    if not ok:
        raise HTTPException(404, "Assignment not found")
    return {"ok": True}


@app.get("/api/status")
def api_status():
    last = get_last_scrape()
    return {
        "last_scrape": last,
        "refresh_interval_seconds": REFRESH_INTERVAL,
    }


@app.post("/api/refresh")
async def api_refresh():
    """Trigger an immediate scrape."""
    try:
        scraper = await get_scraper()
        assignments = await scraper.scrape()
        if assignments:
            upsert_assignments(assignments)
        log_scrape(len(assignments), scraper.last_error)
        return {"ok": True, "count": len(assignments)}
    except Exception as e:
        raise HTTPException(500, str(e))


# ------------------------------------------------------------------
# Serve frontend
# ------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))
