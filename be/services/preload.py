import json
import logging
import httpx
from sqlalchemy import select
from models.models import Trip, ItineraryDay
from routers.ai import build_prompt, call_ai

logger = logging.getLogger(__name__)

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]


async def _run_overpass(query: str) -> dict | None:
    """Run an Overpass query directly via httpx (not through the FastAPI route handler)."""
    async with httpx.AsyncClient(timeout=30) as client:
        for mirror in OVERPASS_MIRRORS:
            try:
                res = await client.post(
                    mirror,
                    data={"data": query},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if res.status_code == 200:
                    return res.json()
            except Exception as e:
                logger.debug(f"Overpass mirror {mirror} failed: {e}")
    return None


async def preload_trip_data(trip_id: int):
    """
    Background task: pre-generate AI itinerary and fetch nearby map facilities.
    """
    try:
        from db.session import SessionLocal

        # Fix: use `async with` — SessionLocal is an async_sessionmaker, not a plain class
        async with SessionLocal() as db:
            r = await db.execute(select(Trip).where(Trip.id == trip_id))
            trip = r.scalar_one_or_none()
            if not trip:
                return

            # --- 1. Pre-generate itinerary if dates set and itinerary is empty ---
            days_r = await db.execute(
                select(ItineraryDay).where(ItineraryDay.trip_id == trip.id)
            )
            days = list(days_r.scalars().all())

            if not days and trip.start_date and trip.end_date:
                try:
                    prompt = build_prompt(
                        trip, [], [], [],
                        "Generate a detailed day-by-day itinerary. Format as a strict JSON array "
                        "of objects with keys: 'day_number' (int), 'title' (string), "
                        "'notes' (string), 'place_names' (string, comma-separated places)."
                    )
                    ai_response = await call_ai(prompt)
                    clean_json = ai_response.replace("```json", "").replace("```", "").strip()
                    itinerary_data = json.loads(clean_json)

                    new_days = []
                    for idx, day_data in enumerate(itinerary_data):
                        day = ItineraryDay(
                            trip_id=trip.id,
                            day_number=day_data.get("day_number", idx + 1),
                            title=day_data.get("title", f"Day {idx + 1}"),
                            notes=day_data.get("notes", ""),
                            place_names=day_data.get("place_names", ""),
                        )
                        db.add(day)
                        new_days.append(day)

                    await db.flush()
                    await db.commit()
                    logger.info(f"Preloaded {len(new_days)} itinerary days for trip {trip.id}")
                except Exception as e:
                    logger.warning(f"Failed to preload AI itinerary for trip {trip.id}: {e}")
                    await db.rollback()

            # --- 2. Pre-fetch map facilities if destination is known ---
            if trip.destination and not trip.preloaded_facilities:
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        geo_res = await client.get(
                            "https://nominatim.openstreetmap.org/search",
                            params={"q": trip.destination, "format": "json", "limit": "1"},
                            headers={"User-Agent": "TravelMateApp/1.0"},
                        )
                        geo_data = geo_res.json() if geo_res.status_code == 200 else []

                    if geo_data:
                        lat = float(geo_data[0]["lat"])
                        lon = float(geo_data[0]["lon"])
                        minLat, maxLat = lat - 0.045, lat + 0.045
                        minLng, maxLng = lon - 0.045, lon + 0.045

                        q = (
                            f'[out:json][timeout:15];'
                            f'(node["tourism"]({minLat},{minLng},{maxLat},{maxLng});'
                            f'node["amenity"~"restaurant|cafe"]({minLat},{minLng},{maxLat},{maxLng});'
                            f');out body 50;'
                        )
                        # Fix: call Overpass directly — not via the FastAPI route handler
                        overpass_res = await _run_overpass(q)

                        if overpass_res and "elements" in overpass_res:
                            trip.preloaded_facilities = json.dumps(overpass_res.get("elements", []))
                            trip.map_bbox = json.dumps({
                                "minLat": minLat, "maxLat": maxLat,
                                "minLng": minLng, "maxLng": maxLng,
                                "center": [lat, lon],
                            })
                            await db.flush()
                            await db.commit()
                            logger.info(f"Preloaded map facilities for trip {trip.id}")
                except Exception as e:
                    logger.warning(f"Failed to preload facilities for trip {trip.id}: {e}")

    except Exception as e:
        logger.error(f"Error in preload_trip_data for trip {trip_id}: {e}")
