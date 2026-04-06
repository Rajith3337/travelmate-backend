from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from db.session import engine, init_db
from routers import (
    ai,
    auth,
    checklist,
    expenses,
    itinerary,
    notes,
    photos,
    places,
    settings,
    share,
    stats,
    tracker,
    trips,
)

logger = logging.getLogger("travelmate.startup")

COMPATIBILITY_SQL = [
    # Trips cache/AI columns used by newer backend features.
    "ALTER TABLE trips ADD COLUMN IF NOT EXISTS map_bbox TEXT",
    "ALTER TABLE trips ADD COLUMN IF NOT EXISTS preloaded_facilities TEXT",
    "ALTER TABLE trips ADD COLUMN IF NOT EXISTS ai_roadmap TEXT",
    "ALTER TABLE trips ADD COLUMN IF NOT EXISTS active_route TEXT",
    # Keep large profile fields unbounded for cross-provider compatibility.
    "ALTER TABLE users ALTER COLUMN avatar_url TYPE TEXT",
    "ALTER TABLE users ALTER COLUMN bio TYPE TEXT",
]


async def apply_schema_compatibility() -> None:
    async with engine.begin() as conn:
        for sql in COMPATIBILITY_SQL:
            try:
                await conn.execute(text(sql))
            except Exception as exc:
                logger.warning("Compatibility SQL skipped (%s): %s", sql, exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_ready = False
    last_err = None

    # Max 3 attempts with short delays to avoid blocking startup for too long.
    for attempt in range(1, 4):
        try:
            await init_db()
            db_ready = True
            break
        except Exception as exc:
            last_err = exc
            logger.warning("DB init attempt %s/3 failed: %s", attempt, exc)
            if attempt < 3:
                await asyncio.sleep(0.5 * attempt)

    if not db_ready:
        logger.error("DB not reachable at startup; running in degraded mode: %s", last_err)

    Path("uploads").mkdir(exist_ok=True)

    if db_ready:
        await apply_schema_compatibility()

    yield


app = FastAPI(title="TravelMate API v5", lifespan=lifespan)

frontend_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://10.0.2.2:5173",
    "http://10.0.2.2:5174",
]

extra_origins = os.getenv("FRONTEND_ORIGINS", "").strip()
if extra_origins:
    frontend_origins.extend([o.strip() for o in extra_origins.split(",") if o.strip()])

app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

uploads_path = Path("uploads")
uploads_path.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(trips.router, prefix="/api/v1/trips", tags=["trips"])
app.include_router(places.router, prefix="/api/v1/trips", tags=["places"])
app.include_router(expenses.router, prefix="/api/v1/trips", tags=["expenses"])
app.include_router(photos.router, prefix="/api/v1/trips", tags=["photos"])
app.include_router(itinerary.router, prefix="/api/v1/trips", tags=["itinerary"])
app.include_router(notes.router, prefix="/api/v1/trips", tags=["notes"])
app.include_router(checklist.router, prefix="/api/v1/trips", tags=["checklist"])
app.include_router(ai.router, prefix="/api/v1/ai", tags=["ai"])
app.include_router(share.router, prefix="/api/v1/share", tags=["share"])
app.include_router(stats.router, prefix="/api/v1", tags=["stats"])
app.include_router(tracker.router, prefix="/api/v1/trips", tags=["tracker"])
app.include_router(settings.router, prefix="/api/v1/settings", tags=["settings"])


@app.get("/")
async def root():
    return {"status": "TravelMate API v5 running", "docs": "/docs"}
