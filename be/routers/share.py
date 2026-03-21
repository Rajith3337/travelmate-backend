import secrets
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.session import get_db
from models.models import Expense, ItineraryDay, Place, ShareToken, Trip, User
from schemas.schemas import ShareOut
from services.deps import current_user

router = APIRouter()

@router.post("/trips/{trip_id}", response_model=ShareOut)
async def create_share(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r    = await db.execute(select(Trip).where(Trip.id == trip_id, Trip.owner_id == u.id))
    trip = r.scalar_one_or_none()
    if not trip:
        raise HTTPException(404, "Trip not found")
    ex    = await db.execute(select(ShareToken).where(ShareToken.trip_id == trip_id, ShareToken.owner_id == u.id))
    share = ex.scalar_one_or_none()
    if not share:
        share = ShareToken(token=secrets.token_urlsafe(32), trip_id=trip_id, owner_id=u.id)
        db.add(share)
        await db.flush()
        await db.refresh(share)
    return ShareOut(token=share.token, share_url=f"/api/v1/share/view/{share.token}", trip_name=trip.name)

@router.delete("/trips/{trip_id}", status_code=204)
async def revoke_share(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r     = await db.execute(select(ShareToken).where(ShareToken.trip_id == trip_id, ShareToken.owner_id == u.id))
    share = r.scalar_one_or_none()
    if share:
        await db.delete(share)

@router.get("/view/{token}")
async def view_shared(token: str, db: AsyncSession = Depends(get_db)):
    r     = await db.execute(select(ShareToken).where(ShareToken.token == token))
    share = r.scalar_one_or_none()
    if not share:
        raise HTTPException(404, "Share link not found or revoked")
    tr   = await db.execute(select(Trip).where(Trip.id == share.trip_id))
    trip = tr.scalar_one_or_none()
    if not trip:
        raise HTTPException(404, "Trip not found")
    places   = (await db.execute(select(Place).where(Place.trip_id == trip.id))).scalars().all()
    expenses = (await db.execute(select(Expense).where(Expense.trip_id == trip.id))).scalars().all()
    days     = (await db.execute(select(ItineraryDay).where(ItineraryDay.trip_id == trip.id).order_by(ItineraryDay.day_number))).scalars().all()
    return {
        "trip": {
            "name": trip.name, "destination": trip.destination,
            "description": trip.description, "cover_emoji": trip.cover_emoji,
            "cover_color": trip.cover_color, "start_date": trip.start_date,
            "end_date": trip.end_date, "status": trip.status,
            "budget": trip.budget, "spent": trip.spent, "progress": trip.progress,
        },
        "places": [
            {"name": p.name, "place_type": p.place_type, "address": p.address,
             "latitude": p.latitude, "longitude": p.longitude,
             "status": p.status, "rating": p.rating, "visit_time": p.visit_time}
            for p in places
        ],
        "itinerary": [
            {"day_number": d.day_number, "date_label": d.date_label,
             "title": d.title, "notes": d.notes, "places_list": d.places_list}
            for d in days
        ],
        "expense_summary": {"total": sum(e.amount for e in expenses), "count": len(expenses)},
    }
