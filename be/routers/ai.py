import asyncio
import json
import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from db.session import get_db
from models.models import AIChat, ChecklistItem, Expense, ItineraryDay, Note, Place, Trip, User, now
from schemas.schemas import AIChatIn, AIChatOut, AIRequest, AIResponse, RoadmapPrecomputeOut, RoadmapStatusOut, RoadmapTripStatusOut
from services.deps import current_user
from services.roadmap_precompute import classify_trip_warmup, trip_warmup_status, warmup_trip_all_data

router = APIRouter(tags=["AI"])
settings = get_settings()
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
GROK_URL = "https://api.x.ai/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
AI_HTTP_TIMEOUT = 90

TRIP_STATUS_VALUES = {"planning", "upcoming", "active", "completed"}
PLACE_STATUS_VALUES = {"planned", "upcoming", "visited", "skipped"}
EXPENSE_CATEGORY_VALUES = {"accommodation", "transport", "food", "tickets", "shopping", "activities", "entertainment", "other"}

TOOLS = {
    "functionDeclarations": [
        {
            "name": "manage_trip",
            "description": "Create, update, or delete a trip.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "op": {"type": "STRING", "description": "create|update|delete"},
                    "trip_id": {"type": "INTEGER"},
                    "name": {"type": "STRING"},
                    "destination": {"type": "STRING"},
                    "start_location": {"type": "STRING"},
                    "description": {"type": "STRING"},
                    "start_date": {"type": "STRING"},
                    "end_date": {"type": "STRING"},
                    "status": {"type": "STRING"},
                    "budget": {"type": "NUMBER"},
                },
                "required": ["op"],
            },
        },
        {
            "name": "manage_place",
            "description": "Create, update, or delete a place in a trip.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "op": {"type": "STRING", "description": "create|update|delete"},
                    "trip_id": {"type": "INTEGER"},
                    "place_id": {"type": "INTEGER"},
                    "name": {"type": "STRING"},
                    "place_type": {"type": "STRING"},
                    "address": {"type": "STRING"},
                    "notes": {"type": "STRING"},
                    "latitude": {"type": "NUMBER"},
                    "longitude": {"type": "NUMBER"},
                    "rating": {"type": "NUMBER"},
                    "visit_time": {"type": "STRING"},
                    "status": {"type": "STRING"},
                    "order_idx": {"type": "INTEGER"},
                },
                "required": ["op", "trip_id"],
            },
        },
        {
            "name": "manage_itinerary_day",
            "description": "Create, update, or delete an itinerary day.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "op": {"type": "STRING", "description": "create|update|delete"},
                    "trip_id": {"type": "INTEGER"},
                    "day_id": {"type": "INTEGER"},
                    "day_number": {"type": "INTEGER"},
                    "date_label": {"type": "STRING"},
                    "title": {"type": "STRING"},
                    "notes": {"type": "STRING"},
                    "place_names": {"type": "STRING"},
                },
                "required": ["op", "trip_id"],
            },
        },
        {
            "name": "manage_note",
            "description": "Create, update, or delete a note.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "op": {"type": "STRING", "description": "create|update|delete"},
                    "trip_id": {"type": "INTEGER"},
                    "note_id": {"type": "INTEGER"},
                    "title": {"type": "STRING"},
                    "content": {"type": "STRING"},
                    "color": {"type": "STRING"},
                    "pinned": {"type": "BOOLEAN"},
                },
                "required": ["op", "trip_id"],
            },
        },
        {
            "name": "manage_checklist_item",
            "description": "Create, update, or delete a checklist item.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "op": {"type": "STRING", "description": "create|update|delete"},
                    "trip_id": {"type": "INTEGER"},
                    "item_id": {"type": "INTEGER"},
                    "text": {"type": "STRING"},
                    "category": {"type": "STRING"},
                    "order_idx": {"type": "INTEGER"},
                    "done": {"type": "BOOLEAN"},
                },
                "required": ["op", "trip_id"],
            },
        },
        {
            "name": "manage_expense",
            "description": "Create, update, or delete an expense.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "op": {"type": "STRING", "description": "create|update|delete"},
                    "trip_id": {"type": "INTEGER"},
                    "expense_id": {"type": "INTEGER"},
                    "title": {"type": "STRING"},
                    "amount": {"type": "NUMBER"},
                    "category": {"type": "STRING"},
                    "notes": {"type": "STRING"},
                },
                "required": ["op", "trip_id"],
            },
        },
    ]
}


def _as_int(v):
    try:
        return int(v)
    except Exception:
        return None


def _as_float(v):
    try:
        return float(v)
    except Exception:
        return None


def _as_bool(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    txt = str(v).strip().lower()
    if txt in {"1", "true", "yes", "y", "on"}:
        return True
    if txt in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _clean(v):
    if v is None:
        return None
    t = str(v).strip()
    return t or None


def _op(v):
    t = str(v or "").strip().lower()
    return t if t in {"create", "update", "delete"} else None


def _trip_status(v):
    t = _clean(v)
    return t if t in TRIP_STATUS_VALUES else None


def _place_status(v):
    t = _clean(v)
    return t if t in PLACE_STATUS_VALUES else None


def _expense_category(v):
    t = _clean(v)
    return t if t in EXPENSE_CATEGORY_VALUES else "other"


async def _owned_trip(db: AsyncSession, user: User, trip_id):
    tid = _as_int(trip_id)
    if tid is None:
        return None
    r = await db.execute(select(Trip).where(Trip.id == tid, Trip.owner_id == user.id))
    return r.scalar_one_or_none()


async def _manage_trip(db: AsyncSession, user: User, args: dict):
    op = _op(args.get("op"))
    if op == "create":
        name = _clean(args.get("name"))
        destination = _clean(args.get("destination"))
        if not name or not destination:
            return {"status": "error", "message": "name and destination are required."}
        trip = Trip(
            owner_id=user.id,
            name=name,
            destination=destination,
            start_location=_clean(args.get("start_location")),
            description=_clean(args.get("description")),
            start_date=_clean(args.get("start_date")),
            end_date=_clean(args.get("end_date")),
            status=_trip_status(args.get("status")) or "planning",
            budget=_as_float(args.get("budget")) or 0.0,
            cover_emoji="✨",
            cover_color="#A855F7",
        )
        db.add(trip)
        await db.flush()
        await db.refresh(trip)
        return {"status": "success", "trip_id": trip.id, "message": "Trip created successfully."}
    trip = await _owned_trip(db, user, args.get("trip_id"))
    if not trip:
        return {"status": "error", "message": "Trip not found or permission denied."}
    if op == "update":
        if "name" in args:
            trip.name = _clean(args.get("name")) or trip.name
        if "destination" in args:
            trip.destination = _clean(args.get("destination")) or trip.destination
        if "start_location" in args:
            trip.start_location = _clean(args.get("start_location"))
        if "description" in args:
            trip.description = _clean(args.get("description"))
        if "start_date" in args:
            trip.start_date = _clean(args.get("start_date"))
        if "end_date" in args:
            trip.end_date = _clean(args.get("end_date"))
        if "status" in args:
            status = _trip_status(args.get("status"))
            if status:
                trip.status = status
        if "budget" in args:
            budget = _as_float(args.get("budget"))
            if budget is not None:
                trip.budget = budget
        await db.flush()
        return {"status": "success", "message": f"Trip {trip.id} updated."}
    if op == "delete":
        trip_id = trip.id
        await db.delete(trip)
        await db.flush()
        return {"status": "success", "message": f"Trip {trip_id} deleted."}
    return {"status": "error", "message": "Invalid op for manage_trip."}


async def _manage_place(db: AsyncSession, user: User, args: dict):
    op = _op(args.get("op"))
    trip = await _owned_trip(db, user, args.get("trip_id"))
    if not trip:
        return {"status": "error", "message": "Trip not found or permission denied."}
    if op == "create":
        name = _clean(args.get("name"))
        if not name:
            return {"status": "error", "message": "name is required."}
        place = Place(
            trip_id=trip.id, name=name,
            place_type=_clean(args.get("place_type")) or "Attraction",
            address=_clean(args.get("address")),
            notes=_clean(args.get("notes")),
            latitude=_as_float(args.get("latitude")),
            longitude=_as_float(args.get("longitude")),
            rating=_as_float(args.get("rating")),
            visit_time=_clean(args.get("visit_time")),
            status=_place_status(args.get("status")) or "planned",
            order_idx=_as_int(args.get("order_idx")) or 0,
        )
        db.add(place)
        await db.flush()
        await db.refresh(place)
        return {"status": "success", "place_id": place.id, "message": "Place created."}
    place_id = _as_int(args.get("place_id"))
    if place_id is None:
        return {"status": "error", "message": "place_id is required for update/delete."}
    r = await db.execute(select(Place).where(Place.id == place_id, Place.trip_id == trip.id))
    place = r.scalar_one_or_none()
    if not place:
        return {"status": "error", "message": "Place not found."}
    if op == "update":
        if "name" in args:
            place.name = _clean(args.get("name")) or place.name
        if "place_type" in args:
            place.place_type = _clean(args.get("place_type")) or place.place_type
        if "address" in args:
            place.address = _clean(args.get("address"))
        if "notes" in args:
            place.notes = _clean(args.get("notes"))
        if "latitude" in args:
            place.latitude = _as_float(args.get("latitude"))
        if "longitude" in args:
            place.longitude = _as_float(args.get("longitude"))
        if "rating" in args:
            place.rating = _as_float(args.get("rating"))
        if "visit_time" in args:
            place.visit_time = _clean(args.get("visit_time"))
        if "status" in args:
            status = _place_status(args.get("status"))
            if status:
                place.status = status
        if "order_idx" in args:
            idx = _as_int(args.get("order_idx"))
            if idx is not None:
                place.order_idx = idx
        await db.flush()
        return {"status": "success", "message": f"Place {place.id} updated."}
    if op == "delete":
        await db.delete(place)
        await db.flush()
        return {"status": "success", "message": f"Place {place_id} deleted."}
    return {"status": "error", "message": "Invalid op for manage_place."}


async def _manage_itinerary_day(db: AsyncSession, user: User, args: dict):
    op = _op(args.get("op"))
    trip = await _owned_trip(db, user, args.get("trip_id"))
    if not trip:
        return {"status": "error", "message": "Trip not found or permission denied."}
    if op == "create":
        day_number = _as_int(args.get("day_number"))
        title = _clean(args.get("title"))
        if day_number is None or not title:
            return {"status": "error", "message": "day_number and title are required."}
        day = ItineraryDay(
            trip_id=trip.id, day_number=day_number, title=title,
            date_label=_clean(args.get("date_label")),
            notes=_clean(args.get("notes")),
            place_names=_clean(args.get("place_names")),
        )
        db.add(day)
        await db.flush()
        await db.refresh(day)
        return {"status": "success", "day_id": day.id, "message": "Itinerary day created."}
    day_id = _as_int(args.get("day_id"))
    if day_id is None:
        return {"status": "error", "message": "day_id is required for update/delete."}
    r = await db.execute(select(ItineraryDay).where(ItineraryDay.id == day_id, ItineraryDay.trip_id == trip.id))
    day = r.scalar_one_or_none()
    if not day:
        return {"status": "error", "message": "Itinerary day not found."}
    if op == "update":
        if "day_number" in args:
            val = _as_int(args.get("day_number"))
            if val is not None:
                day.day_number = val
        if "date_label" in args:
            day.date_label = _clean(args.get("date_label"))
        if "title" in args:
            day.title = _clean(args.get("title")) or day.title
        if "notes" in args:
            day.notes = _clean(args.get("notes"))
        if "place_names" in args:
            day.place_names = _clean(args.get("place_names"))
        await db.flush()
        return {"status": "success", "message": f"Itinerary day {day.id} updated."}
    if op == "delete":
        await db.delete(day)
        await db.flush()
        return {"status": "success", "message": f"Itinerary day {day_id} deleted."}
    return {"status": "error", "message": "Invalid op for manage_itinerary_day."}


async def _manage_note(db: AsyncSession, user: User, args: dict):
    op = _op(args.get("op"))
    trip = await _owned_trip(db, user, args.get("trip_id"))
    if not trip:
        return {"status": "error", "message": "Trip not found or permission denied."}
    if op == "create":
        title = _clean(args.get("title"))
        content = _clean(args.get("content"))
        if not title or not content:
            return {"status": "error", "message": "title and content are required."}
        note = Note(trip_id=trip.id, title=title, content=content, color=_clean(args.get("color")) or "#00D4FF", pinned=_as_bool(args.get("pinned")) or False)
        db.add(note)
        await db.flush()
        await db.refresh(note)
        return {"status": "success", "note_id": note.id, "message": "Note created."}
    note_id = _as_int(args.get("note_id"))
    if note_id is None:
        return {"status": "error", "message": "note_id is required for update/delete."}
    r = await db.execute(select(Note).where(Note.id == note_id, Note.trip_id == trip.id))
    note = r.scalar_one_or_none()
    if not note:
        return {"status": "error", "message": "Note not found."}
    if op == "update":
        if "title" in args:
            note.title = _clean(args.get("title")) or note.title
        if "content" in args:
            note.content = _clean(args.get("content")) or note.content
        if "color" in args:
            note.color = _clean(args.get("color")) or note.color
        if "pinned" in args:
            pinned = _as_bool(args.get("pinned"))
            if pinned is not None:
                note.pinned = pinned
        await db.flush()
        return {"status": "success", "message": f"Note {note.id} updated."}
    if op == "delete":
        await db.delete(note)
        await db.flush()
        return {"status": "success", "message": f"Note {note_id} deleted."}
    return {"status": "error", "message": "Invalid op for manage_note."}


async def _manage_checklist_item(db: AsyncSession, user: User, args: dict):
    op = _op(args.get("op"))
    trip = await _owned_trip(db, user, args.get("trip_id"))
    if not trip:
        return {"status": "error", "message": "Trip not found or permission denied."}
    if op == "create":
        text = _clean(args.get("text"))
        if not text:
            return {"status": "error", "message": "text is required."}
        item = ChecklistItem(trip_id=trip.id, text=text, category=_clean(args.get("category")) or "General", order_idx=_as_int(args.get("order_idx")) or 0, done=_as_bool(args.get("done")) or False)
        db.add(item)
        await db.flush()
        await db.refresh(item)
        return {"status": "success", "item_id": item.id, "message": "Checklist item created."}
    item_id = _as_int(args.get("item_id"))
    if item_id is None:
        return {"status": "error", "message": "item_id is required for update/delete."}
    r = await db.execute(select(ChecklistItem).where(ChecklistItem.id == item_id, ChecklistItem.trip_id == trip.id))
    item = r.scalar_one_or_none()
    if not item:
        return {"status": "error", "message": "Checklist item not found."}
    if op == "update":
        if "text" in args:
            item.text = _clean(args.get("text")) or item.text
        if "category" in args:
            item.category = _clean(args.get("category")) or item.category
        if "order_idx" in args:
            idx = _as_int(args.get("order_idx"))
            if idx is not None:
                item.order_idx = idx
        if "done" in args:
            done = _as_bool(args.get("done"))
            if done is not None:
                item.done = done
        await db.flush()
        return {"status": "success", "message": f"Checklist item {item.id} updated."}
    if op == "delete":
        await db.delete(item)
        await db.flush()
        return {"status": "success", "message": f"Checklist item {item_id} deleted."}
    return {"status": "error", "message": "Invalid op for manage_checklist_item."}


async def _manage_expense(db: AsyncSession, user: User, args: dict):
    op = _op(args.get("op"))
    trip = await _owned_trip(db, user, args.get("trip_id"))
    if not trip:
        return {"status": "error", "message": "Trip not found or permission denied."}
    if op == "create":
        title = _clean(args.get("title"))
        amount = _as_float(args.get("amount"))
        if not title or amount is None or amount <= 0:
            return {"status": "error", "message": "title and positive amount are required."}
        expense = Expense(trip_id=trip.id, title=title, amount=amount, category=_expense_category(args.get("category")), notes=_clean(args.get("notes")))
        db.add(expense)
        trip.spent = (trip.spent or 0.0) + amount
        await db.flush()
        await db.refresh(expense)
        return {"status": "success", "expense_id": expense.id, "message": "Expense created."}
    expense_id = _as_int(args.get("expense_id"))
    if expense_id is None:
        return {"status": "error", "message": "expense_id is required for update/delete."}
    r = await db.execute(select(Expense).where(Expense.id == expense_id, Expense.trip_id == trip.id))
    expense = r.scalar_one_or_none()
    if not expense:
        return {"status": "error", "message": "Expense not found."}
    if op == "update":
        old_amount = expense.amount or 0.0
        if "title" in args:
            expense.title = _clean(args.get("title")) or expense.title
        if "amount" in args:
            new_amount = _as_float(args.get("amount"))
            if new_amount is not None and new_amount > 0:
                expense.amount = new_amount
                trip.spent = max(0.0, (trip.spent or 0.0) - old_amount + new_amount)
        if "category" in args:
            expense.category = _expense_category(args.get("category"))
        if "notes" in args:
            expense.notes = _clean(args.get("notes"))
        await db.flush()
        return {"status": "success", "message": f"Expense {expense.id} updated."}
    if op == "delete":
        trip.spent = max(0.0, (trip.spent or 0.0) - (expense.amount or 0.0))
        await db.delete(expense)
        await db.flush()
        return {"status": "success", "message": f"Expense {expense_id} deleted."}
    return {"status": "error", "message": "Invalid op for manage_expense."}


async def call_gemini_with_tools(prompt: str, db: AsyncSession, u: User) -> str:
    if not settings.gemini_api_key or settings.gemini_api_key == "your-gemini-api-key-here":
        raise HTTPException(503, "Gemini API key not configured.")

    messages = [{"role": "user", "parts": [{"text": prompt}]}]

    for _ in range(5):
        payload = {
            "contents": messages,
            "generationConfig": {"temperature": 0.5, "maxOutputTokens": 4096},
            "tools": [TOOLS],
        }

        async with httpx.AsyncClient(timeout=AI_HTTP_TIMEOUT) as client:
            res = await client.post(f"{GEMINI_URL}?key={settings.gemini_api_key}", json=payload)
            if res.status_code != 200:
                detail = res.json().get("error", {}).get("message", "Gemini API error")
                raise HTTPException(502, f"Gemini error: {detail}")
            data = res.json()

        candidates = data.get("candidates") or []
        if not candidates:
            return "I couldn't generate a response right now."

        parts = candidates[0].get("content", {}).get("parts", [])
        text_part = next((p for p in parts if "text" in p), None)
        function_part = next((p for p in parts if "functionCall" in p), None)

        if text_part:
            return text_part.get("text", "")
        if not function_part:
            return "I couldn't complete that request."

        function_call = function_part.get("functionCall", {})
        func_name = function_call.get("name")
        args = function_call.get("args", {}) or {}
        messages.append({"role": "model", "parts": [{"functionCall": function_call}]})

        try:
            if func_name == "manage_trip":
                result = await _manage_trip(db, u, args)
            elif func_name == "manage_place":
                result = await _manage_place(db, u, args)
            elif func_name == "manage_itinerary_day":
                result = await _manage_itinerary_day(db, u, args)
            elif func_name == "manage_note":
                result = await _manage_note(db, u, args)
            elif func_name == "manage_checklist_item":
                result = await _manage_checklist_item(db, u, args)
            elif func_name == "manage_expense":
                result = await _manage_expense(db, u, args)
            else:
                result = {"status": "error", "message": f"Unknown tool: {func_name}"}

            if result.get("status") == "success":
                await db.commit()
            else:
                await db.rollback()
        except Exception as exc:
            await db.rollback()
            result = {"status": "error", "message": str(exc)}

        messages.append({
            "role": "user",
            "parts": [{"functionResponse": {"name": func_name, "response": result}}],
        })

    return "I completed the requested actions but timed out while preparing the final response."


async def call_gemini(prompt: str) -> str:
    _log = logging.getLogger("travelmate.ai")
    if not settings.gemini_api_key or settings.gemini_api_key == "your-gemini-api-key-here":
        raise HTTPException(503, "Gemini API key not configured. Add GEMINI_API_KEY to your .env file.")
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.7, "maxOutputTokens": 16384}}
    async with httpx.AsyncClient(timeout=AI_HTTP_TIMEOUT) as client:
        res = await client.post(f"{GEMINI_URL}?key={settings.gemini_api_key}", json=payload)
        if res.status_code != 200:
            try:
                err_body = res.json()
                detail = err_body.get("error", {}).get("message", "Gemini API error")
            except Exception:
                detail = res.text[:300]
            _log.error("Gemini API error %s: %s", res.status_code, detail)
            raise HTTPException(502, f"Gemini error ({res.status_code}): {detail}")
        data = res.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            _log.error("Gemini unexpected response shape: %s | data: %s", exc, str(data)[:500])
            raise HTTPException(502, f"Gemini returned unexpected response: {str(data)[:200]}")


async def call_grok(prompt: str) -> str:
    _log = logging.getLogger("travelmate.ai")
    if not settings.grok_api_key or settings.grok_api_key == "your-grok-api-key-here":
        raise HTTPException(503, "Grok API key not configured. Add GROK_API_KEY to .env")
    payload = {
        "model": "grok-3-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 2048,
    }
    async with httpx.AsyncClient(timeout=AI_HTTP_TIMEOUT) as client:
        res = await client.post(
            GROK_URL,
            json=payload,
            headers={"Authorization": f"Bearer {settings.grok_api_key}", "Content-Type": "application/json"},
        )
        if res.status_code != 200:
            try:
                err_body = res.json()
                if isinstance(err_body, dict):
                    detail = err_body.get("error", {}).get("message") or str(err_body)[:300]
                else:
                    detail = str(err_body)[:300]
            except Exception:
                detail = res.text[:300]
            _log.error("Grok API error %s: %s", res.status_code, detail)
            raise HTTPException(502, f"Grok error ({res.status_code}): {detail}")
        try:
            data = res.json()
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, Exception) as exc:
            _log.error("Grok unexpected response: %s | data: %s", exc, res.text[:500])
            raise HTTPException(502, f"Grok returned unexpected response: {res.text[:200]}")


async def call_groq(prompt: str) -> str:
    _log = logging.getLogger("travelmate.ai")
    if not settings.groq_api_key or settings.groq_api_key == "your-groq-api-key-here":
        raise HTTPException(503, "Groq API key not configured. Add GROQ_API_KEY to .env")
    # Groq free tier: 6000 TPM total (input + output).
    # Estimate input tokens at ~4 chars/token, leave the rest for output, floor at 300.
    GROQ_TPM_LIMIT = 5800  # small buffer below the 6000 hard limit
    estimated_input = len(prompt) // 4
    max_tokens = max(300, GROQ_TPM_LIMIT - estimated_input)
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": max_tokens,
    }
    async with httpx.AsyncClient(timeout=AI_HTTP_TIMEOUT) as client:
        res = await client.post(
            GROQ_URL,
            json=payload,
            headers={"Authorization": f"Bearer {settings.groq_api_key}", "Content-Type": "application/json"},
        )
        if res.status_code != 200:
            try:
                err_body = res.json()
                if isinstance(err_body, dict):
                    detail = err_body.get("error", {}).get("message") or str(err_body)[:300]
                else:
                    detail = str(err_body)[:300]
            except Exception:
                detail = res.text[:300]
            _log.error("Groq API error %s: %s", res.status_code, detail)
            raise HTTPException(502, f"Groq error ({res.status_code}): {detail}")
        try:
            data = res.json()
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, Exception) as exc:
            _log.error("Groq unexpected response: %s | data: %s", exc, res.text[:500])
            raise HTTPException(502, f"Groq returned unexpected response: {res.text[:200]}")


async def call_ai(prompt: str) -> str:
    _log = logging.getLogger("travelmate.ai")
    gemini_ok = settings.gemini_api_key and settings.gemini_api_key != "your-gemini-api-key-here"
    grok_ok = settings.grok_api_key and settings.grok_api_key != "your-grok-api-key-here"
    groq_ok = settings.groq_api_key and settings.groq_api_key != "your-groq-api-key-here"
    if gemini_ok:
        try:
            return await call_gemini(prompt)
        except HTTPException as exc:
            if exc.status_code in (429, 500, 502, 503, 504):
                # Trim prompt for fallback providers with tighter token limits
                fallback_prompt = prompt[:4000]
                if groq_ok:
                    _log.warning("Gemini failed (%s), falling back to Groq", exc.status_code)
                    return await call_groq(fallback_prompt)
                if grok_ok:
                    _log.warning("Gemini failed (%s), falling back to Grok", exc.status_code)
                    return await call_grok(fallback_prompt)
            raise
        except Exception:
            fallback_prompt = prompt[:4000]
            if groq_ok:
                return await call_groq(fallback_prompt)
            if grok_ok:
                return await call_grok(fallback_prompt)
            raise
    if groq_ok:
        return await call_groq(prompt)
    if grok_ok:
        return await call_grok(prompt)
    raise HTTPException(503, "No AI API key configured. Add GEMINI_API_KEY, GROQ_API_KEY, or GROK_API_KEY to .env")


async def call_ai_with_meta(prompt: str) -> tuple[str, str]:
    _log = logging.getLogger("travelmate.ai")
    gemini_ok = settings.gemini_api_key and settings.gemini_api_key != "your-gemini-api-key-here"
    grok_ok = settings.grok_api_key and settings.grok_api_key != "your-grok-api-key-here"
    groq_ok = settings.groq_api_key and settings.groq_api_key != "your-groq-api-key-here"
    if gemini_ok:
        try:
            return await call_gemini(prompt), "gemini"
        except HTTPException as exc:
            if exc.status_code in (429, 500, 502, 503, 504):
                fallback_prompt = prompt[:4000]
                if groq_ok:
                    _log.warning("Gemini failed (%s), falling back to Groq", exc.status_code)
                    return await call_groq(fallback_prompt), "groq"
                if grok_ok:
                    _log.warning("Gemini failed (%s), falling back to Grok", exc.status_code)
                    return await call_grok(fallback_prompt), "grok"
            raise
        except Exception:
            fallback_prompt = prompt[:4000]
            if groq_ok:
                return await call_groq(fallback_prompt), "groq"
            if grok_ok:
                return await call_grok(fallback_prompt), "grok"
            raise
    if groq_ok:
        return await call_groq(prompt), "groq"
    if grok_ok:
        return await call_grok(prompt), "grok"
    raise HTTPException(503, "No AI API key configured. Add GEMINI_API_KEY or GROK_API_KEY to .env")


def ai_available() -> bool:
    return bool(
        (settings.gemini_api_key and settings.gemini_api_key != "your-gemini-api-key-here")
        or (settings.grok_api_key and settings.grok_api_key != "your-grok-api-key-here")
        or (settings.groq_api_key and settings.groq_api_key != "your-groq-api-key-here")
    )


def build_prompt(user, trip, places, expenses, days, all_trips, query):
    # Hard limits — prevent 413s regardless of how much data a trip accumulates
    MAX_TRIPS = 10
    MAX_PLACES = 10
    MAX_DAYS = 7
    MAX_QUERY_CHARS = 800

    trips_block = "\n".join(
        f"- ID:{t.id} {t.name} → {t.destination} ({t.status})"
        for t in all_trips[:MAX_TRIPS]
    ) if all_trips else "- None"
    if len(all_trips) > MAX_TRIPS:
        trips_block += f"\n- … +{len(all_trips) - MAX_TRIPS} more"

    if trip:
        visited = [p.name for p in places if p.status == "visited"][:MAX_PLACES]
        upcoming = [p.name for p in places if p.status in ("planned", "upcoming")][:MAX_PLACES]
        total_exp = sum(e.amount for e in expenses)
        remaining = trip.budget - total_exp

        places_text = ""
        if visited:
            places_text += f"Visited: {', '.join(visited)}. "
        if upcoming:
            places_text += f"Planned: {', '.join(upcoming)}. "

        by_cat: dict = {}
        for e in expenses:
            by_cat[e.category] = by_cat.get(e.category, 0) + e.amount
        expenses_text = ("Expenses: " + ", ".join(f"{c}:{a:,.0f}" for c, a in by_cat.items()) + ".") if by_cat else ""

        itinerary_text = ""
        if days:
            itinerary_text = "Days: " + "; ".join(
                f"D{d.day_number} {d.title[:40]}" for d in days[:MAX_DAYS]
            )

        active_trip_block = (
            f"TRIP ID:{trip.id} {trip.name}→{trip.destination} ({trip.status}) "
            f"{trip.start_date or '?'} to {trip.end_date or '?'} "
            f"Budget INR {trip.budget:,.0f} spent {total_exp:,.0f} left {remaining:,.0f}. "
            f"{places_text}{expenses_text} {itinerary_text}"
        ).strip()
    else:
        active_trip_block = "No trip selected (global mode)."

    # Truncate query to prevent oversized user messages
    clean_query = query[:MAX_QUERY_CHARS]
    if len(query) > MAX_QUERY_CHARS:
        clean_query += "…"

    return f"""You are TravelMate AI. Be concise (3-5 points max unless asked for more).

User: {user.full_name}
Trips: {trips_block}
Active: {active_trip_block}

Q: {clean_query}

Tools: manage_trip, manage_place, manage_itinerary_day, manage_note, manage_checklist_item, manage_expense."""


@router.post("/travel-insights")
async def travel_insights(body: dict):
    _log = logging.getLogger("travelmate.ai")
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(400, "prompt is required")
    if not ai_available():
        raise HTTPException(503, "AI not configured. Add GEMINI_API_KEY or GROK_API_KEY.")
    try:
        text, provider = await call_ai_with_meta(prompt)
        return {"text": text, "ai_used": True, "ai_provider": provider}
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("travel-insights failed: %s: %s", type(exc).__name__, exc, exc_info=True)
        raise HTTPException(502, f"AI request failed: {exc}")


@router.post("/overpass")
async def overpass_proxy(body: dict):
    query = body.get("query", "")
    if not query:
        raise HTTPException(400, "query required")
    mirrors = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.private.coffee/api/interpreter",
    ]
    last_err = None
    async with httpx.AsyncClient(timeout=60) as client:
        for mirror in mirrors:
            try:
                res = await client.post(
                    mirror,
                    data={"data": query},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if res.status_code == 200:
                    return res.json()
                last_err = f"{mirror} returned {res.status_code}"
            except Exception as exc:
                last_err = str(exc)
    raise HTTPException(502, f"All Overpass mirrors failed: {last_err}")


@router.post("/recommend", response_model=AIResponse)
async def recommend(body: AIRequest, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    all_trips = list((await db.execute(select(Trip).where(Trip.owner_id == u.id))).scalars().all())

    trip = None
    places = []
    expenses = []
    days = []

    if body.trip_id is not None and body.trip_id != 0:
        r = await db.execute(select(Trip).where(Trip.id == body.trip_id, Trip.owner_id == u.id))
        trip = r.scalar_one_or_none()
        if not trip:
            raise HTTPException(404, "Trip not found")
        places = list((await db.execute(select(Place).where(Place.trip_id == trip.id))).scalars().all())
        expenses = list((await db.execute(select(Expense).where(Expense.trip_id == trip.id))).scalars().all())
        days = list((await db.execute(select(ItineraryDay).where(ItineraryDay.trip_id == trip.id))).scalars().all())

    prompt = build_prompt(u, trip, places, expenses, days, all_trips, body.query)
    if settings.gemini_api_key and settings.gemini_api_key != "your-gemini-api-key-here":
        response = await call_gemini_with_tools(prompt, db, u)
    else:
        response = await call_ai(prompt)

    return AIResponse(response=response, trip_name=trip.name if trip else "Global Data")


@router.get("/chat/{trip_id}", response_model=AIChatOut)
async def get_chat(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if trip_id != 0:
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
    if trip_id != 0:
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
    await db.flush()
    await db.commit()
    return AIChatOut(trip_id=trip_id, messages=messages, updated_at=chat.updated_at)


async def _throttled_warmup(trip_ids: list[int], force: bool = False):
    for i, trip_id in enumerate(trip_ids):
        try:
            if i > 0:
                await asyncio.sleep(2)
            await warmup_trip_all_data(trip_id, force)
        except Exception as exc:
            logging.getLogger("travelmate.warmup").warning("Warmup failed for trip %s: %s", trip_id, exc)


@router.post("/roadmaps/precompute", response_model=RoadmapPrecomputeOut)
async def precompute_roadmaps(background_tasks: BackgroundTasks, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
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

    if stale_ids:
        background_tasks.add_task(_throttled_warmup, stale_ids, False)

    return RoadmapPrecomputeOut(total=total, queued=queued, skipped_fresh=skipped_fresh, skipped_invalid=skipped_invalid)


@router.get("/roadmaps/status", response_model=RoadmapStatusOut)
async def roadmap_status(u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Trip).where(Trip.owner_id == u.id))
    trips = list(r.scalars().all())
    items = [RoadmapTripStatusOut(**trip_warmup_status(tr)) for tr in trips]
    fresh = sum(1 for i in items if i.state == "fresh")
    invalid = sum(1 for i in items if i.state == "invalid")
    stale = len(items) - fresh - invalid
    return RoadmapStatusOut(total=len(items), fresh=fresh, stale=stale, invalid=invalid, items=items, ai_available=ai_available())