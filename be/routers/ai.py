import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.config import get_settings
from db.session import get_db
from models.models import Expense, Place, Trip, User, ItineraryDay
from schemas.schemas import AIRequest, AIResponse
from services.deps import current_user

router = APIRouter(tags=["AI"])
settings = get_settings()
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

async def call_gemini(prompt: str) -> str:
    if not settings.gemini_api_key or settings.gemini_api_key == "your-gemini-api-key-here":
        raise HTTPException(503, "Gemini API key not configured. Add GEMINI_API_KEY to your .env file.")
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048}}
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(f"{GEMINI_URL}?key={settings.gemini_api_key}", json=payload)
        if res.status_code != 200:
            detail = res.json().get("error", {}).get("message", "Gemini API error")
            raise HTTPException(502, f"Gemini error: {detail}")
        data = res.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

def build_prompt(trip, places, expenses, days, query):
    visited  = [p for p in places if p.status == "visited"]
    upcoming = [p for p in places if p.status in ("planned", "upcoming")]
    total_exp = sum(e.amount for e in expenses)
    remaining = trip.budget - total_exp
    places_text = ""
    if visited:  places_text += f"Already visited: {', '.join(p.name for p in visited)}. "
    if upcoming: places_text += f"Still to visit: {', '.join(p.name for p in upcoming)}. "
    expenses_text = ""
    if expenses:
        by_cat = {}
        for e in expenses: by_cat[e.category] = by_cat.get(e.category, 0) + e.amount
        expenses_text = "Expenses: " + ", ".join(f"{c}: ₹{a:,.0f}" for c, a in by_cat.items()) + "."
    itinerary_text = ""
    if days:
        itinerary_text = "Itinerary: " + "; ".join(f"Day {d.day_number} - {d.title}" + (f" ({d.place_names})" if d.place_names else "") for d in days) + "."
    return f"""You are TravelMate AI, a friendly travel assistant for Indian destinations.

TRIP: {trip.name} to {trip.destination} ({trip.status})
DATES: {trip.start_date or "not set"} to {trip.end_date or "not set"}
BUDGET: ₹{trip.budget:,.0f} total, ₹{total_exp:,.0f} spent, ₹{remaining:,.0f} remaining ({trip.progress:.0f}% used)
PLACES: {places_text or "None added yet."}
{expenses_text}
{itinerary_text}

USER: {query}

RULES: Answer ONLY what was asked. Be concise and direct. No preambles, no unrelated sections.
Use Indian context (₹, local knowledge). Emojis sparingly. Bullet points only if listing items.
If asked one thing, give 2-5 sentences max. Only expand if the user explicitly asks for detail."""

@router.post("/recommend", response_model=AIResponse)
async def recommend(body: AIRequest, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Trip).where(Trip.id == body.trip_id, Trip.owner_id == u.id))
    trip = r.scalar_one_or_none()
    if not trip:
        raise HTTPException(404, "Trip not found")
    places   = list((await db.execute(select(Place).where(Place.trip_id == trip.id))).scalars().all())
    expenses = list((await db.execute(select(Expense).where(Expense.trip_id == trip.id))).scalars().all())
    days     = list((await db.execute(select(ItineraryDay).where(ItineraryDay.trip_id == trip.id))).scalars().all())
    response = await call_gemini(build_prompt(trip, places, expenses, days, body.query))
    return AIResponse(response=response, trip_name=trip.name)
