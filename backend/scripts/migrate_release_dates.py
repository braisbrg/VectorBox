import asyncio
import os
import sys

# Fix path to backend root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import engine
from sqlalchemy import text

async def migrate():
    print("Starting migration: Add release_dates JSONB column...")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE movies ADD COLUMN IF NOT EXISTS release_dates JSONB;"))
        print("Migration success: Column added.")
    except Exception as e:
        print(f"Migration failed: {e}")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(migrate())
