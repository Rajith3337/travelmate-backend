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

GROK_URL = "https://api.x.ai/v1/chat/completions"

async def call_grok(prompt: str) -> str:
    if not settings.grok_api_key or settings.grok_api_key == "your-grok-api-key-here":
        raise HTTPException(503, "Grok API key not configured. Add GROK_API_KEY to .env")
    payload = {
        "model": "grok-3-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 2048,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(
            GROK_URL,
            json=payload,
            headers={"Authorization": f"Bearer {settings.grok_api_key}", "Content-Type": "application/json"},
        )
        if res.status_code != 200:
            detail = res.json().get("error", {}).get("message", "Grok API error")
            raise HTTPException(502, f"Grok error: {detail}")
        return res.json()["choices"][0]["message"]["content"]

async def call_ai(prompt: str) -> str:
    """Try Gemini first, fall back to Grok if Gemini fails or is unconfigured."""
    gemini_ok = settings.gemini_api_key and settings.gemini_api_key != "your-gemini-api-key-here"
    grok_ok   = settings.grok_api_key   and settings.grok_api_key   != "your-grok-api-key-here"
    if gemini_ok:
        try:
            return await call_gemini(prompt)
        except Exception:
            if grok_ok:
                return await call_grok(prompt)
            raise
    elif grok_ok:
        return await call_grok(prompt)
    raise HTTPException(503, "No AI API key configured. Add GEMINI_API_KEY or GROK_API_KEY to .env")


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

@router.post("/travel-insights")
async def travel_insights(body: dict):
    """Travel mode insights — tries Gemini first, falls back to smart distance calc."""
    prompt = body.get("prompt", "")
    dist_km = body.get("dist_km", 0)
    driving_min = body.get("driving_min", 0)
    from_loc = body.get("from_loc", "Origin")
    to_loc = body.get("to_loc", "Destination")

    # Try Gemini if key is configured
    try:
        if prompt and settings.gemini_api_key and settings.gemini_api_key != "your-gemini-api-key-here":
            text = await call_ai(prompt)
            return {"text": text}
    except Exception:
        pass  # Fall through to built-in calculation

    # Built-in smart calculation (no API key needed)
    import math
    d = dist_km or 300
    drv = driving_min or int(d / 0.8)

    flight_ok = d > 350
    flight_dur = f"{math.ceil(d/600 + 1)}h" if flight_ok else "N/A"
    flight_score = 9 if d > 600 else 7 if d > 350 else 2

    train_dur_h = round(d / 55)
    train_score = 8 if 100 < d < 800 else 5

    bus_dur_h = round(d / 45)
    bus_cost_low = round(d * 1.2 / 10) * 10
    bus_cost_high = round(d * 2.5 / 10) * 10

    road_h = drv // 60
    road_m = drv % 60
    road_dur = f"{road_h}h {road_m}m" if road_h else f"{road_m}m"
    fuel_cost = round(d * 3 / 10) * 10

    if d > 600:
        best = "✈️ Flight"
        rec = f"For {round(d)}km, flight saves the most time."
    elif d > 200:
        best = "🚆 Train"
        rec = f"Train is the best balance of comfort and cost for {round(d)}km."
    else:
        best = "🚗 Road"
        rec = f"Road trip is ideal for this {round(d)}km distance."

    result = {
        "recommendation": rec,
        "bestMode": best,
        "modes": [
            {"mode":"✈️ Flight","available":flight_ok,"duration":flight_dur,
             "cost":f"₹2,500–12,000","frequency":"Check airlines","note":"Book 2–3 weeks ahead","score":flight_score},
            {"mode":"🚆 Train","available":True,"duration":f"{train_dur_h}h",
             "cost":f"₹{round(d*0.8/10)*10}–{round(d*2/10)*10}","frequency":"Multiple daily","note":"Book on IRCTC","score":train_score},
            {"mode":"🚌 Bus","available":True,"duration":f"{bus_dur_h}h",
             "cost":f"₹{bus_cost_low}–{bus_cost_high}","frequency":"Multiple daily","note":"Budget option","score":5},
            {"mode":"🚗 Road","available":True,"duration":road_dur,
             "cost":f"₹{fuel_cost} fuel","frequency":"Anytime","note":"Flexible stops","score":6},
        ],
        "tips": [
            "Book train tickets on IRCTC 2–3 months ahead for best fares",
            "Check IndiGo, Air India, SpiceJet for flight deals",
            "Carry snacks and water for road/bus journeys"
        ]
    }
    import json
    return {"text": json.dumps(result)}

@router.post("/overpass")
async def overpass_proxy(body: dict):
    """Proxy Overpass queries — avoids browser rate limits (429s).
    Server-side requests are not rate-limited like browser requests."""
    query = body.get("query", "")
    if not query:
        raise HTTPException(400, "query required")
    MIRRORS = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.private.coffee/api/interpreter",
    ]
    last_err = None
    async with httpx.AsyncClient(timeout=60) as client:
        for mirror in MIRRORS:
            try:
                res = await client.post(
                    mirror,
                    data={"data": query},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if res.status_code == 200:
                    return res.json()
                last_err = f"{mirror} returned {res.status_code}"
            except Exception as e:
                last_err = str(e)
    raise HTTPException(502, f"All Overpass mirrors failed: {last_err}")

@router.post("/recommend", response_model=AIResponse)
async def recommend(body: AIRequest, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Trip).where(Trip.id == body.trip_id, Trip.owner_id == u.id))
    trip = r.scalar_one_or_none()
    if not trip:
        raise HTTPException(404, "Trip not found")
    places   = list((await db.execute(select(Place).where(Place.trip_id == trip.id))).scalars().all())
    expenses = list((await db.execute(select(Expense).where(Expense.trip_id == trip.id))).scalars().all())
    days     = list((await db.execute(select(ItineraryDay).where(ItineraryDay.trip_id == trip.id))).scalars().all())
    response = await call_ai(build_prompt(trip, places, expenses, days, body.query))
    return AIResponse(response=response, trip_name=trip.name)