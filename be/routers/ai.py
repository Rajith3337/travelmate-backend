import httpx
import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.config import get_settings
from db.session import get_db
from models.models import Expense, Place, Trip, User, ItineraryDay, AIChat, now
from schemas.schemas import AIRequest, AIResponse, RoadmapPrecomputeOut, RoadmapStatusOut, RoadmapTripStatusOut, AIChatIn, AIChatOut
from services.deps import current_user
from services.roadmap_precompute import classify_trip_warmup, warmup_trip_all_data, trip_warmup_status

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

async def call_ai_with_meta(prompt: str) -> tuple[str, str]:
    """Return (text, provider) where provider is gemini|grok."""
    gemini_ok = settings.gemini_api_key and settings.gemini_api_key != "your-gemini-api-key-here"
    grok_ok   = settings.grok_api_key   and settings.grok_api_key   != "your-grok-api-key-here"
    if gemini_ok:
        try:
            return await call_gemini(prompt), "gemini"
        except Exception:
            if grok_ok:
                return await call_grok(prompt), "grok"
            raise
    elif grok_ok:
        return await call_grok(prompt), "grok"
    raise HTTPException(503, "No AI API key configured. Add GEMINI_API_KEY or GROK_API_KEY to .env")

def ai_available() -> bool:
    return bool(
        (settings.gemini_api_key and settings.gemini_api_key != "your-gemini-api-key-here")
        or (settings.grok_api_key and settings.grok_api_key != "your-grok-api-key-here")
    )


def build_prompt(user, trip, places, expenses, days, all_trips, query):
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
        expenses_text = "Expenses: " + ", ".join(f"{c}: INR {a:,.0f}" for c, a in by_cat.items()) + "."
    itinerary_text = ""
    if days:
        itinerary_text = "Itinerary: " + "; ".join(f"Day {d.day_number} - {d.title}" + (f" ({d.place_names})" if d.place_names else "") for d in days) + "."
    trips_summary = []
    for t in all_trips:
        trips_summary.append(
            f"- {t.name} to {t.destination} ({t.status})"
            f", dates: {t.start_date or 'not set'} to {t.end_date or 'not set'}"
            f", budget: INR {t.budget:,.0f}, spent: INR {t.spent:,.0f}"
        )
    trips_block = "\n".join(trips_summary) if trips_summary else "- None"

    return f"""You are TravelMate AI, a friendly travel assistant for Indian destinations.

USER PROFILE:
- Name: {user.full_name}
- Username: {user.username}
- Email: {user.email}
- Joined: {user.created_at.strftime('%Y-%m-%d') if user.created_at else 'unknown'}

ALL TRIPS (summary):
{trips_block}

ACTIVE TRIP:
TRIP: {trip.name} to {trip.destination} ({trip.status})
DATES: {trip.start_date or "not set"} to {trip.end_date or "not set"}
BUDGET: INR {trip.budget:,.0f} total, INR {total_exp:,.0f} spent, INR {remaining:,.0f} remaining ({trip.progress:.0f}% used)
PLACES: {places_text or "None added yet."}
{expenses_text}
{itinerary_text}

USER: {query}

RULES: Answer ONLY what was asked. Be concise and direct. No preambles, no unrelated sections.
Use Indian context (INR, local knowledge). Emojis sparingly. Bullet points only if listing items.
If asked one thing, give 2-5 sentences max. Only expand if the user explicitly asks for detail."""

@router.post("/travel-insights")
async def travel_insights(body: dict):
    """Travel mode insights - AI only, no fallback."""
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(400, "prompt is required")
    if not ai_available():
        raise HTTPException(503, "AI not configured. Add GEMINI_API_KEY or GROK_API_KEY.")
    text, provider = await call_ai_with_meta(prompt)
    return {"text": text, "ai_used": True, "ai_provider": provider}

@router.post("/overpass")
async def overpass_proxy(body: dict):
    """Proxy Overpass queries - avoids browser rate limits (429s).
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
    r = await db.execute(select(Trip).where(Trip.owner_id == u.id))
    all_trips = list(r.scalars().all())
    places   = list((await db.execute(select(Place).where(Place.trip_id == trip.id))).scalars().all())
    expenses = list((await db.execute(select(Expense).where(Expense.trip_id == trip.id))).scalars().all())
    days     = list((await db.execute(select(ItineraryDay).where(ItineraryDay.trip_id == trip.id))).scalars().all())
    response = await call_ai(build_prompt(u, trip, places, expenses, days, all_trips, body.query))
    return AIResponse(response=response, trip_name=trip.name)


@router.get("/chat/{trip_id}", response_model=AIChatOut)
async def get_chat(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Trip).where(Trip.id == trip_id, Trip.owner_id == u.id))
    if not r.scalar_one_or_none():
        raise HTTPException(404, "Trip not found")
    r = await db.execute(select(AIChat).where(AIChat.trip_id == trip_id, AIChat.owner_id == u.id))
    chat = r.scalar_one_or_none()
    if not chat:
        return AIChatOut(trip_id=trip_id, messages=[], updated_at=None)
    try:
        messages = json.loads(chat.messages_json or "[]")
        if not isinstance(messages, list):
            messages = []
    except Exception:
        messages = []
    return AIChatOut(trip_id=trip_id, messages=messages, updated_at=chat.updated_at)


@router.put("/chat/{trip_id}", response_model=AIChatOut)
async def save_chat(trip_id: int, body: AIChatIn, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Trip).where(Trip.id == trip_id, Trip.owner_id == u.id))
    if not r.scalar_one_or_none():
        raise HTTPException(404, "Trip not found")
    messages = body.messages or []
    if not isinstance(messages, list):
        raise HTTPException(400, "messages must be a list")
    r = await db.execute(select(AIChat).where(AIChat.trip_id == trip_id, AIChat.owner_id == u.id))
    chat = r.scalar_one_or_none()
    if not chat:
        chat = AIChat(owner_id=u.id, trip_id=trip_id)
    chat.messages_json = json.dumps(messages)
    chat.updated_at = now()
    db.add(chat)
    return AIChatOut(trip_id=trip_id, messages=messages, updated_at=chat.updated_at)


async def _throttled_warmup(trip_ids: list[int], force: bool = False):
    """Warm up trips one at a time with a small delay between each.
    Prevents hammering Nominatim/OSRM/Overpass/Gemini simultaneously."""
    for i, trip_id in enumerate(trip_ids):
        try:
            if i > 0:
                await asyncio.sleep(2)  # 2s gap between each trip - stay under rate limits
            await warmup_trip_all_data(trip_id, force)
        except Exception as exc:
            # Never let one trip failure stop the rest
            import logging
            logging.getLogger("travelmate.warmup").warning(
                "Warmup failed for trip %s: %s", trip_id, exc
            )


@router.post("/roadmaps/precompute", response_model=RoadmapPrecomputeOut)
async def precompute_roadmaps(
    background_tasks: BackgroundTasks,
    u: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(select(Trip).where(Trip.owner_id == u.id))
    trips = list(r.scalars().all())
    total = len(trips)
    queued = 0
    skipped_fresh = 0
    skipped_invalid = 0

    stale_ids = []
    for trip in trips:
        state = classify_trip_warmup(trip)
        if state == "invalid":
            skipped_invalid += 1
            continue
        if state == "fresh":
            skipped_fresh += 1
            continue
        queued += 1
        stale_ids.append(trip.id)

    # Queue a SINGLE background task that processes trips sequentially with delays.
    # Previously each trip got its own task - they all ran in parallel and hammered
    # external APIs (Nominatim, OSRM, Overpass, Gemini) causing timeouts and slowness.
    if stale_ids:
        background_tasks.add_task(_throttled_warmup, stale_ids, False)

    return RoadmapPrecomputeOut(
        total=total,
        queued=queued,
        skipped_fresh=skipped_fresh,
        skipped_invalid=skipped_invalid,
    )


@router.get("/roadmaps/status", response_model=RoadmapStatusOut)
async def roadmap_status(
    u: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(select(Trip).where(Trip.owner_id == u.id))
    trips = list(r.scalars().all())
    items = [RoadmapTripStatusOut(**trip_warmup_status(tr)) for tr in trips]
    fresh = sum(1 for i in items if i.state == "fresh")
    invalid = sum(1 for i in items if i.state == "invalid")
    stale = len(items) - fresh - invalid
    return RoadmapStatusOut(total=len(items), fresh=fresh, stale=stale, invalid=invalid, items=items, ai_available=ai_available())





