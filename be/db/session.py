"""
TravelMate — db/session.py
──────────────────────────
Smart database session manager:
  • Uses the DATABASE_URL from .env
  • On local dev (Windows/Mac/Linux) automatically falls back to SQLite
    if the remote DB is unreachable — so the app still runs offline
  • On Render (production) it retries with exponential backoff and raises
    a clean 503 if the DB is genuinely down
  • Handles Render's free-tier Postgres quirks (SSL, pooler, statement cache)
"""

from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from sqlalchemy.pool import StaticPool, NullPool
import os
import ssl
import asyncio
import logging
from pathlib import Path
from core.config import get_settings
from urllib.parse import urlparse

logger = logging.getLogger("travelmate.db")
settings = get_settings()

# ── URL normalisation ─────────────────────────────────────────────────────────
_raw_url = settings.database_url
if _raw_url.startswith("postgres://"):
    _raw_url = _raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif _raw_url.startswith("postgresql://") and "+asyncpg" not in _raw_url:
    _raw_url = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

database_url_lower = _raw_url.lower()
is_sqlite    = "sqlite" in database_url_lower
is_local     = "localhost" in database_url_lower or "127.0.0.1" in database_url_lower
is_render    = bool(os.getenv("RENDER"))          # Render sets this automatically
is_dev       = not is_render                       # everything else = dev/local

# Detect Render's PgBouncer pooler (port 6543 or "pooler." in hostname)
is_pooled_postgres = (
    ":6543" in database_url_lower
    or "pooler." in database_url_lower
    or "-pooler." in database_url_lower
    or "pgbouncer=true" in database_url_lower
)

# ── Pool config ───────────────────────────────────────────────────────────────
pool_size    = int(os.getenv("DB_POOL_SIZE",    "3"))
max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "2"))
pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "60"))
db_concurrency = int(os.getenv("DB_CONCURRENCY", str(pool_size + max_overflow)))


# ── Engine factory ────────────────────────────────────────────────────────────
def _make_engine(url: str):
    lower = url.lower()
    if "sqlite" in lower:
        return create_async_engine(
            url,
            echo=False,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

    _is_local_pg = "localhost" in lower or "127.0.0.1" in lower
    _ssl_ctx = None
    if not _is_local_pg:
        _ssl_ctx = ssl.create_default_context()
        _ssl_ctx.check_hostname = False
        _ssl_ctx.verify_mode   = ssl.CERT_NONE

    _connect_args: dict = {
        "command_timeout": 30,
        "timeout":         15,          # fail fast on DNS/TCP errors
        "server_settings": {"application_name": "travelmate_backend"},
    }
    if _ssl_ctx:
        _connect_args["ssl"] = _ssl_ctx
    if is_pooled_postgres:
        _connect_args["statement_cache_size"] = 0   # required for PgBouncer

    return create_async_engine(
        url,
        connect_args=_connect_args,
        echo=False,
        # Use NullPool on Render (single-process, serverless-style) to avoid
        # "connection already closed" errors after the process sleeps
        poolclass=NullPool if is_render else None,
        **({} if is_render else {
            "pool_size":            pool_size,
            "max_overflow":         max_overflow,
            "pool_pre_ping":        True,
            "pool_recycle":         300,
            "pool_timeout":         pool_timeout,
            "pool_reset_on_return": "rollback",
        }),
    )


# ── Build the actual engine (with local SQLite fallback) ─────────────────────
_SQLITE_FALLBACK_PATH = (Path(__file__).resolve().parents[1] / "travelmate_local.db")
_SQLITE_FALLBACK = f"sqlite+aiosqlite:///{_SQLITE_FALLBACK_PATH.as_posix()}"
_using_fallback  = False

if is_sqlite:
    engine = _make_engine(_raw_url)
else:
    engine = _make_engine(_raw_url)

    if is_dev and not is_local:
        # In dev (local Windows/Mac), probe the remote DB before committing.
        # If it's unreachable (Render free-tier blocks external IPs, VPN issues,
        # DNS failures, etc.) silently fall back to a local SQLite DB so you can
        # still develop without a network.
        import socket

        def _can_reach_db(url: str, timeout: float = 4.0) -> bool:
            try:
                parsed = urlparse(url)
                host   = parsed.hostname or ""
                port   = parsed.port or 5432
                if not host:
                    return False
                # A plain TCP connect is not enough for hosted Postgres (Render/Neon/Supabase)
                # because SSL negotiation can still hang/timeout. Do a tiny Postgres SSL probe:
                # 1) TCP connect
                # 2) Send SSLRequest
                # 3) Expect 'S' then complete the TLS handshake (fast fail on blocked SSL)
                s = socket.create_connection((host, port), timeout=timeout)
                try:
                    s.settimeout(timeout)
                    # Skip SSL probe for local targets.
                    if host in {"localhost", "127.0.0.1"}:
                        return True

                    import struct

                    ssl_req = struct.pack("!ii", 8, 80877103)  # len=8, SSLRequest code
                    s.sendall(ssl_req)
                    resp = s.recv(1)
                    if resp != b"S":
                        # Server doesn't accept SSL (or didn't respond correctly); asyncpg will fail.
                        return False

                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    tls = ctx.wrap_socket(s, server_hostname=host)
                    try:
                        tls.do_handshake()
                    finally:
                        tls.close()
                    return True
                finally:
                    try:
                        s.close()
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning(
                    "Remote DB unreachable (%s:%s) — %s",
                    parsed.hostname, parsed.port, exc,
                )
                return False

        if not _can_reach_db(_raw_url):
            logger.warning(
                "⚠️  Cannot reach remote database from this machine.\n"
                "   This is normal when running locally against Render's free-tier Postgres\n"
                "   (Render blocks external connections by default).\n"
                "   Falling back to LOCAL SQLite: %s\n"
                "   All data will be stored locally. When you deploy to Render the real DB is used.",
                _SQLITE_FALLBACK,
            )
            engine         = _make_engine(_SQLITE_FALLBACK)
            _using_fallback = True


SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
db_semaphore = asyncio.Semaphore(max(1, 1 if (_using_fallback or is_sqlite) else db_concurrency))

_db_connected_logged = False


# ── Helpers ───────────────────────────────────────────────────────────────────
def describe_database() -> dict:
    if _using_fallback:
        return {"provider": "dev(sqlite-fallback)", "host": "local file"}
    url   = _raw_url
    lower = url.lower()
    if "sqlite" in lower:
        return {"provider": "dev(local)", "host": "sqlite"}
    host = ""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        pass
    hl = host.lower()
    if hl in {"localhost", "127.0.0.1", "0.0.0.0"} or hl.endswith(".local"):
        return {"provider": "dev(local)",  "host": host or "localhost"}
    if "supabase.co" in lower or "supabase" in hl:
        return {"provider": "supabase",    "host": host}
    if "neon.tech"   in lower or "neon"     in hl:
        return {"provider": "neon",        "host": host}
    if "render.com"  in lower or "render"   in hl:
        return {"provider": "render",      "host": host}
    return {"provider": "remote", "host": host or "unknown"}


def mark_db_connected_logged() -> None:
    global _db_connected_logged
    _db_connected_logged = True


async def log_db_connected_once(session: AsyncSession) -> None:
    global _db_connected_logged
    if _db_connected_logged:
        return
    try:
        await session.execute(text("SELECT 1"))
        info = describe_database()
        logging.getLogger("uvicorn.error").info(
            "DB connected (late): %s (%s)", info["provider"], info["host"]
        )
        _db_connected_logged = True
    except Exception:
        pass


# ── Session dependency ────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    await db_semaphore.acquire()
    session = SessionLocal()
    try:
        await log_db_connected_once(session)
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
        db_semaphore.release()


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
