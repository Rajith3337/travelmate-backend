from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.session import get_db
from models.models import User, UserSetting
from schemas.schemas import UserSettingIn, UserSettingOut
from services.deps import current_user

router = APIRouter()


@router.get("/{key}", response_model=UserSettingOut)
async def get_setting(key: str, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(UserSetting).where(UserSetting.user_id == u.id, UserSetting.key == key))
    row = r.scalar_one_or_none()
    if row:
        return row
    row = UserSetting(user_id=u.id, key=key, value_text="")
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


@router.put("/{key}", response_model=UserSettingOut)
async def put_setting(
    key: str,
    body: UserSettingIn,
    u: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(select(UserSetting).where(UserSetting.user_id == u.id, UserSetting.key == key))
    row = r.scalar_one_or_none()
    if not row:
        row = UserSetting(user_id=u.id, key=key, value_text=body.value_text or "")
        db.add(row)
    else:
        row.value_text = body.value_text or ""
    await db.flush()
    await db.refresh(row)
    return row
