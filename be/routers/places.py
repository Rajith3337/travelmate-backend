from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.session import get_db
from models.models import Place, Trip, User
from schemas.schemas import PlaceIn, PlaceOut, PlaceUpdate
from services.deps import current_user

router = APIRouter(prefix="/{trip_id}/places")

async def _trip(trip_id: int, u: User, db: AsyncSession) -> Trip:
    r = await db.execute(select(Trip).where(Trip.id == trip_id, Trip.owner_id == u.id))
    t = r.scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Trip not found")
    return t

@router.get("/", response_model=list[PlaceOut])
async def list_places(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r = await db.execute(select(Place).where(Place.trip_id == trip_id).order_by(Place.created_at))
    return r.scalars().all()

@router.post("/", response_model=PlaceOut, status_code=201)
async def create_place(trip_id: int, body: PlaceIn, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    place = Place(**body.model_dump(), trip_id=trip_id)
    db.add(place)
    await db.flush()
    await db.refresh(place)
    return place

@router.patch("/{place_id}", response_model=PlaceOut)
async def update_place(trip_id: int, place_id: int, body: PlaceUpdate, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r     = await db.execute(select(Place).where(Place.id == place_id, Place.trip_id == trip_id))
    place = r.scalar_one_or_none()
    if not place:
        raise HTTPException(404, "Place not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(place, k, v)
    await db.flush()
    await db.refresh(place)
    return place

@router.delete("/{place_id}", status_code=204)
async def delete_place(trip_id: int, place_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r     = await db.execute(select(Place).where(Place.id == place_id, Place.trip_id == trip_id))
    place = r.scalar_one_or_none()
    if not place:
        raise HTTPException(404, "Place not found")
    await db.delete(place)
