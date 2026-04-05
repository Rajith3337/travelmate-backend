import uuid
import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.session import get_db
from models.models import Photo, Trip, User
from schemas.schemas import PhotoOut, PhotoUpdate
from services.deps import current_user
from core.config import get_settings

router   = APIRouter(prefix="/{trip_id}/photos")
settings = get_settings()
ALLOWED  = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_SIZE = 10 * 1024 * 1024  # 10 MB

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

@router.get("/", response_model=list[PhotoOut])
async def list_photos(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r = await db.execute(select(Photo).where(Photo.trip_id == trip_id).order_by(Photo.uploaded_at.desc()))
    return r.scalars().all()

@router.post("/", response_model=PhotoOut, status_code=201)
async def upload_photo(trip_id: int, file: UploadFile = File(...), u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    if file.content_type not in ALLOWED:
        raise HTTPException(400, "Only JPEG, PNG, WebP, GIF allowed")
    data = await file.read()
    if len(data) > MAX_SIZE:
        raise HTTPException(400, "File too large (max 10 MB)")

    ext   = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "jpg"
    fname = f"{uuid.uuid4().hex}.{ext}"
    path  = f"{trip_id}/{fname}"

    # Upload to Supabase Storage
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _storage_url(path),
            content=data,
            headers={**_supabase_headers(), "Content-Type": file.content_type},
        )
    if resp.status_code not in (200, 201):
        raise HTTPException(500, f"Storage upload failed: {resp.text}")

    public_url = _public_url(path)
    photo = Photo(trip_id=trip_id, filename=fname, url=public_url, caption=file.filename)
    db.add(photo)
    await db.flush()
    await db.refresh(photo)
    return photo

@router.patch("/{photo_id}", response_model=PhotoOut)
async def update_photo(trip_id: int, photo_id: int, body: PhotoUpdate, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r     = await db.execute(select(Photo).where(Photo.id == photo_id, Photo.trip_id == trip_id))
    photo = r.scalar_one_or_none()
    if not photo:
        raise HTTPException(404, "Photo not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(photo, k, v)
    await db.flush()
    await db.refresh(photo)
    return photo

@router.delete("/{photo_id}", status_code=204)
async def delete_photo(trip_id: int, photo_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r     = await db.execute(select(Photo).where(Photo.id == photo_id, Photo.trip_id == trip_id))
    photo = r.scalar_one_or_none()
    if not photo:
        raise HTTPException(404, "Photo not found")

    # Delete from Supabase Storage
    path = f"{trip_id}/{photo.filename}"
    async with httpx.AsyncClient() as client:
        await client.delete(_storage_url(path), headers=_supabase_headers())

    await db.delete(photo)
    await db.flush()
