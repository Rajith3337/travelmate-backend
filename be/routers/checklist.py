from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.session import get_db
from models.models import ChecklistItem, Trip, User
from schemas.schemas import ChecklistItemIn, ChecklistItemOut, ChecklistItemUpdate
from services.deps import current_user

router = APIRouter(prefix="/{trip_id}/checklist")

async def _trip(trip_id: int, u: User, db: AsyncSession) -> Trip:
    r = await db.execute(select(Trip).where(Trip.id == trip_id, Trip.owner_id == u.id))
    t = r.scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Trip not found")
    return t

@router.get("/", response_model=list[ChecklistItemOut])
async def list_items(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r = await db.execute(
        select(ChecklistItem).where(ChecklistItem.trip_id == trip_id)
        .order_by(ChecklistItem.category, ChecklistItem.order_idx)
    )
    return r.scalars().all()

@router.post("/", response_model=ChecklistItemOut, status_code=201)
async def create_item(trip_id: int, body: ChecklistItemIn, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    item = ChecklistItem(**body.model_dump(), trip_id=trip_id)
    db.add(item)
    await db.flush()
    await db.refresh(item)
    return item

@router.patch("/{item_id}", response_model=ChecklistItemOut)
async def update_item(trip_id: int, item_id: int, body: ChecklistItemUpdate, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r = await db.execute(select(ChecklistItem).where(ChecklistItem.id == item_id, ChecklistItem.trip_id == trip_id))
    item = r.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Item not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(item, k, v)
    await db.flush()
    await db.refresh(item)
    return item

@router.delete("/{item_id}", status_code=204)
async def delete_item(trip_id: int, item_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r = await db.execute(select(ChecklistItem).where(ChecklistItem.id == item_id, ChecklistItem.trip_id == trip_id))
    item = r.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Item not found")
    await db.delete(item)

@router.delete("/", status_code=204)
async def clear_done(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r = await db.execute(select(ChecklistItem).where(ChecklistItem.trip_id == trip_id, ChecklistItem.done == True))
    for item in r.scalars().all():
        await db.delete(item)
