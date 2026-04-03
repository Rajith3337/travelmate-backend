import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
import sys

from core.config import get_settings

async def main():
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=True)
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE users ALTER COLUMN avatar_url TYPE text;"))
            print("Successfully altered column.")
        except Exception as e:
            print(f"Error, maybe it's already text or other issue: {e}")

if __name__ == '__main__':
    asyncio.run(main())
