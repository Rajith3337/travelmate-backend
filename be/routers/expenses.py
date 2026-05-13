from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.session import get_db
from models.models import Expense, Trip, User
from schemas.schemas import ExpenseIn, ExpenseOut, ExpenseUpdate
from services.deps import current_user

router = APIRouter(prefix="/{trip_id}/expenses")

async def _trip(trip_id: int, u: User, db: AsyncSession) -> Trip:
    r = await db.execute(select(Trip).where(Trip.id == trip_id, Trip.owner_id == u.id))
    t = r.scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Trip not found")
    return t

@router.get("/", response_model=list[ExpenseOut])
async def list_expenses(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _trip(trip_id, u, db)
    r = await db.execute(select(Expense).where(Expense.trip_id == trip_id).order_by(Expense.spent_at.desc()))
    return r.scalars().all()

@router.get("/summary")
async def expense_summary(trip_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    trip = await _trip(trip_id, u, db)
    r    = await db.execute(select(Expense).where(Expense.trip_id == trip_id))
    exps = r.scalars().all()
    by_cat: dict[str, float] = {}
    for e in exps:
        by_cat[e.category] = by_cat.get(e.category, 0) + e.amount
    total = sum(e.amount for e in exps)
    return {
        "total_spent":  total,
        "total_budget": trip.budget,
        "remaining":    trip.budget - total,
        "progress_pct": round(total / trip.budget * 100, 1) if trip.budget > 0 else 0,
        "by_category":  by_cat,
    }

@router.post("/", response_model=ExpenseOut, status_code=201)
async def create_expense(trip_id: int, body: ExpenseIn, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    trip    = await _trip(trip_id, u, db)
    expense = Expense(**body.model_dump(), trip_id=trip_id)
    db.add(expense)
    trip.spent = trip.spent + body.amount
    await db.flush()
    await db.refresh(expense)
    return expense

@router.patch("/{expense_id}", response_model=ExpenseOut)
async def update_expense(trip_id: int, expense_id: int, body: ExpenseUpdate, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    trip    = await _trip(trip_id, u, db)
    r       = await db.execute(select(Expense).where(Expense.id == expense_id, Expense.trip_id == trip_id))
    expense = r.scalar_one_or_none()
    if not expense:
        raise HTTPException(404, "Expense not found")
    old_amount = expense.amount
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(expense, k, v)
    if body.amount is not None:
        trip.spent = max(0.0, trip.spent - old_amount + body.amount)
    await db.flush()
    await db.refresh(expense)
    return expense

@router.delete("/{expense_id}", status_code=204)
async def delete_expense(trip_id: int, expense_id: int, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    trip    = await _trip(trip_id, u, db)
    r       = await db.execute(select(Expense).where(Expense.id == expense_id, Expense.trip_id == trip_id))
    expense = r.scalar_one_or_none()
    if not expense:
        raise HTTPException(404, "Expense not found")
    trip.spent = max(0.0, trip.spent - expense.amount)
    await db.delete(expense)
    await db.flush()
