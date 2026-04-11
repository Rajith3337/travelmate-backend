import enum, secrets
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean, DateTime, Enum, Float, ForeignKey, JSON,
    Integer, String, Text, UniqueConstraint, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from db.session import Base

# ── Enums ──────────────────────────────────────────────────────────────────
class TripStatus(str, enum.Enum):
    planning  = "planning"
    upcoming  = "upcoming"
    active    = "active"
    completed = "completed"

class PlaceStatus(str, enum.Enum):
    planned  = "planned"
    upcoming = "upcoming"
    visited  = "visited"
    skipped  = "skipped"

class ExpenseCategory(str, enum.Enum):
    accommodation = "accommodation"
    transport     = "transport"
    food          = "food"
    tickets       = "tickets"
    shopping      = "shopping"
    activities    = "activities"
    entertainment = "entertainment"
    other         = "other"

now = lambda: datetime.now(timezone.utc)

# ── Models ─────────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"
    id:              Mapped[int]      = mapped_column(primary_key=True, index=True)
    email:           Mapped[str]      = mapped_column(String(255), unique=True, index=True)
    username:        Mapped[str]      = mapped_column(String(100), unique=True, index=True)
    full_name:       Mapped[str]      = mapped_column(String(200))
    hashed_password: Mapped[str]      = mapped_column(String(255))
    avatar_url:      Mapped[str|None] = mapped_column(Text, nullable=True)
    bio:             Mapped[str|None] = mapped_column(Text, nullable=True)
    is_active:       Mapped[bool]     = mapped_column(Boolean, default=True)
    created_at:      Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    trips: Mapped[list["Trip"]] = relationship("Trip", back_populates="owner", cascade="all, delete-orphan")
    settings: Mapped[list["UserSetting"]] = relationship("UserSetting", back_populates="user", cascade="all, delete-orphan")


class Trip(Base):
    __tablename__ = "trips"
    id:             Mapped[int]        = mapped_column(primary_key=True, index=True)
    owner_id:       Mapped[int]        = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name:           Mapped[str]        = mapped_column(String(200))
    destination:    Mapped[str]        = mapped_column(String(300))
    start_location: Mapped[str|None]   = mapped_column(String(300), nullable=True)
    description:    Mapped[str|None]   = mapped_column(Text, nullable=True)
    cover_emoji:    Mapped[str]        = mapped_column(String(20), default="\u2708\uFE0F")   # widened: emoji+variation selector can be >10 bytes
    cover_color:    Mapped[str]        = mapped_column(String(20), default="#00D4FF")
    start_date:     Mapped[str|None]   = mapped_column(String(20), nullable=True)
    end_date:       Mapped[str|None]   = mapped_column(String(20), nullable=True)
    status:         Mapped[TripStatus] = mapped_column(Enum(TripStatus), default=TripStatus.planning)
    budget:         Mapped[float]      = mapped_column(Float, default=0.0)
    spent:          Mapped[float]      = mapped_column(Float, default=0.0)
    
    # Preloaded Caching for Backend Anticipation
    map_bbox:             Mapped[str|None] = mapped_column(Text, nullable=True) # JSON serialized
    preloaded_facilities: Mapped[str|None] = mapped_column(Text, nullable=True) # JSON serialized
    ai_roadmap:           Mapped[str|None] = mapped_column(Text, nullable=True) # JSON serialized plan & insights
    active_route:         Mapped[str|None] = mapped_column(Text, nullable=True) # JSON serialized active drawn LiveMap route
    places_route:         Mapped[dict|None] = mapped_column(JSON, nullable=True) # JSON serialized places visit route
    
    created_at:     Mapped[datetime]   = mapped_column(DateTime(timezone=True), default=now)
    updated_at:     Mapped[datetime]   = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    owner:     Mapped["User"]               = relationship("User", back_populates="trips")
    places:    Mapped[list["Place"]]        = relationship("Place",        back_populates="trip", cascade="all, delete-orphan")
    expenses:  Mapped[list["Expense"]]      = relationship("Expense",      back_populates="trip", cascade="all, delete-orphan")
    photos:    Mapped[list["Photo"]]        = relationship("Photo",        back_populates="trip", cascade="all, delete-orphan")
    itinerary: Mapped[list["ItineraryDay"]] = relationship("ItineraryDay", back_populates="trip", cascade="all, delete-orphan", order_by="ItineraryDay.day_number")
    share_tokens:    Mapped[list["ShareToken"]]    = relationship("ShareToken",    back_populates="trip", cascade="all, delete-orphan")
    notes:           Mapped[list["Note"]]           = relationship("Note",           back_populates="trip", cascade="all, delete-orphan")
    checklist_items: Mapped[list["ChecklistItem"]]  = relationship("ChecklistItem",  back_populates="trip", cascade="all, delete-orphan")
    tracker_sessions: Mapped[list["TrackerSession"]] = relationship("TrackerSession", back_populates="trip", cascade="all, delete-orphan")
    tracker_photos: Mapped[list["TrackerPhoto"]] = relationship("TrackerPhoto", back_populates="trip", cascade="all, delete-orphan")

    @property
    def progress(self) -> float:
        return round(min(self.spent / self.budget * 100, 100), 1) if self.budget > 0 else 0.0


class Place(Base):
    __tablename__ = "places"
    id:         Mapped[int]         = mapped_column(primary_key=True, index=True)
    trip_id:    Mapped[int]         = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    name:       Mapped[str]         = mapped_column(String(200))
    place_type: Mapped[str]         = mapped_column(String(100), default="Attraction")
    address:    Mapped[str|None]    = mapped_column(String(500), nullable=True)
    notes:      Mapped[str|None]    = mapped_column(Text, nullable=True)
    latitude:   Mapped[float|None]  = mapped_column(Float, nullable=True)
    longitude:  Mapped[float|None]  = mapped_column(Float, nullable=True)
    rating:     Mapped[float|None]  = mapped_column(Float, nullable=True)
    visit_time: Mapped[str|None]    = mapped_column(String(20), nullable=True)
    status:     Mapped[PlaceStatus] = mapped_column(Enum(PlaceStatus), default=PlaceStatus.planned)
    order_idx:  Mapped[int]         = mapped_column(Integer, default=0)
    created_at: Mapped[datetime]    = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime]    = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    trip: Mapped["Trip"] = relationship("Trip", back_populates="places")


class Expense(Base):
    __tablename__ = "expenses"
    __table_args__ = (
        Index("ix_expenses_spent_at", "spent_at"),   # perf: ORDER BY spent_at DESC
    )
    id:         Mapped[int]              = mapped_column(primary_key=True, index=True)
    trip_id:    Mapped[int]              = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    title:      Mapped[str]              = mapped_column(String(200))
    amount:     Mapped[float]            = mapped_column(Float)
    category:   Mapped[ExpenseCategory]  = mapped_column(Enum(ExpenseCategory), default=ExpenseCategory.other)
    notes:      Mapped[str|None]         = mapped_column(Text, nullable=True)
    spent_at:   Mapped[datetime]         = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime]         = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime]         = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    trip: Mapped["Trip"] = relationship("Trip", back_populates="expenses")


class Photo(Base):
    __tablename__ = "photos"
    id:          Mapped[int]      = mapped_column(primary_key=True, index=True)
    trip_id:     Mapped[int]      = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    filename:    Mapped[str]      = mapped_column(String(300))
    url:         Mapped[str]      = mapped_column(String(500))
    caption:     Mapped[str|None] = mapped_column(String(500), nullable=True)
    is_cover:    Mapped[bool]     = mapped_column(Boolean, default=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    trip: Mapped["Trip"] = relationship("Trip", back_populates="photos")


class ItineraryDay(Base):
    __tablename__ = "itinerary_days"
    __table_args__ = (
        UniqueConstraint("trip_id", "day_number", name="uq_itinerary_trip_day"),  # no duplicate days
        Index("ix_itinerary_days_day_number", "day_number"),                       # perf: ORDER BY day_number
    )
    id:          Mapped[int]      = mapped_column(primary_key=True, index=True)
    trip_id:     Mapped[int]      = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    day_number:  Mapped[int]      = mapped_column(Integer)
    date_label:  Mapped[str|None] = mapped_column(String(30), nullable=True)
    title:       Mapped[str]      = mapped_column(String(200))
    notes:       Mapped[str|None] = mapped_column(Text, nullable=True)
    place_names: Mapped[str|None] = mapped_column(Text, nullable=True)
    created_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    trip: Mapped["Trip"] = relationship("Trip", back_populates="itinerary")

    @property
    def places_list(self) -> list[str]:
        return [p.strip() for p in self.place_names.split(",")] if self.place_names else []


class Note(Base):
    __tablename__ = "notes"
    id:         Mapped[int]      = mapped_column(primary_key=True, index=True)
    trip_id:    Mapped[int]      = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    title:      Mapped[str]      = mapped_column(String(200))
    content:    Mapped[str]      = mapped_column(Text)
    color:      Mapped[str]      = mapped_column(String(20), default="#00D4FF")
    pinned:     Mapped[bool]     = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    trip: Mapped["Trip"] = relationship("Trip", back_populates="notes")


class ChecklistItem(Base):
    __tablename__ = "checklist_items"
    id:         Mapped[int]      = mapped_column(primary_key=True, index=True)
    trip_id:    Mapped[int]      = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    text:       Mapped[str]      = mapped_column(String(500))
    done:       Mapped[bool]     = mapped_column(Boolean, default=False)
    category:   Mapped[str]      = mapped_column(String(100), default="General")
    order_idx:  Mapped[int]      = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    trip: Mapped["Trip"] = relationship("Trip", back_populates="checklist_items")


class ShareToken(Base):
    __tablename__ = "share_tokens"
    __table_args__ = (
        UniqueConstraint("trip_id", "owner_id", name="uq_share_trip_owner"),  # one token per trip per owner
    )
    id:         Mapped[int]      = mapped_column(primary_key=True)
    token:      Mapped[str]      = mapped_column(String(64), unique=True, index=True, default=lambda: secrets.token_urlsafe(32))
    trip_id:    Mapped[int]      = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"))
    owner_id:   Mapped[int]      = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    trip:  Mapped["Trip"] = relationship("Trip",  back_populates="share_tokens")
    owner: Mapped["User"] = relationship("User")


class TrackerSession(Base):
    __tablename__ = "tracker_sessions"
    __table_args__ = (
        Index("ix_tracker_sessions_trip_start", "trip_id", "start_time"),
    )
    id:             Mapped[int]      = mapped_column(primary_key=True, index=True)
    trip_id:        Mapped[int]      = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    name:           Mapped[str]      = mapped_column(String(200))
    start_time:     Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    end_time:       Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_distance: Mapped[float]    = mapped_column(Float, default=0.0)
    duration:       Mapped[int]      = mapped_column(Integer, default=0)
    coord_count:    Mapped[int]      = mapped_column(Integer, default=0)
    path_json:      Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at:     Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at:     Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    trip: Mapped["Trip"] = relationship("Trip", back_populates="tracker_sessions")
    photos: Mapped[list["TrackerPhoto"]] = relationship("TrackerPhoto", back_populates="session", cascade="all, delete-orphan")


class TrackerPhoto(Base):
    __tablename__ = "tracker_photos"
    __table_args__ = (
        Index("ix_tracker_photos_trip_captured", "trip_id", "captured_at"),
    )
    id:          Mapped[int]      = mapped_column(primary_key=True, index=True)
    trip_id:     Mapped[int]      = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    session_id:  Mapped[int | None] = mapped_column(ForeignKey("tracker_sessions.id", ondelete="SET NULL"), index=True, nullable=True)
    filename:    Mapped[str]      = mapped_column(String(300))
    url:         Mapped[str]      = mapped_column(String(500))
    latitude:    Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude:   Mapped[float | None] = mapped_column(Float, nullable=True)
    size_bytes:  Mapped[int | None] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    trip: Mapped["Trip"] = relationship("Trip", back_populates="tracker_photos")
    session: Mapped["TrackerSession"] = relationship("TrackerSession", back_populates="photos")


class AIChat(Base):
    __tablename__ = "ai_chats"
    __table_args__ = (
        UniqueConstraint("owner_id", "trip_id", name="uq_ai_chat_owner_trip"),
        Index("ix_ai_chats_trip_owner", "trip_id", "owner_id"),
    )
    id:        Mapped[int]      = mapped_column(primary_key=True, index=True)
    owner_id:  Mapped[int]      = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    trip_id:   Mapped[int]      = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    messages_json: Mapped[str]  = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    owner: Mapped["User"] = relationship("User")
    trip:  Mapped["Trip"] = relationship("Trip")


class UserSetting(Base):
    __tablename__ = "user_settings"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_user_setting_key"),
    )
    id:         Mapped[int]      = mapped_column(primary_key=True, index=True)
    user_id:    Mapped[int]      = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key:        Mapped[str]      = mapped_column(String(100), index=True)
    value_text: Mapped[str]      = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    user: Mapped["User"] = relationship("User", back_populates="settings")

