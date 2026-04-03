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
    r = await db.execute(select(Place).where(Place.trip_id == trip_id).order_by(Place.order_idx.asc(), Place.created_at.asc()))
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

import httpx

@router.post("/optimize", response_model=list[PlaceOut])
async def optimize_route(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r = await db.execute(select(Place).where(Place.trip_id == trip_id, Place.latitude.is_not(None), Place.longitude.is_not(None)).order_by(Place.order_idx.asc(), Place.created_at.asc()))
    places = r.scalars().all()
    
    if len(places) < 3:
        raise HTTPException(400, "Need at least 3 mapped places to optimize route")
        
    # OSRM expects coordinates in lon,lat format separated by semicolon
    coords = ";".join([f"{p.longitude},{p.latitude}" for p in places])
    url = f"http://router.project-osrm.org/trip/v1/driving/{coords}?roundtrip=false&source=first&destination=last"
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=10.0)
        
    if resp.status_code != 200:
        raise HTTPException(500, f"Optimization failed: {resp.text}")
        
    data = resp.json()
    if data.get("code") != "Ok":
        raise HTTPException(400, "Could not compute optimal route.")
        
    waypoints = data["waypoints"]
    for wp in waypoints:
        orig = wp["original_index"]
        new_order = wp["waypoint_index"]
        places[orig].order_idx = new_order
        
    await db.flush()
    # Return all places newly sorted
    all_r = await db.execute(select(Place).where(Place.trip_id == trip_id).order_by(Place.order_idx.asc(), Place.created_at.asc()))
    return all_r.scalars().all()
