from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pathlib import Path
from db.session import init_db
from routers import auth, trips, places, expenses, photos, itinerary, ai, share, notes, checklist, stats

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    Path("uploads").mkdir(exist_ok=True)
    yield

app = FastAPI(title="TravelMate API v5", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

uploads_path = Path("uploads")
uploads_path.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

app.include_router(auth.router,       prefix="/api/v1/auth",  tags=["auth"])
app.include_router(trips.router,      prefix="/api/v1/trips", tags=["trips"])
app.include_router(places.router,     prefix="/api/v1/trips", tags=["places"])
app.include_router(expenses.router,   prefix="/api/v1/trips", tags=["expenses"])
app.include_router(photos.router,     prefix="/api/v1/trips", tags=["photos"])
app.include_router(itinerary.router,  prefix="/api/v1/trips", tags=["itinerary"])
app.include_router(notes.router,      prefix="/api/v1/trips", tags=["notes"])
app.include_router(checklist.router,  prefix="/api/v1/trips", tags=["checklist"])
app.include_router(ai.router,         prefix="/api/v1/ai",    tags=["ai"])
app.include_router(stats.router,      prefix="/api/v1",       tags=["stats"])

@app.get("/")
async def root():
    return {"status": "TravelMate API v5 running", "docs": "/docs"}
