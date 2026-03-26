from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.session import get_db
from models.models import Trip, User
from schemas.schemas import TripIn, TripOut, TripUpdate
from services.deps import current_user
from services.preload import preload_trip_data

router = APIRouter()

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
    background_tasks.add_task(preload_trip_data, trip.id)
    return trip

@router.get("/{trip_id}", response_model=TripOut)
async def get_trip(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r    = await db.execute(select(Trip).where(Trip.id == trip_id, Trip.owner_id == u.id))
    trip = r.scalar_one_or_none()
    if not trip:
        raise HTTPException(404, "Trip not found")
    return trip

@router.patch("/{trip_id}", response_model=TripOut)
async def update_trip(trip_id: int, body: TripUpdate, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r    = await db.execute(select(Trip).where(Trip.id == trip_id, Trip.owner_id == u.id))
    trip = r.scalar_one_or_none()
    if not trip:
        raise HTTPException(404, "Trip not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(trip, k, v)
    await db.flush()
    await db.refresh(trip)
    return trip

@router.delete("/{trip_id}", status_code=204)
async def delete_trip(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r    = await db.execute(select(Trip).where(Trip.id == trip_id, Trip.owner_id == u.id))
    trip = r.scalar_one_or_none()
    if not trip:
        raise HTTPException(404, "Trip not found")
    await db.delete(trip)

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
    return new_trip
