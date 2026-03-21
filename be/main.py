from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pathlib import Path
from db.session import init_db
from routers import auth, trips, places, expenses, photos, itinerary, ai, share

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    Path("uploads").mkdir(exist_ok=True)
    yield

app = FastAPI(title="TravelMate API v5", lifespan=lifespan)

# ── CORS — allow any origin during development ──────────────────────────────
# This lets your phone (any local IP) reach the backend.
# For production, replace ["*"] with your actual domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # ← allows all origins (phone, browser, app)
    allow_credentials=False,      # must be False when allow_origins=["*"]
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve uploaded files
uploads_path = Path("uploads")
uploads_path.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# ── Routers ──────────────────────────────────────────────────────────────────
app.include_router(auth.router,      prefix="/api/v1/auth",  tags=["auth"])
app.include_router(trips.router,     prefix="/api/v1/trips", tags=["trips"])
app.include_router(places.router,    prefix="/api/v1/trips", tags=["places"])
app.include_router(expenses.router,  prefix="/api/v1/trips", tags=["expenses"])
app.include_router(photos.router,    prefix="/api/v1/trips", tags=["photos"])
app.include_router(itinerary.router, prefix="/api/v1/trips", tags=["itinerary"])
app.include_router(ai.router,        prefix="/api/v1/ai",    tags=["ai"])
app.include_router(share.router,     prefix="/api/v1/share", tags=["share"])

@app.get("/")
async def root():
    return {"status": "TravelMate API v5 running", "docs": "/docs"}