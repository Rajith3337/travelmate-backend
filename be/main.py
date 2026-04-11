from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from db.session import engine, init_db, describe_database, mark_db_connected_logged
from routers import (
    ai,
    auth,
    checklist,
    expenses,
    itinerary,
    notes,
    places,
    settings,
    share,
    stats,
    tracker,
    trips,
    weather,
)

logger = logging.getLogger("travelmate.startup")


def _db_error_detail(exc: Exception) -> str:
    msg = str(exc or "").strip()
    lower = msg.lower()
    if "exceeded the data transfer quota" in lower:
        return (
            "Database unavailable: your hosted Postgres has exceeded its data transfer quota. "
            "Upgrade the DB plan or switch `DATABASE_URL` to a local database for development."
        )
    if "password authentication failed" in lower:
        return "Database auth failed: check `DATABASE_URL` username/password."
    if "could not translate host name" in lower or "name or service not known" in lower:
        return "Database host not reachable: check `DATABASE_URL` host/network."
    if "timeout" in lower:
        return "Database connection timed out: check your network/DB status and try again."
    return "Database unavailable: check `DATABASE_URL` and database status."

COMPATIBILITY_SQL = [
    # Trips cache/AI columns used by newer backend features.
    "ALTER TABLE trips ADD COLUMN IF NOT EXISTS map_bbox TEXT",
    "ALTER TABLE trips ADD COLUMN IF NOT EXISTS preloaded_facilities TEXT",
    "ALTER TABLE trips ADD COLUMN IF NOT EXISTS ai_roadmap TEXT",
    "ALTER TABLE trips ADD COLUMN IF NOT EXISTS active_route TEXT",
    "ALTER TABLE trips ADD COLUMN IF NOT EXISTS places_route TEXT",
    # Keep large profile fields unbounded for cross-provider compatibility.
    "ALTER TABLE users ALTER COLUMN avatar_url TYPE TEXT",
    "ALTER TABLE users ALTER COLUMN bio TYPE TEXT",
]


async def apply_schema_compatibility() -> None:
    # These statements are written for Postgres. For local SQLite (dev fallback),
    # avoid noisy errors and apply only what SQLite can support.
    dialect = getattr(getattr(engine, "dialect", None), "name", "") or ""
    if dialect.lower() == "sqlite":
        # If the SQLite DB is brand new, tables/columns come from models and this is unnecessary.
        # If it's older, we can still add missing columns via PRAGMA + ALTER TABLE ADD COLUMN.
        try:
            async with engine.begin() as conn:
                def _colnames(rows) -> set[str]:
                    out: set[str] = set()
                    for r in rows or []:
                        try:
                            m = r._mapping  # SQLAlchemy Row
                            name = m.get("name")
                        except Exception:
                            name = None
                        if not name and len(r) > 1:
                            name = r[1]
                        if name:
                            out.add(str(name))
                    return out

                async def _ensure_text_cols(table: str, cols: list[str]) -> None:
                    # PRAGMA table_info returns empty if table doesn't exist yet.
                    res = await conn.execute(text(f"PRAGMA table_info('{table}')"))
                    existing = _colnames(res.fetchall())
                    for c in cols:
                        if c in existing:
                            continue
                        try:
                            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {c} TEXT"))
                        except Exception:
                            # If SQLite version doesn't allow it or table missing, ignore.
                            pass

                await _ensure_text_cols("trips", ["map_bbox", "preloaded_facilities", "ai_roadmap", "active_route", "places_route"])
                await _ensure_text_cols("users", ["avatar_url", "bio"])
        except Exception:
            # Keep startup resilient in degraded/offline mode.
            pass
        return

    for sql in COMPATIBILITY_SQL:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(sql))
        except Exception as exc:
            logger.warning("Compatibility SQL skipped (%s): %s", sql, str(exc).split('\n')[0])


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_ready = False
    last_err = None
    startup_timeout = float(os.getenv("STARTUP_DB_TIMEOUT", "30"))
    startup_retries = int(os.getenv("STARTUP_DB_RETRIES", "3"))
    startup_backoff = float(os.getenv("STARTUP_DB_BACKOFF", "2"))
    apply_schema_on_startup = os.getenv("APPLY_SCHEMA_ON_STARTUP", "1").strip() != "0"

    # Bounded attempts with timeouts to avoid blocking startup for too long.
    for attempt in range(1, startup_retries + 1):
        try:
            await asyncio.wait_for(init_db(), timeout=startup_timeout)
            db_ready = True
            break
        except Exception as exc:
            last_err = exc
            logger.warning(
                "DB init attempt %s/%s failed: %s: %s",
                attempt,
                startup_retries,
                type(exc).__name__,
                str(exc).strip() or "(no message)",
            )
            if attempt < startup_retries:
                await asyncio.sleep(startup_backoff * attempt)

    if not db_ready:
        logger.error("DB not reachable at startup; running in degraded mode: %s", last_err)
    else:
        db_info = describe_database()
        logger.info("DB connected: %s (%s)", db_info["provider"], db_info["host"])
        mark_db_connected_logged()

    Path("uploads").mkdir(exist_ok=True)

    if db_ready and apply_schema_on_startup:
        # Run compatibility migrations in background to reduce startup latency.
        asyncio.create_task(apply_schema_compatibility())

    yield


app = FastAPI(title="TravelMate API v5", lifespan=lifespan)

try:
    import asyncpg  # type: ignore
    _POSTGRES_ERROR = asyncpg.PostgresError
except Exception:
    _POSTGRES_ERROR = ()


@app.exception_handler(DBAPIError)
async def handle_dbapi_error(request: Request, exc: DBAPIError):
    # Prefer a clean 503 over a stack trace when the DB is unreachable/quota-limited.
    detail = _db_error_detail(getattr(exc, "__cause__", None) or exc)
    lower = str(getattr(exc, "__cause__", None) or exc).lower()
    is_unavailable = (
        getattr(exc, "connection_invalidated", False)
        or "exceeded the data transfer quota" in lower
        or "could not connect" in lower
        or "connection refused" in lower
        or "timeout" in lower
    )
    return JSONResponse(status_code=503 if is_unavailable else 500, content={"detail": detail})


@app.exception_handler(TimeoutError)
async def handle_timeout_error(request: Request, exc: TimeoutError):
    # asyncpg (and other I/O) can raise bare TimeoutError/asyncio.TimeoutError.
    return JSONResponse(status_code=503, content={"detail": _db_error_detail(exc)})


if _POSTGRES_ERROR:
    @app.exception_handler(_POSTGRES_ERROR)  # type: ignore[arg-type]
    async def handle_postgres_error(request: Request, exc: Exception):
        return JSONResponse(status_code=503, content={"detail": _db_error_detail(exc)})

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

origin_regex = os.getenv("FRONTEND_ORIGIN_REGEX", "").strip()
if not origin_regex:
    # Allow any http(s) origin in dev to prevent CORS blocks on dynamic LAN IPs.
    origin_regex = r"https?://.*"

app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_origin_regex=origin_regex,
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
app.include_router(itinerary.router, prefix="/api/v1/trips", tags=["itinerary"])
app.include_router(notes.router, prefix="/api/v1/trips", tags=["notes"])
app.include_router(checklist.router, prefix="/api/v1/trips", tags=["checklist"])
app.include_router(ai.router, prefix="/api/v1/ai", tags=["ai"])
app.include_router(weather.router, prefix="/api/v1/weather", tags=["weather"])
app.include_router(share.router, prefix="/api/v1/share", tags=["share"])
app.include_router(stats.router, prefix="/api/v1", tags=["stats"])
app.include_router(tracker.router, prefix="/api/v1/trips", tags=["tracker"])
app.include_router(settings.router, prefix="/api/v1/settings", tags=["settings"])


@app.get("/")
async def root():
    return {"status": "TravelMate API v5 running", "docs": "/docs"}

@app.get('/health')
@app.get('/api/v1/health')
async def health():
    from sqlalchemy import text as _t
    try:
        async with engine.connect() as conn:
            await conn.execute(_t('SELECT 1'))
        return {'status': 'ok', 'db': 'connected'}
    except Exception as e:
        return {'status': 'degraded', 'db': str(e)[:120]}