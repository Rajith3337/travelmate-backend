from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.session import get_db
from models.models import Note, Trip, User
from schemas.schemas import NoteIn, NoteOut, NoteUpdate
from services.deps import current_user

router = APIRouter(prefix="/{trip_id}/notes")

async def _trip(trip_id: int, u: User, db: AsyncSession) -> Trip:
    r = await db.execute(select(Trip).where(Trip.id == trip_id, Trip.owner_id == u.id))
    t = r.scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Trip not found")
    return t

@router.get("/", response_model=list[NoteOut])
async def list_notes(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r = await db.execute(
        select(Note).where(Note.trip_id == trip_id)
        .order_by(Note.pinned.desc(), Note.updated_at.desc())
    )
    return r.scalars().all()

@router.post("/", response_model=NoteOut, status_code=201)
async def create_note(trip_id: int, body: NoteIn, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    note = Note(**body.model_dump(), trip_id=trip_id)
    db.add(note)
    await db.flush()
    await db.refresh(note)
    return note

@router.patch("/{note_id}", response_model=NoteOut)
async def update_note(trip_id: int, note_id: int, body: NoteUpdate, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r = await db.execute(select(Note).where(Note.id == note_id, Note.trip_id == trip_id))
    note = r.scalar_one_or_none()
    if not note:
        raise HTTPException(404, "Note not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(note, k, v)
    await db.flush()
    await db.refresh(note)
    return note

@router.delete("/{note_id}", status_code=204)
async def delete_note(trip_id: int, note_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r = await db.execute(select(Note).where(Note.id == note_id, Note.trip_id == trip_id))
    note = r.scalar_one_or_none()
    if not note:
        raise HTTPException(404, "Note not found")
    await db.delete(note)
    await db.flush()
