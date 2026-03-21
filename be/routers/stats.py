from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from db.session import get_db
from models.models import Expense, Photo, Place, Trip, User
from services.deps import current_user

router = APIRouter()

@router.get("/stats", tags=["stats"])
async def user_stats(u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    trips_r = await db.execute(select(Trip).where(Trip.owner_id == u.id))
    trips = trips_r.scalars().all()
    trip_ids = [t.id for t in trips]

    places = []
    expenses = []
    photos_count = 0

    if trip_ids:
        pl_r = await db.execute(select(Place).where(Place.trip_id.in_(trip_ids)))
        places = pl_r.scalars().all()
        ex_r = await db.execute(select(Expense).where(Expense.trip_id.in_(trip_ids)))
        expenses = ex_r.scalars().all()
        ph_r = await db.execute(select(func.count(Photo.id)).where(Photo.trip_id.in_(trip_ids)))
        photos_count = ph_r.scalar() or 0

    # Extract unique countries/destinations
    destinations = list({t.destination.split(",")[-1].strip() for t in trips if t.destination})

    total_spent = sum(e.amount for e in expenses)
    total_budget = sum(t.budget for t in trips)

    # Expense breakdown by category
    by_category: dict[str, float] = {}
    for e in expenses:
        by_category[e.category] = by_category.get(e.category, 0) + e.amount

    # Monthly spending trend (last 12 months)
    monthly: dict[str, float] = {}
    for e in expenses:
        key = e.spent_at.strftime("%Y-%m")
        monthly[key] = monthly.get(key, 0) + e.amount
    monthly_sorted = dict(sorted(monthly.items())[-12:])

    return {
        "total_trips": len(trips),
        "completed_trips": sum(1 for t in trips if t.status == "completed"),
        "active_trips": sum(1 for t in trips if t.status in ("active", "upcoming")),
        "planning_trips": sum(1 for t in trips if t.status == "planning"),
        "total_budget": total_budget,
        "total_spent": total_spent,
        "total_savings": max(0, total_budget - total_spent),
        "total_places": len(places),
        "visited_places": sum(1 for p in places if p.status == "visited"),
        "total_photos": photos_count,
        "destinations": destinations,
        "destinations_count": len(destinations),
        "by_category": by_category,
        "monthly_spending": monthly_sorted,
        "avg_trip_budget": total_budget / len(trips) if trips else 0,
        "avg_trip_spent": total_spent / len(trips) if trips else 0,
    }

@router.get("/activity", tags=["stats"])
async def recent_activity(u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Last 20 actions across trips"""
    from models.models import Expense, Photo, Note, ChecklistItem
    trips_r = await db.execute(select(Trip).where(Trip.owner_id == u.id))
    trips = trips_r.scalars().all()
    trip_ids = [t.id for t in trips]
    trip_map = {t.id: t for t in trips}
    activity = []

    if trip_ids:
        # Recent expenses
        ex_r = await db.execute(
            select(Expense).where(Expense.trip_id.in_(trip_ids))
            .order_by(Expense.created_at.desc()).limit(8)
        )
        for e in ex_r.scalars().all():
            tr = trip_map.get(e.trip_id)
            activity.append({
                "type": "expense", "icon": "💳",
                "text": f"Added ₹{e.amount:,.0f} expense — {e.title}",
                "trip": tr.name if tr else "Unknown", "trip_emoji": tr.cover_emoji if tr else "✈️",
                "at": e.created_at.isoformat()
            })
        # Recent photos
        ph_r = await db.execute(
            select(Photo).where(Photo.trip_id.in_(trip_ids))
            .order_by(Photo.uploaded_at.desc()).limit(5)
        )
        for p in ph_r.scalars().all():
            tr = trip_map.get(p.trip_id)
            activity.append({
                "type": "photo", "icon": "📸",
                "text": f"Uploaded photo{f': {p.caption}' if p.caption else ''}",
                "trip": tr.name if tr else "Unknown", "trip_emoji": tr.cover_emoji if tr else "✈️",
                "at": p.uploaded_at.isoformat()
            })
        # Recent notes
        from models.models import Note
        no_r = await db.execute(
            select(Note).where(Note.trip_id.in_(trip_ids))
            .order_by(Note.created_at.desc()).limit(5)
        )
        for n in no_r.scalars().all():
            tr = trip_map.get(n.trip_id)
            activity.append({
                "type": "note", "icon": "📝",
                "text": f"Saved note: {n.title}",
                "trip": tr.name if tr else "Unknown", "trip_emoji": tr.cover_emoji if tr else "✈️",
                "at": n.created_at.isoformat()
            })

    # Recent trips
    for t in sorted(trips, key=lambda x: x.created_at, reverse=True)[:4]:
        activity.append({
            "type": "trip", "icon": t.cover_emoji,
            "text": f"Created trip to {t.destination}",
            "trip": t.name, "trip_emoji": t.cover_emoji,
            "at": t.created_at.isoformat()
        })

    activity.sort(key=lambda x: x["at"], reverse=True)
    return activity[:20]
