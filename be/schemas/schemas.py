from datetime import datetime
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional

class RegisterIn(BaseModel):
    email: EmailStr
    username: str
    full_name: Optional[str] = None
    password: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters")
        return v

    @field_validator("username")
    @classmethod
    def username_no_spaces(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Username is required")
        return v

class LoginIn(BaseModel):
    email: str   # accepts email OR username
    password: str

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"

class UserOut(BaseModel):
    id: int
    email: str
    username: str
    full_name: str
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    created_at: datetime
    model_config = {"from_attributes": True}

class UserUpdate(BaseModel):
    full_name:  Optional[str] = None
    username:   Optional[str] = None
    email:      Optional[str] = None
    bio:        Optional[str] = None
    avatar_url: Optional[str] = None
    password:   Optional[str] = None   # handled in auth router

class TripIn(BaseModel):
    name: str
    destination: str
    start_location: Optional[str] = None
    description: Optional[str] = None
    cover_emoji: str = "✈️"
    cover_color: str = "#00D4FF"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: str = "planning"
    budget: float = 0.0

class TripOut(BaseModel):
    id: int
    name: str
    destination: str
    start_location: Optional[str] = None
    description: Optional[str] = None
    cover_emoji: str
    cover_color: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: str
    budget: float
    spent: float
    progress: float
    map_bbox: Optional[str] = None
    preloaded_facilities: Optional[str] = None
    ai_roadmap: Optional[str] = None
    active_route: Optional[str] = None
    created_at: datetime
    model_config = {"from_attributes": True}

class TripUpdate(BaseModel):
    name: Optional[str] = None
    destination: Optional[str] = None
    start_location: Optional[str] = None
    description: Optional[str] = None
    cover_emoji: Optional[str] = None
    cover_color: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: Optional[str] = None
    budget: Optional[float] = None
    ai_roadmap: Optional[str] = None
    active_route: Optional[str] = None

class PlaceIn(BaseModel):
    name: str
    place_type: str = "Attraction"
    address: Optional[str] = None
    notes: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    rating: Optional[float] = None
    visit_time: Optional[str] = None
    status: str = "planned"
    order_idx: int = 0

class PlaceOut(BaseModel):
    id: int
    trip_id: int
    name: str
    place_type: str
    address: Optional[str] = None
    notes: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    rating: Optional[float] = None
    visit_time: Optional[str] = None
    status: str
    order_idx: int = 0
    created_at: datetime
    model_config = {"from_attributes": True}

class PlaceUpdate(BaseModel):
    name: Optional[str] = None
    place_type: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    rating: Optional[float] = None
    visit_time: Optional[str] = None
    status: Optional[str] = None
    order_idx: Optional[int] = None

class ExpenseIn(BaseModel):
    title: str
    amount: float
    category: str = "other"
    notes: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Amount must be positive")
        return v

class ExpenseOut(BaseModel):
    id: int
    trip_id: int
    title: str
    amount: float
    category: str
    notes: Optional[str] = None
    spent_at: datetime
    created_at: datetime
    model_config = {"from_attributes": True}

class ExpenseUpdate(BaseModel):
    title: Optional[str] = None
    amount: Optional[float] = None
    category: Optional[str] = None
    notes: Optional[str] = None

class PhotoOut(BaseModel):
    id: int
    trip_id: int
    filename: str
    url: str
    caption: Optional[str] = None
    is_cover: bool
    uploaded_at: datetime
    model_config = {"from_attributes": True}

class PhotoUpdate(BaseModel):
    caption: Optional[str] = None
    is_cover: Optional[bool] = None

class ItineraryDayIn(BaseModel):
    day_number: int
    date_label: Optional[str] = None
    title: str
    notes: Optional[str] = None
    place_names: Optional[str] = None

class ItineraryDayOut(BaseModel):
    id: int
    trip_id: int
    day_number: int
    date_label: Optional[str] = None
    title: str
    notes: Optional[str] = None
    place_names: Optional[str] = None
    places_list: list[str] = []
    created_at: datetime
    model_config = {"from_attributes": True}

class ItineraryDayUpdate(BaseModel):
    day_number: Optional[int] = None
    date_label: Optional[str] = None
    title: Optional[str] = None
    notes: Optional[str] = None
    place_names: Optional[str] = None

class AIRequest(BaseModel):
    trip_id: int
    query: str

class AIResponse(BaseModel):
    response: str
    trip_name: str

class ShareOut(BaseModel):
    token: str
    share_url: str
    trip_name: str

class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str
    confirm_password: Optional[str] = None

    @field_validator("new_password")
    @classmethod
    def new_password_min(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("New password must be at least 6 characters")
        return v

# ── Notes ────────────────────────────────────────────────────────────────────
class NoteIn(BaseModel):
    title: str
    content: str
    color: str = "#00D4FF"
    pinned: bool = False

class NoteOut(BaseModel):
    id: int
    trip_id: int
    title: str
    content: str
    color: str
    pinned: bool
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}

class NoteUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    color: Optional[str] = None
    pinned: Optional[bool] = None

# ── Checklist ────────────────────────────────────────────────────────────────
class ChecklistItemIn(BaseModel):
    text: str
    category: str = "General"
    order_idx: int = 0

class ChecklistItemOut(BaseModel):
    id: int
    trip_id: int
    text: str
    done: bool
    category: str
    order_idx: int
    created_at: datetime
    model_config = {"from_attributes": True}

class ChecklistItemUpdate(BaseModel):
    text: Optional[str] = None
    done: Optional[bool] = None
    category: Optional[str] = None
    order_idx: Optional[int] = None

# ── Stats ─────────────────────────────────────────────────────────────────────
class TripStatsOut(BaseModel):
    total_trips: int
    completed_trips: int
    active_trips: int
    total_budget: float
    total_spent: float
    total_places: int
    visited_places: int
    total_photos: int
    countries_visited: list[str]


class TrackerSessionIn(BaseModel):
    name: str
    start_time: Optional[datetime] = None


class TrackerSessionUpdate(BaseModel):
    end_time: Optional[datetime] = None
    total_distance: Optional[float] = None
    duration: Optional[int] = None
    coord_count: Optional[int] = None
    path_json: Optional[str] = None


class TrackerSessionOut(BaseModel):
    id: int
    trip_id: int
    name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    total_distance: float
    duration: int
    coord_count: int
    path_json: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class TrackerPhotoOut(BaseModel):
    id: int
    trip_id: int
    session_id: Optional[int] = None
    filename: str
    url: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    size_bytes: Optional[int] = None
    captured_at: datetime
    created_at: datetime
    model_config = {"from_attributes": True}


class UserSettingOut(BaseModel):
    key: str
    value_text: str
    updated_at: datetime
    model_config = {"from_attributes": True}


class UserSettingIn(BaseModel):
    value_text: str = ""
