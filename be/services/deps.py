from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.security import decode_token
from db.session import get_db
from models.models import User

bearer = HTTPBearer()

async def current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db:    AsyncSession                 = Depends(get_db),
) -> User:
    user_id = decode_token(creds.credentials)
    result  = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    user    = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user
