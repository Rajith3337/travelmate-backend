import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["Weather"])


@router.get("/geocode")
async def geocode_proxy(city: str):
    """Proxy geocoding requests to Open-Meteo, returns {results:[{latitude,longitude,name,...}]}"""
    if not city or not city.strip():
        raise HTTPException(400, "city parameter is required")

    url = f"https://geocoding-api.open-meteo.com/v1/search?name={city.strip()}&count=6&language=en&format=json"
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            res = await client.get(url)
            if res.status_code != 200:
                raise HTTPException(502, f"Geocoding service returned {res.status_code}")
            data = res.json()
            # Open-Meteo omits the "results" key entirely when nothing is found —
            # normalise to always return {results: [...]} so the frontend can rely on it.
            results = data.get("results") or []
            return {"results": results}
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(504, "Geocoding service timed out — please try again")
    except httpx.RequestError as ex:
        raise HTTPException(502, f"Could not reach geocoding service: {str(ex)}")
    except Exception as ex:
        raise HTTPException(502, f"Geocoding error: {str(ex)}")


@router.get("/forecast")
async def weather_proxy(lat: float, lng: float):
    """Proxy weather forecast requests to Open-Meteo"""
    # Validate coordinates
    if not (-90 <= lat <= 90):
        raise HTTPException(400, f"Invalid latitude: {lat}")
    if not (-180 <= lng <= 180):
        raise HTTPException(400, f"Invalid longitude: {lng}")

    # Round to 4 decimal places — sufficient accuracy (~11 m)
    lat = round(lat, 4)
    lng = round(lng, 4)
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lng}"
        f"&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,"
        f"wind_direction_10m,relative_humidity_2m,precipitation,pressure_msl,uv_index"
        f"&hourly=temperature_2m,precipitation_probability,weather_code,uv_index"
        f"&daily=weather_code,temperature_2m_max,temperature_2m_min,"
        f"precipitation_probability_max,sunrise,sunset"
        f"&timezone=auto"
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            res = await client.get(url)
            if res.status_code != 200:
                raise HTTPException(502, f"Weather service returned {res.status_code}")
            return res.json()
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(504, "Weather service timed out — please try again")
    except httpx.RequestError as ex:
        raise HTTPException(502, f"Could not reach weather service: {str(ex)}")
    except Exception as ex:
        raise HTTPException(502, f"Weather error: {str(ex)}")