from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from db.session import get_db
from models.models import User
from schemas.schemas import RegisterIn, LoginIn, TokenOut, UserOut, UserUpdate, PasswordChangeIn
from core.security import hash_password, verify_password, create_access_token
from services.deps import current_user

router = APIRouter()

@router.post("/register", response_model=TokenOut, status_code=201)
async def register(body: RegisterIn, db: AsyncSession = Depends(get_db)):
    ex = await db.execute(select(User).where(or_(User.email == body.email, User.username == body.username)))
    if ex.scalar_one_or_none():
        raise HTTPException(400, "Email or username already taken")
    user = User(
        email=body.email, username=body.username,
        full_name=body.full_name or body.username,
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return TokenOut(access_token=create_access_token(user.id))

@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(User).where(or_(User.email == body.email, User.username == body.email)))
    user = r.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(401, "Invalid credentials")
    return TokenOut(access_token=create_access_token(user.id))

@router.get("/me", response_model=UserOut)
async def me(u: User = Depends(current_user)):
    return u

@router.patch("/me", response_model=UserOut)
async def update_me(body: UserUpdate, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    data = body.model_dump(exclude_none=True)
    
    # Handle avatar_url size limit (base64 images can be very large)
    if "avatar_url" in data and data["avatar_url"]:
        # Check if it's a base64 data URL
        if data["avatar_url"].startswith("data:"):
            # Limit base64 images to ~1MB
            if len(data["avatar_url"]) > 1500000:  # ~1.5MB limit
                raise HTTPException(400, "Avatar image too large (max 1.5MB)")
            # You could add image format validation here if needed
    
    # Validation against blanks and conflicts
    if "username" in data:
        if not data["username"].strip():
            raise HTTPException(400, "Username cannot be empty")
        if data["username"] != u.username:
            ex = await db.execute(select(User).where(User.username == data["username"]))
            if ex.scalar_one_or_none():
                raise HTTPException(400, "Username already taken")
                
    if "email" in data:
        if not data["email"].strip():
            raise HTTPException(400, "Email cannot be empty")
        if data["email"] != u.email:
            ex = await db.execute(select(User).where(User.email == data["email"]))
            if ex.scalar_one_or_none():
                raise HTTPException(400, "Email already taken")
                
    new_password = data.pop("password", None)
    for k, v in data.items():
        setattr(u, k, v)
    if new_password:
        u.hashed_password = hash_password(new_password)
    await db.flush()
    await db.refresh(u)
    return u

@router.post("/me/password")
async def change_password(body: PasswordChangeIn, u: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if not verify_password(body.current_password, u.hashed_password):
        raise HTTPException(400, "Current password is incorrect")
    if len(body.new_password) < 6:
        raise HTTPException(422, "New password must be at least 6 characters")
    u.hashed_password = hash_password(body.new_password)
    await db.flush()
    return {"message": "Password changed successfully"}
