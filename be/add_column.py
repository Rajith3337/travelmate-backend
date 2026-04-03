import asyncio
from sqlalchemy import text
from db.session import engine

async def main():
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE trips ADD COLUMN ai_roadmap TEXT"))
            print("Successfully added ai_roadmap column.")
        except Exception as e:
            print("Error or already exists:", e)

asyncio.run(main())
