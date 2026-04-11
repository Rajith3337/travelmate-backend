from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
import asyncio
import json
from db.session import get_db
from models.models import Trip, User
from schemas.schemas import TripIn, TripOut, TripUpdate
from services.deps import current_user
from services.roadmap_precompute import warmup_trip_all_data

router = APIRouter()

async def _delayed_warmup(trip_id: int, force: bool = False):
    """Delay warmup by 3s so the HTTP response is returned first,
    then warm up in the background without blocking the request."""
    await asyncio.sleep(3)
    try:
        await warmup_trip_all_data(trip_id, force)
    except Exception as exc:
        import logging
        logging.getLogger("travelmate.warmup").warning("Warmup failed for trip %s: %s", trip_id, exc)

@router.get("/", response_model=list[TripOut])
async def list_trips(u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Trip).where(Trip.owner_id == u.id).order_by(Trip.created_at.desc()))
    return r.scalars().all()

@router.post("/", response_model=TripOut, status_code=201)
async def create_trip(body: TripIn, background_tasks: BackgroundTasks, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    trip = Trip(**body.model_dump(), owner_id=u.id)
    db.add(trip)
    await db.flush()
    await db.refresh(trip)
    background_tasks.add_task(_delayed_warmup, trip.id, False)
    return trip

@router.get("/{trip_id}", response_model=TripOut)
async def get_trip(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r    = await db.execute(select(Trip).where(Trip.id == trip_id, Trip.owner_id == u.id))
    trip = r.scalar_one_or_none()
    if not trip:
        raise HTTPException(404, "Trip not found")
    return trip

@router.patch("/{trip_id}", response_model=TripOut)
async def update_trip(trip_id: int, body: TripUpdate, background_tasks: BackgroundTasks, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r    = await db.execute(select(Trip).where(Trip.id == trip_id, Trip.owner_id == u.id))
    trip = r.scalar_one_or_none()
    if not trip:
        raise HTTPException(404, "Trip not found")
    data = body.model_dump(exclude_none=True)
    if "places_route" in data and isinstance(data["places_route"], str):
        try:
            data["places_route"] = json.loads(data["places_route"])
        except Exception:
            pass
    for k, v in data.items():
        setattr(trip, k, v)
    await db.flush()
    await db.refresh(trip)
    background_tasks.add_task(_delayed_warmup, trip.id, False)
    return trip

@router.delete("/{trip_id}", status_code=204)
async def delete_trip(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r = await db.execute(delete(Trip).where(Trip.id == trip_id, Trip.owner_id == u.id))
    if r.rowcount == 0:
        raise HTTPException(404, "Trip not found")
    await db.flush()

@router.post("/{trip_id}/duplicate", response_model=TripOut, status_code=201)
async def duplicate_trip(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r    = await db.execute(select(Trip).where(Trip.id == trip_id, Trip.owner_id == u.id))
    trip = r.scalar_one_or_none()
    if not trip:
        raise HTTPException(404, "Trip not found")
    new_trip = Trip(
        owner_id=u.id, name=f"{trip.name} (copy)",
        destination=trip.destination, start_location=trip.start_location,
        description=trip.description, cover_emoji=trip.cover_emoji,
        cover_color=trip.cover_color, status="planning",
        budget=trip.budget, spent=0.0,
    )
    db.add(new_trip)
    await db.flush()
    await db.refresh(new_trip)
    # duplicated trip will be warmed on next login; explicit warmup can be called by client too
    return new_trip
