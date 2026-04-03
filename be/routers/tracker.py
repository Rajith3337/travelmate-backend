import uuid
from datetime import datetime, timezone
import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from core.config import get_settings
from db.session import get_db
from models.models import TrackerPhoto, TrackerSession, Trip, User
from schemas.schemas import TrackerSessionIn, TrackerSessionOut, TrackerSessionUpdate, TrackerPhotoOut
from services.deps import current_user

router = APIRouter(prefix="/{trip_id}/tracker")
settings = get_settings()
ALLOWED = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_SIZE = 10 * 1024 * 1024


def _supabase_headers():
    return {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
    }


def _storage_url(path: str) -> str:
    return f"{settings.supabase_url}/storage/v1/object/{settings.supabase_bucket}/{path}"


def _public_url(path: str) -> str:
    return f"{settings.supabase_url}/storage/v1/object/public/{settings.supabase_bucket}/{path}"


async def _trip(trip_id: int, u: User, db: AsyncSession) -> Trip:
    r = await db.execute(select(Trip).where(Trip.id == trip_id, Trip.owner_id == u.id))
    t = r.scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Trip not found")
    return t


@router.get("/sessions/", response_model=list[TrackerSessionOut])
async def list_sessions(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r = await db.execute(
        select(TrackerSession).where(TrackerSession.trip_id == trip_id).order_by(TrackerSession.start_time.desc())
    )
    return r.scalars().all()


@router.post("/sessions/", response_model=TrackerSessionOut, status_code=201)
async def create_session(
    trip_id: int,
    body: TrackerSessionIn,
    u: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    await _trip(trip_id, u, db)
    session = TrackerSession(
        trip_id=trip_id,
        name=body.name,
        start_time=body.start_time or datetime.now(timezone.utc),
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return session


@router.patch("/sessions/{session_id}", response_model=TrackerSessionOut)
async def update_session(
    trip_id: int,
    session_id: int,
    body: TrackerSessionUpdate,
    u: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    await _trip(trip_id, u, db)
    r = await db.execute(
        select(TrackerSession).where(TrackerSession.id == session_id, TrackerSession.trip_id == trip_id)
    )
    s = r.scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Session not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(s, k, v)
    await db.flush()
    await db.refresh(s)
    return s


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    trip_id: int,
    session_id: int,
    u: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    await _trip(trip_id, u, db)
    r = await db.execute(
        select(TrackerSession).where(TrackerSession.id == session_id, TrackerSession.trip_id == trip_id)
    )
    s = r.scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Session not found")

    rp = await db.execute(
        select(TrackerPhoto).where(TrackerPhoto.trip_id == trip_id, TrackerPhoto.session_id == session_id)
    )
    photos = rp.scalars().all()
    if photos:
        async with httpx.AsyncClient() as client:
            for photo in photos:
                path = f"tracker/{trip_id}/{photo.filename}"
                await client.delete(_storage_url(path), headers=_supabase_headers())

    await db.delete(s)


@router.get("/sessions/{session_id}/photos/", response_model=list[TrackerPhotoOut])
async def list_session_photos(
    trip_id: int,
    session_id: int,
    u: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    await _trip(trip_id, u, db)
    r = await db.execute(
        select(TrackerPhoto)
        .where(TrackerPhoto.trip_id == trip_id, TrackerPhoto.session_id == session_id)
        .order_by(TrackerPhoto.captured_at.desc())
    )
    return r.scalars().all()


@router.post("/photos/", response_model=TrackerPhotoOut, status_code=201)
async def upload_tracker_photo(
    trip_id: int,
    file: UploadFile = File(...),
    session_id: int | None = Form(default=None),
    latitude: float | None = Form(default=None),
    longitude: float | None = Form(default=None),
    size_bytes: int | None = Form(default=None),
    captured_at: datetime | None = Form(default=None),
    u: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    await _trip(trip_id, u, db)
    if session_id is not None:
        rs = await db.execute(
            select(TrackerSession).where(TrackerSession.id == session_id, TrackerSession.trip_id == trip_id)
        )
        if not rs.scalar_one_or_none():
            raise HTTPException(404, "Session not found")

    if file.content_type not in ALLOWED:
        raise HTTPException(400, "Only JPEG, PNG, WebP, GIF allowed")
    data = await file.read()
    if len(data) > MAX_SIZE:
        raise HTTPException(400, "File too large (max 10 MB)")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "jpg"
    fname = f"{uuid.uuid4().hex}.{ext}"
    path = f"tracker/{trip_id}/{fname}"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _storage_url(path),
            content=data,
            headers={**_supabase_headers(), "Content-Type": file.content_type},
        )
    if resp.status_code not in (200, 201):
        raise HTTPException(500, f"Storage upload failed: {resp.text}")

    photo = TrackerPhoto(
        trip_id=trip_id,
        session_id=session_id,
        filename=fname,
        url=_public_url(path),
        latitude=latitude,
        longitude=longitude,
        size_bytes=size_bytes or len(data),
        captured_at=captured_at or datetime.now(timezone.utc),
    )
    db.add(photo)
    await db.flush()
    await db.refresh(photo)
    return photo


@router.delete("/photos/{photo_id}", status_code=204)
async def delete_tracker_photo(
    trip_id: int,
    photo_id: int,
    u: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    await _trip(trip_id, u, db)
    r = await db.execute(select(TrackerPhoto).where(TrackerPhoto.id == photo_id, TrackerPhoto.trip_id == trip_id))
    photo = r.scalar_one_or_none()
    if not photo:
        raise HTTPException(404, "Photo not found")

    path = f"tracker/{trip_id}/{photo.filename}"
    async with httpx.AsyncClient() as client:
        await client.delete(_storage_url(path), headers=_supabase_headers())

    await db.delete(photo)
