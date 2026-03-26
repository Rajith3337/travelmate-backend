import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from models.models import Trip, ItineraryDay
from routers.ai import build_prompt, call_ai
from routers.ai import overpass_proxy

logger = logging.getLogger(__name__)

async def preload_trip_data(trip_id: int):
    """
    Background task to pre-generate AI Itinerary and fetch nearby facilities.
    """
    try:
        from core.config import get_settings
        from db.session import SessionLocal
        from sqlalchemy import select
        
        db = SessionLocal()
        # 1. Fetch the trip explicitly
        r = await db.execute(select(Trip).where(Trip.id == trip_id))
        trip = r.scalar_one_or_none()
        if not trip:
            return

        # 2. Pre-generate Itinerary if dates are provided and itinerary is empty
        days_r = await db.execute(select(ItineraryDay).where(ItineraryDay.trip_id == trip.id))
        days = list(days_r.scalars().all())
        
        if not days and trip.start_date and trip.end_date:
            try:
                # Ask AI for a day-by-day itinerary
                prompt = build_prompt(trip, [], [], [], "Generate a detailed day-by-day itinerary. Format as a strict JSON array of objects with keys: 'day_number' (int), 'title' (string), 'notes' (string), 'place_names' (string, comma-separated places).")
                ai_response = await call_ai(prompt)
                
                # Parse JSON array out of AI response (strip markdown wrappers if any)
                clean_json = ai_response.replace("```json", "").replace("```", "").strip()
                itinerary_data = json.loads(clean_json)

                new_days = []
                for idx, day_data in enumerate(itinerary_data):
                    day = ItineraryDay(
                        trip_id=trip.id,
                        day_number=day_data.get("day_number", idx + 1),
                        title=day_data.get("title", f"Day {idx + 1}"),
                        notes=day_data.get("notes", ""),
                        place_names=day_data.get("place_names", "")
                    )
                    db.add(day)
                    new_days.append(day)
                
                await db.commit()
                logger.info(f"Preloaded {len(new_days)} itinerary days for trip {trip.id}")
            except Exception as e:
                logger.warning(f"Failed to preload AI itinerary for trip {trip.id}: {e}")
                await db.rollback()

        # 3. Pre-fetch Map Facilities if destination is known
        if trip.destination and not trip.preloaded_facilities:
            try:
                # First get coordinates of destination
                import httpx
                async with httpx.AsyncClient(timeout=10) as client:
                    geo_res = await client.get(f"https://nominatim.openstreetmap.org/search?q={trip.destination}&format=json&limit=1", headers={"User-Agent": "TravelMateApp/1.0"})
                    if geo_res.status_code == 200 and geo_res.json():
                        lat = float(geo_res.json()[0]["lat"])
                        lon = float(geo_res.json()[0]["lon"])
                        
                        # Fetch nearby overpass data (e.g., top attractions, restaurants, hotels within 5km)
                        minLat = lat - 0.045
                        maxLat = lat + 0.045
                        minLng = lon - 0.045
                        maxLng = lon + 0.045
                        
                        q = f'[out:json][timeout:15];(node["tourism"]({minLat},{minLng},{maxLat},{maxLng});node["amenity"~"restaurant|cafe"]({minLat},{minLng},{maxLat},{maxLng}););out body 50;'
                        overpass_res = await overpass_proxy({"query": q})
                        
                        if overpass_res and isinstance(overpass_res, dict) and "elements" in overpass_res:
                            # Save to trip cache
                            trip.preloaded_facilities = json.dumps(overpass_res.get("elements", []))
                            trip.map_bbox = json.dumps({"minLat": minLat, "maxLat": maxLat, "minLng": minLng, "maxLng": maxLng, "center": [lat, lon]})
                            db.add(trip)
                            await db.commit()
                            logger.info(f"Preloaded map facilities for trip {trip.id}")
            except Exception as e:
                logger.warning(f"Failed to preload facilities for trip {trip.id}: {e}")
                
    except Exception as e:
        logger.error(f"Error in preload_trip_data for trip {trip_id}: {e}")
    finally:
        await db.close()

