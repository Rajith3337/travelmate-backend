from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.session import get_db
from models.models import ItineraryDay, Trip, User
from schemas.schemas import ItineraryDayIn, ItineraryDayOut, ItineraryDayUpdate
from services.deps import current_user

router = APIRouter(prefix="/{trip_id}/itinerary")

async def _trip(trip_id: int, u: User, db: AsyncSession) -> Trip:
    r = await db.execute(select(Trip).where(Trip.id == trip_id, Trip.owner_id == u.id))
    t = r.scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Trip not found")
    return t

@router.get("/", response_model=list[ItineraryDayOut])
async def list_days(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r = await db.execute(select(ItineraryDay).where(ItineraryDay.trip_id == trip_id).order_by(ItineraryDay.day_number))
    return r.scalars().all()

@router.post("/", response_model=ItineraryDayOut, status_code=201)
async def create_day(trip_id: int, body: ItineraryDayIn, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    day = ItineraryDay(**body.model_dump(), trip_id=trip_id)
    db.add(day)
    await db.flush()
    await db.refresh(day)
    return day

@router.patch("/{day_id}", response_model=ItineraryDayOut)
async def update_day(trip_id: int, day_id: int, body: ItineraryDayUpdate, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r   = await db.execute(select(ItineraryDay).where(ItineraryDay.id == day_id, ItineraryDay.trip_id == trip_id))
    day = r.scalar_one_or_none()
    if not day:
        raise HTTPException(404, "Day not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(day, k, v)
    await db.flush()
    await db.refresh(day)
    return day

@router.delete("/{day_id}", status_code=204)
async def delete_day(trip_id: int, day_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r   = await db.execute(select(ItineraryDay).where(ItineraryDay.id == day_id, ItineraryDay.trip_id == trip_id))
    day = r.scalar_one_or_none()
    if not day:
        raise HTTPException(404, "Day not found")
    await db.delete(day)
