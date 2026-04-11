import logging
import asyncio
import json
from datetime import datetime, timezone
from math import atan2, cos, sin, sqrt
from typing import TYPE_CHECKING

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from db.session import SessionLocal
from models.models import Trip, Place, ItineraryDay

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
GROK_URL = "https://api.x.ai/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# Limit to 1 concurrent warmup job at a time — prevents hammering Nominatim/OSRM/Overpass.
# Multiple concurrent warmups caused 429s, slow responses, and apparent startup hangs.
_warmup_sem = asyncio.Semaphore(1)

def _ai_available() -> bool:
    settings = get_settings()
    return bool(
        (settings.gemini_api_key and settings.gemini_api_key != "your-gemini-api-key-here")
        or (settings.grok_api_key and settings.grok_api_key != "your-grok-api-key-here")
    )


def _norm(v):
    return " ".join(str(v or "").strip().lower().split())


def _resolved_route_endpoints(trip: Trip):
    start = (trip.start_location or "").strip()
    destination = (trip.destination or "").strip()
    if start and destination:
        return start, destination
    if trip.active_route:
        try:
            ar = json.loads(trip.active_route)
            start = start or str(ar.get("fromLabel") or "").strip()
            destination = destination or str(ar.get("toLabel") or "").strip()
        except Exception:
            pass
    if (not start or not destination) and trip.ai_roadmap:
        try:
            rr = json.loads(trip.ai_roadmap)
            start = start or str(rr.get("start") or "").strip()
            destination = destination or str(rr.get("destination") or "").strip()
        except Exception:
            pass
    return start, destination


def roadmap_signature_for_trip(trip: Trip) -> str:
    start, destination = _resolved_route_endpoints(trip)
    payload = {
        "name": _norm(trip.name),
        "start": _norm(start),
        "destination": _norm(destination),
        "start_date": _norm(trip.start_date),
        "end_date": _norm(trip.end_date),
        "budget": float(trip.budget or 0.0),
        "description": _norm(trip.description),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _is_cached_fresh(trip: Trip) -> bool:
    if not trip.ai_roadmap:
        return False
    try:
        cached = json.loads(trip.ai_roadmap)
    except Exception:
        return False
    start, destination = _resolved_route_endpoints(trip)
    return (
        isinstance(cached, dict)
        and isinstance(cached.get("plan"), dict)
        and isinstance(cached.get("insight"), dict)
        and cached.get("start") == start
        and cached.get("destination") == destination
        and cached.get("signature") == roadmap_signature_for_trip(trip)
    )


def classify_trip_precompute(trip: Trip) -> str:
    start, destination = _resolved_route_endpoints(trip)
    if not (start and destination):
        return "invalid"
    return "fresh" if _is_cached_fresh(trip) else "needs"


def _is_active_route_ready(trip: Trip) -> bool:
    if not trip.active_route:
        return False
    try:
        cached = json.loads(trip.active_route)
    except Exception:
        return False
    return isinstance(cached, dict) and isinstance(cached.get("routes"), list) and len(cached.get("routes")) > 0


def _is_livemap_cached(trip: Trip) -> bool:
    return bool(trip.map_bbox and trip.preloaded_facilities and _is_active_route_ready(trip))


def classify_trip_warmup(trip: Trip) -> str:
    start, destination = _resolved_route_endpoints(trip)
    if not (start and destination):
        return "invalid"
    return "fresh" if (_is_cached_fresh(trip) and _is_livemap_cached(trip)) else "needs"


def trip_warmup_status(trip: Trip):
    ai_ready = _is_cached_fresh(trip)
    livemap_ready = _is_livemap_cached(trip)
    state = "invalid"
    start, destination = _resolved_route_endpoints(trip)
    if start and destination:
        state = "fresh" if (ai_ready and livemap_ready) else "stale"
    generated_at = None
    if trip.ai_roadmap:
        try:
            generated_at = (json.loads(trip.ai_roadmap) or {}).get("generated_at")
        except Exception:
            generated_at = None
    return {
        "trip_id": trip.id,
        "trip_name": trip.name,
        "state": state,
        "ai_ready": ai_ready,
        "livemap_ready": livemap_ready,
        "generated_at": generated_at,
    }


def _haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000
    d_lat = (lat2 - lat1) * 3.141592653589793 / 180
    d_lng = (lng2 - lng1) * 3.141592653589793 / 180
    a = sin(d_lat / 2) ** 2 + cos(lat1 * 3.141592653589793 / 180) * cos(lat2 * 3.141592653589793 / 180) * sin(d_lng / 2) ** 2
    return r * 2 * atan2(sqrt(a), sqrt(1 - a))


def _point_at_dist(coords, target_m):
    acc = 0.0
    for i in range(1, len(coords)):
        seg = _haversine_m(coords[i - 1][0], coords[i - 1][1], coords[i][0], coords[i][1])
        if acc + seg >= target_m:
            frac = (target_m - acc) / seg if seg else 0
            return [
                coords[i - 1][0] + frac * (coords[i][0] - coords[i - 1][0]),
                coords[i - 1][1] + frac * (coords[i][1] - coords[i - 1][1]),
            ]
        acc += seg
    return coords[-1]


def _parse_elements(elements):
    out = []
    for e in elements or []:
        lat, lon = e.get("lat"), e.get("lon")
        if lat is None or lon is None:
            continue
        tags = e.get("tags") or {}
        out.append({
            "id": str(e.get("id")),
            "lat": lat,
            "lng": lon,
            "name": tags.get("name") or tags.get("name:en") or tags.get("tourism") or tags.get("amenity") or "Unnamed",
            "type": tags.get("tourism") or tags.get("amenity") or "",
            "addr": ", ".join([x for x in [tags.get("addr:street"), tags.get("addr:city")] if x]) or None,
            "hours": tags.get("opening_hours"),
            "phone": tags.get("phone"),
        })
    return out


def _pick_best(results, lat, lng, radius_m):
    ranked = []
    for r in results:
        d = _haversine_m(lat, lng, r["lat"], r["lng"])
        if d <= radius_m and r.get("name") != "Unnamed":
            ranked.append((d, r))
    ranked.sort(key=lambda x: x[0])
    return ranked[0][1] if ranked else None


async def _geocode_city(client: httpx.AsyncClient, q: str):
    r = await client.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": q, "format": "json", "limit": "1"},
        headers={"User-Agent": "TravelMate/1.0"},
    )
    if r.status_code != 200:
        return None
    data = r.json()
    if not data:
        return None
    return {"lat": float(data[0]["lat"]), "lng": float(data[0]["lon"])}


async def _fetch_route(client: httpx.AsyncClient, from_coord, to_coord):
    mirrors = [
        f"https://router.project-osrm.org/route/v1/driving/{from_coord['lng']},{from_coord['lat']};{to_coord['lng']},{to_coord['lat']}?overview=full&geometries=geojson",
        f"https://routing.openstreetmap.de/routed-car/route/v1/driving/{from_coord['lng']},{from_coord['lat']};{to_coord['lng']},{to_coord['lat']}?overview=full&geometries=geojson",
    ]
    for url in mirrors:
        try:
            res = await client.get(url, timeout=25)
            if res.status_code != 200:
                continue
            data = res.json()
            if data.get("routes"):
                route = data["routes"][0]
                coords = [[pt[1], pt[0]] for pt in route["geometry"]["coordinates"]]
                return {
                    "coords": coords,
                    "dist_km": route["distance"] / 1000,
                    "dur_h": route["duration"] / 3600,
                }
        except Exception:
            continue
    return None


def _facility_cat_from_type(t: str):
    tt = (t or "").lower()
    if tt in {"restaurant", "cafe", "fast_food"}:
        return ("food", "🍽️", "Food", "#FFB800")
    if tt in {"hotel", "hostel", "motel", "guest_house"}:
        return ("hotel", "🏨", "Stay", "#A855F7")
    if tt in {"hospital", "clinic", "pharmacy"}:
        return ("medical", "🏥", "Medical", "#FF4E6A")
    if tt == "atm":
        return ("atm", "🏧", "ATM", "#00D4FF")
    if tt in {"fuel", "charging_station"}:
        return ("fuel", "⛽", "Fuel", "#39FF6A")
    if tt == "bus_stop":
        return ("transit", "🚌", "Transit", "#FF8A00")
    if tt in {"attraction", "museum", "viewpoint"}:
        return ("sights", "🏛️", "Sights", "#00FFCC")
    if tt in {"police", "fire_station"}:
        return ("police", "🚨", "Safety", "#FF6ECC")
    return ("sights", "🏛️", "Sights", "#00FFCC")


async def _fetch_active_routes(client: httpx.AsyncClient, from_coord, to_coord):
    urls = [
        f"https://router.project-osrm.org/route/v1/driving/{from_coord['lng']},{from_coord['lat']};{to_coord['lng']},{to_coord['lat']}?overview=full&geometries=geojson&alternatives=true&steps=true",
        f"https://router.project-osrm.org/route/v1/driving/{from_coord['lng']},{from_coord['lat']};{to_coord['lng']},{to_coord['lat']}?overview=simplified&geometries=geojson&alternatives=false&steps=true",
    ]
    for url in urls:
        try:
            res = await client.get(url, timeout=25)
            if res.status_code != 200:
                continue
            data = res.json()
            routes = data.get("routes") or []
            if not routes:
                continue
            out = []
            labels = ["🏆 Fastest", "🛣️ Alt 1", "🌿 Alt 2", "🔄 Alt 3"]
            colors = ["#34D399", "#FBBF24", "#F472B6", "#818CF8"]
            for idx, r in enumerate(routes[:4]):
                coords = [[pt[1], pt[0]] for pt in r["geometry"]["coordinates"]]
                out.append({
                    "idx": idx,
                    "dist": round(r["distance"] / 1000, 1),
                    "dur": round(r["duration"] / 60),
                    "coords": coords,
                    "label": labels[idx] if idx < len(labels) else f"Route {idx+1}",
                    "color": colors[idx] if idx < len(colors) else "#A5B4FC",
                    "steps": ((r.get("legs") or [{}])[0].get("steps") or [])[:60],
                })
            return out
        except Exception:
            continue
    return []


async def _build_livemap_payload(trip: Trip):
    start_label, dest_label = _resolved_route_endpoints(trip)
    if not (start_label and dest_label):
        return None
    async with httpx.AsyncClient(timeout=30) as client:
        from_coord = await _geocode_city(client, start_label)
        to_coord = await _geocode_city(client, dest_label)
        if not from_coord or not to_coord:
            return None

        # Route cache for LiveMap initial draw.
        active_routes = await _fetch_active_routes(client, from_coord, to_coord)
        if not active_routes:
            # fallback single route
            one = await _fetch_route(client, from_coord, to_coord)
            if one:
                active_routes = [{
                    "idx": 0,
                    "dist": round(one["dist_km"], 1),
                    "dur": round(one["dur_h"] * 60),
                    "coords": one["coords"],
                    "label": "🏆 Fastest",
                    "color": "#34D399",
                    "steps": [],
                }]
        active_route_payload = {
            "fromLabel": start_label,
            "toLabel": dest_label,
            "selectedIdx": 0,
            "routeMode": "driving",
            "routes": active_routes,
            "savedAt": datetime.now(timezone.utc).isoformat(),
        } if active_routes else None

        # Comprehensive facility prefetch along entire route
        facilities = []
        if active_routes and active_routes[0].get("coords"):
            coords = active_routes[0]["coords"]
            total_dist = sum(_haversine_m(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1]) 
                           for i in range(len(coords)-1))
            
            # Categories to fetch
            categories = [
                {"key": "food", "emoji": "🍽️", "color": "#FFB800", "label": "Food", 
                 "q": 'node["amenity"~"restaurant|cafe|fast_food"]'},
                {"key": "hotel", "emoji": "🏨", "color": "#A855F7", "label": "Stay", 
                 "q": 'node["tourism"~"hotel|hostel|motel|guest_house"]'},
                {"key": "medical", "emoji": "🏥", "color": "#FF4E6A", "label": "Medical", 
                 "q": 'node["amenity"~"hospital|clinic|pharmacy"]'},
                {"key": "atm", "emoji": "🏧", "color": "#00D4FF", "label": "ATM", 
                 "q": 'node["amenity"="atm"]'},
                {"key": "fuel", "emoji": "⛽", "color": "#39FF6A", "label": "Fuel", 
                 "q": 'node["amenity"~"fuel|charging_station"]'},
                {"key": "transit", "emoji": "🚌", "color": "#FF8A00", "label": "Transit", 
                 "q": 'node["highway"="bus_stop"]["name"]'},
                {"key": "sights", "emoji": "🏛️", "color": "#00FFCC", "label": "Sights", 
                 "q": 'node["tourism"~"attraction|museum|viewpoint"]'},
                {"key": "police", "emoji": "🚨", "color": "#FF6ECC", "label": "Safety", 
                 "q": 'node["amenity"~"police|fire_station"]'},
            ]
            
            # Divide route into segments (1 per ~40km, max 12)
            N = min(12, max(4, int(total_dist / 40000)))
            step = max(1, len(coords) // N)
            PAD = 0.04  # ~4.5km padding
            
            seen = set()
            for cat in categories:
                cat_facilities = []
                for seg in range(N):
                    seg_start = seg * step
                    seg_end = min(seg_start + step, len(coords) - 1)
                    slice_coords = coords[seg_start:seg_end + 1]
                    
                    if not slice_coords:
                        continue
                    
                    # Calculate segment bbox
                    min_lat = min(c[0] for c in slice_coords) - PAD
                    max_lat = max(c[0] for c in slice_coords) + PAD
                    min_lng = min(c[1] for c in slice_coords) - PAD
                    max_lng = max(c[1] for c in slice_coords) + PAD
                    
                    query = f'[out:json][timeout:12];({cat["q"]}({min_lat},{min_lng},{max_lat},{max_lng}););out body 80;'
                    
                    try:
                        results = await _overpass_query(client, query)
                        for it in results:
                            if it["id"] in seen:
                                continue
                            seen.add(it["id"])
                            
                            dist_from_start = _haversine_m(coords[0][0], coords[0][1], it["lat"], it["lng"])
                            cat_facilities.append({
                                "id": it["id"],
                                "lat": it["lat"],
                                "lng": it["lng"],
                                "name": it["name"],
                                "type": it["type"],
                                "phone": it["phone"],
                                "hours": it["hours"],
                                "addr": it["addr"],
                                "dist": dist_from_start,
                                "catKey": cat["key"],
                                "catEmoji": cat["emoji"],
                                "catLabel": cat["label"],
                                "catColor": cat["color"],
                            })
                        
                        # Rate limiting between segments
                        if seg < N - 1:
                            await asyncio.sleep(0.25)
                    except Exception as e:
                        print(f"Error fetching {cat['label']} segment {seg}: {e}")
                        continue
                
                # Sort by distance and limit per category
                cat_facilities.sort(key=lambda x: x["dist"])
                facilities.extend(cat_facilities[:60])  # Max 60 per category
            
            # Sort all facilities by distance from start
            facilities.sort(key=lambda x: x["dist"])
            facilities = facilities[:600]  # Cap at 600 total
        else:
            # Fallback to destination-centric if no route
            lat, lng = to_coord["lat"], to_coord["lng"]
            min_lat, max_lat = lat - 0.01, lat + 0.01
            min_lng, max_lng = lng - 0.01, lng + 0.01
            q = (
                f'[out:json][timeout:15];'
                f'(node["tourism"]["tourism"!~"museum|artwork|attraction"]({min_lat},{min_lng},{max_lat},{max_lng});'
                f'node["amenity"~"restaurant|cafe|hospital|clinic|pharmacy|atm|fuel|charging_station"]({min_lat},{min_lng},{max_lat},{max_lng});'
                f'node["highway"="bus_stop"]["name"]({min_lat},{min_lng},{max_lat},{max_lng});'
                f');out body 50;'
            )
            facilities_raw = await _overpass_query(client, q)
            for it in facilities_raw:
                ck, ce, cl, cc = _facility_cat_from_type(it.get("type"))
                facilities.append({
                    "id": it.get("id"),
                    "lat": it.get("lat"),
                    "lng": it.get("lng"),
                    "name": it.get("name"),
                    "type": it.get("type"),
                    "phone": it.get("phone"),
                    "hours": it.get("hours"),
                    "addr": it.get("addr"),
                    "dist": 0,
                    "catKey": ck,
                    "catEmoji": ce,
                    "catLabel": cl,
                    "catColor": cc,
                })

        # Calculate proper bounding box from route or destination
        if active_routes and active_routes[0].get("coords"):
            coords = active_routes[0]["coords"]
            lats = [c[0] for c in coords]
            lngs = [c[1] for c in coords]
            min_lat, max_lat = min(lats) - 0.03, max(lats) + 0.03
            min_lng, max_lng = min(lngs) - 0.03, max(lngs) + 0.03
            center = [sum(lats) / len(lats), sum(lngs) / len(lngs)]
        else:
            lat, lng = to_coord["lat"], to_coord["lng"]
            min_lat, max_lat = lat - 0.01, lat + 0.01
            min_lng, max_lng = lng - 0.01, lng + 0.01
            center = [lat, lng]

        return {
            "active_route": active_route_payload,
            "map_bbox": {
                "minLat": min_lat,
                "maxLat": max_lat,
                "minLng": min_lng,
                "maxLng": max_lng,
                "center": center,
            },
            "preloaded_facilities": facilities,
        }


async def _overpass_query(client: httpx.AsyncClient, q: str):
    for mirror in OVERPASS_MIRRORS:
        try:
            res = await client.post(
                mirror,
                data={"data": q},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=18,
            )
            if res.status_code == 200:
                return _parse_elements((res.json() or {}).get("elements"))
        except Exception:
            continue
    return []


async def _call_ai(prompt: str):
    _log = logging.getLogger("travelmate.warmup")
    settings = get_settings()
    gemini_ok = settings.gemini_api_key and settings.gemini_api_key != "your-gemini-api-key-here"
    grok_ok = settings.grok_api_key and settings.grok_api_key != "your-grok-api-key-here"
    groq_ok = settings.groq_api_key and settings.groq_api_key != "your-groq-api-key-here"

    if gemini_ok:
        try:
            payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.7, "maxOutputTokens": 16384}}
            async with httpx.AsyncClient(timeout=90) as client:
                res = await client.post(f"{GEMINI_URL}?key={settings.gemini_api_key}", json=payload)
                if res.status_code == 200:
                    data = res.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                _log.warning("Gemini failed (%s) in warmup, trying fallback", res.status_code)
        except Exception as exc:
            _log.warning("Gemini exception in warmup: %s", exc)

    if groq_ok:
        try:
            GROQ_TPM_LIMIT = 5800
            estimated_input = len(prompt) // 4
            groq_max_tokens = max(300, GROQ_TPM_LIMIT - estimated_input)
            payload = {
                "model": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": groq_max_tokens,
            }
            async with httpx.AsyncClient(timeout=90) as client:
                res = await client.post(
                    GROQ_URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {settings.groq_api_key}", "Content-Type": "application/json"},
                )
                if res.status_code == 200:
                    return res.json()["choices"][0]["message"]["content"]
                _log.warning("Groq failed (%s) in warmup", res.status_code)
        except Exception as exc:
            _log.warning("Groq exception in warmup: %s", exc)

    if grok_ok:
        try:
            payload = {
                "model": "grok-3-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": 2048,
            }
            async with httpx.AsyncClient(timeout=90) as client:
                res = await client.post(
                    GROK_URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {settings.grok_api_key}", "Content-Type": "application/json"},
                )
                if res.status_code == 200:
                    return res.json()["choices"][0]["message"]["content"]
                _log.warning("Grok failed (%s) in warmup", res.status_code)
        except Exception as exc:
            _log.warning("Grok exception in warmup: %s", exc)

    return None


def _parse_ai_json(text: str) -> dict | None:
    """Robustly parse AI-returned JSON, repairing truncated responses."""
    import re
    cleaned = re.sub(r"```json|```", "", text or "").strip()
    if not cleaned:
        return None

    # Try clean parse first
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Extract outermost object
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        block = cleaned[start:end + 1]
        try:
            return json.loads(block)
        except Exception:
            pass
        # Attempt to repair truncated JSON
        repaired = _repair_json(block)
        if repaired:
            return repaired

    return _repair_json(cleaned)


def _repair_json(s: str) -> dict | None:
    """Close open braces/brackets in a truncated JSON string and try to parse."""
    s = s.strip()
    if not s.startswith("{"):
        i = s.find("{")
        if i == -1:
            return None
        s = s[i:]
    braces = brackets = 0
    in_string = escape_next = False
    for ch in s:
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            braces += 1
        elif ch == "}":
            braces -= 1
        elif ch == "[":
            brackets += 1
        elif ch == "]":
            brackets -= 1

    repaired = s.rstrip().rstrip(",").rstrip(":")
    if in_string:
        repaired += '"'
    repaired += "]" * max(0, brackets)
    repaired += "}" * max(0, braces)
    try:
        result = json.loads(repaired)
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def _detect_route_type(route_start: str, route_destination: str, dist_km: float, has_route: bool) -> dict:
    """Classify the trip to help AI generate appropriate plans."""
    start_l = route_start.lower()
    dest_l = route_destination.lower()

    # Country/region keyword lists
    india_kw = ["india", "delhi", "mumbai", "chennai", "bangalore", "kolkata", "hyderabad",
                "pune", "ahmedabad", "kochi", "jaipur", "lucknow", "surat", "tamil", "kerala",
                "karnataka", "maharashtra", "gujarat", "rajasthan", "thoothukudi", "madurai",
                "coimbatore", "trichy", "vizag", "visakhapatnam", "bhubaneswar", "patna", "guwahati"]
    japan_kw = ["japan", "tokyo", "osaka", "kyoto", "hiroshima", "nagoya", "sapporo", "fukuoka"]
    sea_kw = ["thailand", "bangkok", "singapore", "malaysia", "kuala lumpur", "vietnam", "hanoi",
              "ho chi minh", "indonesia", "bali", "jakarta", "myanmar", "yangon", "cambodia",
              "philippines", "manila", "laos"]
    china_kw = ["china", "beijing", "shanghai", "guangzhou", "shenzhen", "chengdu", "xi'an"]
    europe_kw = ["paris", "london", "rome", "berlin", "amsterdam", "barcelona", "madrid",
                 "vienna", "prague", "zurich", "europe", "uk", "france", "germany", "italy", "spain"]
    us_kw = ["new york", "los angeles", "chicago", "houston", "usa", "united states", "america",
             "san francisco", "seattle", "miami", "boston", "washington"]
    gulf_kw = ["dubai", "abu dhabi", "doha", "riyadh", "uae", "qatar", "saudi", "bahrain", "kuwait", "oman", "muscat"]

    def match(text, kws): return any(k in text for k in kws)

    start_india = match(start_l, india_kw)
    dest_japan = match(dest_l, japan_kw)
    dest_india = match(dest_l, india_kw)
    start_japan = match(start_l, japan_kw)

    # Determine if ocean crossing is required (no road route possible)
    ocean_pairs = [
        (start_india or match(start_l, sea_kw + china_kw), dest_japan or start_japan),
        (match(start_l, us_kw + europe_kw), match(dest_l, india_kw + japan_kw + sea_kw)),
        (match(start_l, india_kw + sea_kw + china_kw), match(dest_l, us_kw + europe_kw)),
    ]
    needs_ocean_crossing = not has_route and any(a and b for a, b in ocean_pairs)
    needs_ocean_crossing = needs_ocean_crossing or (not has_route and dist_km > 3000)

    # Determine rough currency — for international trips use destination currency,
    # for domestic use the single-country currency.
    is_domestic = (start_india and dest_india) or (start_japan and dest_japan)
    if is_domestic:
        if start_india and dest_india:
            currency = "INR (₹)"
        elif start_japan and dest_japan:
            currency = "JPY (¥)"
        else:
            currency = "USD ($)"
    else:
        # International: prefer destination currency so prices are realistic there
        if dest_japan or match(dest_l, japan_kw):
            currency = "JPY (¥)"
        elif match(dest_l, gulf_kw):
            currency = "AED/USD"
        elif match(dest_l, europe_kw):
            currency = "EUR (€)"
        elif match(dest_l, us_kw):
            currency = "USD ($)"
        elif dest_india or match(dest_l, india_kw):
            currency = "INR (₹)"
        else:
            currency = "USD ($)"

    # Build a realistic multi-modal path description
    modal_hint = ""
    if start_india and dest_japan:
        modal_hint = (
            "The only realistic route is: drive/train within India to Chennai or Delhi → "
            "direct flight to Tokyo (Narita/Haneda). If the traveler insists on maximum land travel: "
            "Chennai → Kolkata by road/train → overland through Myanmar and China (Yunnan) → "
            "ferry or flight from Shanghai/Qingdao to Osaka/Tokyo. "
            "Car shipping from India to Japan costs ~$3,000–5,000 USD and takes 4–6 weeks by sea freight."
        )
    elif not has_route and dist_km > 2000:
        modal_hint = (
            f"This {round(dist_km)} km journey cannot be completed entirely by road. "
            "Plan a mix of road, rail, and flights. Include border crossings, visa requirements, "
            "and realistic daily distances of 300–500 km on drivable legs."
        )

    return {
        "needs_ocean_crossing": needs_ocean_crossing,
        "currency": currency,
        "modal_hint": modal_hint,
        "is_international": not (start_india and dest_india),
    }


async def _analyze_trip(trip: Trip, dist_km: float, dur_h: float, milestones, route_start: str, route_destination: str, has_route: bool = True):
    trip_days = 1
    if trip.start_date and trip.end_date:
        try:
            sd = datetime.fromisoformat(str(trip.start_date))
            ed = datetime.fromisoformat(str(trip.end_date))
            trip_days = max(1, (ed - sd).days)
        except Exception:
            trip_days = max(1, int(dist_km // 450) + 1) if dist_km > 0 else 7
    else:
        trip_days = max(1, int(dist_km // 450) + 1) if dist_km > 0 else 7

    route_info = _detect_route_type(route_start, route_destination, dist_km, has_route)
    currency = route_info["currency"]
    modal_hint = route_info["modal_hint"]
    needs_ocean = route_info["needs_ocean_crossing"]

    milestones_block = ""
    if milestones:
        milestones_block = f"REAL STOPS FOUND ALONG ROUTE ({len(milestones)}):\n" + \
            "\n".join([f"- [{m['category'].upper()}] {m['name']} at ~{round(m['distM']/1000)}km" for m in milestones[:20]])
    else:
        milestones_block = "No road stops pre-fetched (likely international/multi-modal route — generate realistic stops from your knowledge)."

    ocean_note = ""
    if needs_ocean:
        ocean_note = f"""
⚠️ IMPORTANT — IMPOSSIBLE ROAD ROUTE:
This trip cannot be driven continuously. {modal_hint}
You MUST design a multi-modal itinerary (road + train + flight/ferry). 
Be very specific: name real airports, train stations, ferry ports, highways, and border crossings.
For each day specify the journeyMode as "driving", "train", "flight", or "ferry" accurately."""
    days_placeholder = ",".join(
        f'{{"day":{d},"title":"","journeyMode":"","morningTip":"","eveningTip":"","stayRecommendation":"","meals":["","",""],"highlights":["","",""],"routeItems":[{{"category":"sight|food|stay|fuel","name":"","note":""}}]}}'
        for d in range(1, trip_days + 1)
    )
    prompt = f"""Trip: {route_start}->{route_destination}, {trip_days} days, budget {currency} {format(int(trip.budget), ",") if trip.budget else "?"}.{ocean_note}{(" Notes: "+trip.description) if trip.description else ""}
{milestones_block}
Reply ONLY with this exact JSON structure filled in for ALL {trip_days} days:
{{"tripInsight":"","bestTimeToLeave":"","weatherNote":"","budgetAlert":"","travelTips":["",""],"days":[{days_placeholder}]}}
CRITICAL: The days array MUST have exactly {trip_days} objects (day 1 through {trip_days}). Fill every field with real content.
Rules: Real place/hotel/restaurant names. meals=3 strings. highlights=3 strings. {currency} prices. Meal format: "Name (price)" e.g. "Ichiran Ramen (¥1200)"."""



    text = await _call_ai(prompt)
    if not text:
        return None
    return _parse_ai_json(text)


async def _build_ai_only_roadmap(trip: Trip, start_label: str, dest_label: str, from_coord: dict, to_coord: dict) -> dict | None:
    """Generate a fully AI-authored itinerary for routes where no drivable road exists (ocean crossings, etc.)."""
    dist_km = _haversine_m(from_coord["lat"], from_coord["lng"], to_coord["lat"], to_coord["lng"]) / 1000
    dur_h = dist_km / 80  # rough estimate for mixed travel

    trip_days = 7  # default for international
    if trip.start_date and trip.end_date:
        try:
            sd = datetime.fromisoformat(str(trip.start_date))
            ed = datetime.fromisoformat(str(trip.end_date))
            trip_days = max(1, (ed - sd).days)
        except Exception:
            pass

    insight = await _analyze_trip(trip, dist_km, dur_h, [], start_label, dest_label, has_route=False)
    if not insight:
        return None

    ai_days = insight.get("days") or []
    # Build synthetic day segments from AI days
    day_groups = []
    seg_dist = dist_km * 1000 / max(len(ai_days), 1) if ai_days else dist_km * 1000
    for i, ai_day in enumerate(ai_days):
        items = []
        for ri in (ai_day.get("routeItems") or []):
            cat = ri.get("category", "sight")
            items.append({
                "name": ri.get("name", ""),
                "category": cat,
                "distM": int((i + 0.5) * seg_dist),
                "placeType": {"stay": "Hotel", "food": "Restaurant", "sight": "Attraction", "fuel": "Transport"}.get(cat, "Attraction"),
                "note": ri.get("note", ""),
                "lat": to_coord["lat"],
                "lng": to_coord["lng"],
                "addr": ri.get("address", ""),
            })
        # Fallback: synthesize from highlights/meals/stay
        if not items:
            for h in (ai_day.get("highlights") or []):
                items.append({"name": h, "category": "sight", "distM": int((i + 0.5) * seg_dist), "placeType": "Attraction", "note": "", "lat": to_coord["lat"], "lng": to_coord["lng"]})
            for m in (ai_day.get("meals") or []):
                items.append({"name": m, "category": "food", "distM": int((i + 0.5) * seg_dist), "placeType": "Restaurant", "note": "", "lat": to_coord["lat"], "lng": to_coord["lng"]})
            if ai_day.get("stayRecommendation"):
                items.append({"name": ai_day["stayRecommendation"], "category": "stay", "distM": int((i + 0.5) * seg_dist), "placeType": "Hotel", "note": "", "lat": to_coord["lat"], "lng": to_coord["lng"]})

        day_no = ai_day.get("day", i + 1)
        day_groups.append({
            "day": day_no,
            "distStart": int(i * seg_dist),
            "distEnd": int((i + 1) * seg_dist),
            "items": items,
            "title": ai_day.get("title", f"Day {day_no}"),
            "journeyMode": ai_day.get("journeyMode", "mixed"),
        })

    milestones = sorted(
        [item for dg in day_groups for item in dg["items"]],
        key=lambda x: x.get("distM", 0)
    )

    plan = {
        "distKm": round(dist_km),
        "durH": round(dur_h, 1),
        "days": day_groups,
        "milestones": milestones,
        "destination": dest_label,
        "start": start_label,
        "multiModal": True,
    }

    signature = roadmap_signature_for_trip(trip)
    return {
        "plan": plan,
        "insight": insight,
        "start": start_label,
        "destination": dest_label,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "signature": signature,
    }


async def _build_trip_roadmap_payload(trip: Trip):
    if not _ai_available():
        return None
    start_label, dest_label = _resolved_route_endpoints(trip)
    if not (start_label and dest_label):
        return None
    async with httpx.AsyncClient(timeout=35) as client:
        from_coord = await _geocode_city(client, start_label)
        to_coord = await _geocode_city(client, dest_label)
        if not from_coord or not to_coord:
            return None
        route = await _fetch_route(client, from_coord, to_coord)

        # ── No drivable route found (ocean crossing / international) ──────────
        if not route:
            return await _build_ai_only_roadmap(trip, start_label, dest_label, from_coord, to_coord)

        coords = route["coords"]
        dist_km = route["dist_km"]
        dur_h = route["dur_h"]
        total_m = dist_km * 1000

        queries = []
        stay_every = 250000
        food_every = 150000
        sight_every = 100000
        hosp_every = 200000

        stay_count = int(total_m // stay_every)
        food_count = max(0, int(total_m // food_every) - 1)
        sight_count = max(1, int(total_m // sight_every))
        hosp_count = max(1, int(total_m // hosp_every))

        for i in range(1, stay_count + 1):
            d = i * stay_every
            if d >= total_m - 30000:
                continue
            lat, lng = _point_at_dist(coords, d)
            queries.append({
                "distM": d,
                "category": "stay",
                "radius": 8000,
                "lat": lat,
                "lng": lng,
                "q": f'[out:json][timeout:12];(node["tourism"~"hotel|hostel|motel|guest_house"](around:8000,{lat},{lng}););out body 30;',
            })

        for i in range(1, food_count + 1):
            d = i * food_every + food_every * 0.3
            if d >= total_m - 20000:
                continue
            lat, lng = _point_at_dist(coords, d)
            queries.append({
                "distM": d,
                "category": "food",
                "radius": 3000,
                "lat": lat,
                "lng": lng,
                "q": f'[out:json][timeout:12];(node["amenity"~"restaurant|cafe|dhaba"](around:3000,{lat},{lng}););out body 30;',
            })

        for i in range(sight_count):
            d = (i + 0.5) * sight_every
            if d >= total_m:
                continue
            lat, lng = _point_at_dist(coords, d)
            queries.append({
                "distM": d,
                "category": "sight",
                "radius": 10000,
                "lat": lat,
                "lng": lng,
                "q": f'[out:json][timeout:12];(node["tourism"~"attraction|museum|viewpoint"](around:10000,{lat},{lng}););out body 30;',
            })

        for i in range(hosp_count):
            d = (i + 0.4) * hosp_every
            if d >= total_m:
                continue
            lat, lng = _point_at_dist(coords, d)
            queries.append({
                "distM": d,
                "category": "hospital_fuel",
                "radius": 5000,
                "lat": lat,
                "lng": lng,
                "q": f'[out:json][timeout:12];(node["amenity"~"hospital|clinic|fuel"](around:5000,{lat},{lng}););out body 20;',
            })

        milestones = []
        concurrency = 6
        for i in range(0, len(queries), concurrency):
            batch = queries[i:i + concurrency]
            results = await asyncio.gather(*[_overpass_query(client, q["q"]) for q in batch], return_exceptions=True)
            for idx, found in enumerate(results):
                if isinstance(found, Exception):
                    continue
                q_obj = batch[idx]
                best = _pick_best(found, q_obj["lat"], q_obj["lng"], q_obj["radius"])
                if not best:
                    continue
                if q_obj["category"] == "hospital_fuel":
                    is_fuel = best.get("type") == "fuel"
                    milestones.append({
                        **best,
                        "category": "fuel" if is_fuel else "hospital",
                        "distM": q_obj["distM"],
                        "placeType": "Transport" if is_fuel else "Hospital",
                        "note": f"Emergency stop ~{round(q_obj['distM']/1000)}km from start",
                    })
                else:
                    labels = {
                        "stay": {"placeType": "Hotel", "note": f"Night stay ~{round(q_obj['distM']/1000)}km from start"},
                        "food": {"placeType": "Restaurant", "note": f"Food stop ~{round(q_obj['distM']/1000)}km from start"},
                        "sight": {"placeType": "Attraction", "note": f"Sight at ~{round(q_obj['distM']/1000)}km"},
                    }
                    milestones.append({**best, "category": q_obj["category"], "distM": q_obj["distM"], **labels.get(q_obj["category"], {})})

        milestones.sort(key=lambda m: m["distM"])

        km_per_day = 500
        day_groups = []
        cur = {"day": 1, "distStart": 0, "distEnd": km_per_day * 1000, "items": []}
        for m in milestones:
            while m["distM"] > cur["distEnd"]:
                day_groups.append(cur)
                cur = {"day": cur["day"] + 1, "distStart": cur["distEnd"], "distEnd": cur["distEnd"] + km_per_day * 1000, "items": []}
            cur["items"].append(m)
        day_groups.append(cur)

        plan = {
            "distKm": round(dist_km),
            "durH": round(dur_h, 1),
            "days": [d for d in day_groups if d["items"]],
            "milestones": milestones,
            "destination": dest_label,
            "start": start_label,
        }
        insight = await _analyze_trip(trip, dist_km, dur_h, milestones, start_label, dest_label, has_route=True)
        if not insight:
            return None
        signature = roadmap_signature_for_trip(trip)
        return {
            "plan": plan,
            "insight": insight,
            "start": start_label,
            "destination": dest_label,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "signature": signature,
        }


async def _seed_places_and_itinerary_if_empty(db: AsyncSession, trip: Trip, payload: dict):
    """Add sample places/itinerary if trip is empty and AI roadmap was generated."""
    try:
        # Extract milestones and days from the payload dict
        plan = payload.get("plan") or {}
        milestones = plan.get("milestones") or []
        insight = payload.get("insight") or {}
        days = insight.get("days") or []

        # Use a separate session with retry logic for this background task
        max_retries = 3
        for attempt in range(max_retries):
            try:
                existing_places = list((await db.execute(select(Place).where(Place.trip_id == trip.id))).scalars().all())
                if not existing_places and milestones:
                    for idx, m in enumerate(milestones[:120]):
                        db.add(Place(
                            trip_id=trip.id,
                            name=m.get("name") or f"Stop {idx + 1}",
                            place_type=m.get("placeType") or "Attraction",
                            address=m.get("addr") or "",
                            notes=f"[auto-roadmap] {m.get('note') or ''}".strip(),
                            latitude=m.get("lat"),
                            longitude=m.get("lng"),
                            status="planned",
                            order_idx=idx,
                        ))
                
                existing_days = list((await db.execute(select(ItineraryDay).where(ItineraryDay.trip_id == trip.id))).scalars().all())
                if not existing_days and days:
                    next_day = 1
                    for d in days[:30]:
                        db.add(ItineraryDay(
                            trip_id=trip.id,
                            day_number=next_day,
                            date_label=d.get("date_label") or d.get("date"),  # model uses date_label
                            title=d.get("title", f"Day {next_day}"),
                            notes=d.get("notes", ""),
                        ))
                        next_day += 1
                
                await db.flush()
                return  # Success, exit retry loop
                
            except Exception as db_error:
                if "timeout" in str(db_error).lower() or "connection" in str(db_error).lower():
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
                        continue
                    raise db_error
                else:
                    raise db_error
                    
    except Exception as e:
        # Log error but don't crash the app
        print(f"Background seeding failed for trip {trip.id}: {e}")
        return


async def precompute_trip_roadmap(trip_id: int, force: bool = False):
    async with SessionLocal() as db:
        r = await db.execute(select(Trip).where(Trip.id == trip_id))
        trip = r.scalar_one_or_none()
        if not trip:
            return False
        start_label, dest_label = _resolved_route_endpoints(trip)
        if not (start_label and dest_label):
            return False
        if not force and _is_cached_fresh(trip):
            return True
        payload = await _build_trip_roadmap_payload(trip)
        if not payload:
            return False
        trip.ai_roadmap = json.dumps(payload)
        await db.flush()
        await db.commit()
        return True


async def warmup_trip_all_data(trip_id: int, force: bool = False):
    # Acquire semaphore so only one warmup runs at a time across all background tasks.
    # Without this, simultaneous warmups for multiple trips all hit Nominatim/OSRM/Overpass
    # at once, triggering rate limits (429s) and making everything slow.
    async with _warmup_sem:
        async with SessionLocal() as db:
            r = await db.execute(select(Trip).where(Trip.id == trip_id))
            trip = r.scalar_one_or_none()
            if not trip:
                return False
            start_label, dest_label = _resolved_route_endpoints(trip)
            if not (start_label and dest_label):
                return False

            changed = False

            # 1) LiveMap warmup (if missing).
            if force or not _is_livemap_cached(trip):
                livemap = await _build_livemap_payload(trip)
                if livemap:
                    if livemap.get("active_route"):
                        trip.active_route = json.dumps(livemap["active_route"])
                    trip.map_bbox = json.dumps(livemap["map_bbox"])
                    trip.preloaded_facilities = json.dumps(livemap["preloaded_facilities"])
                    changed = True

            # 2) AI roadmap warmup (stale-or-missing).
            if force or not _is_cached_fresh(trip):
                try:
                    payload = await _build_trip_roadmap_payload(trip)
                    if payload:
                        trip.ai_roadmap = json.dumps(payload)
                        await _seed_places_and_itinerary_if_empty(db, trip, payload)
                        changed = True
                except Exception as roadmap_error:
                    print(f"Roadmap generation failed for trip {trip.id}: {roadmap_error}")
                    # Don't fail startup, just continue without roadmap

            if changed:
                await db.flush()
                await db.commit()
            return True