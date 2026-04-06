from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from core.config import get_settings

settings = get_settings()
database_url_lower = settings.database_url.lower()
is_pooled_postgres = (
    ":6543" in database_url_lower
    or "pooler." in database_url_lower
    or "-pooler." in database_url_lower
    or "pgbouncer=true" in database_url_lower
)

COMMON_ENGINE_KWARGS = {
    "echo": False,
    "pool_size": 3,
    "max_overflow": 5,
    "pool_pre_ping": True,
    "pool_recycle": 1800,
    "pool_timeout": 20,
    "pool_reset_on_return": "rollback",
}

if "sqlite" in database_url_lower:
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        connect_args={"check_same_thread": False},
    )
elif is_pooled_postgres:
    # PgBouncer/pooled Postgres connections (Supabase/Neon poolers, etc.).
    # Prepared statement caching must be disabled because PgBouncer doesn't support it.
    engine = create_async_engine(
        settings.database_url,
        connect_args={
            "statement_cache_size": 0,
            # Avoid transient 500s when DB wake-up or network jitter is >10s.
            "command_timeout": 30,
            "timeout": 20,
            "ssl": "require",
            "server_settings": {"application_name": "travelmate_backend"},
        },
        **COMMON_ENGINE_KWARGS,
    )
else:
    # Direct Postgres connection (no PgBouncer/pooler).
    # Prepared statements work fine here; no statement_cache_size override needed.
    engine = create_async_engine(
        settings.database_url,
        connect_args={
            # Avoid transient 500s when DB wake-up or network jitter is >10s.
            "command_timeout": 30,
            "timeout": 20,
            "ssl": "require",
            "server_settings": {"application_name": "travelmate_backend"},
        },
        **COMMON_ENGINE_KWARGS,
    )

SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    session = SessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
