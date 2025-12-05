import asyncio
import os
import sys

# Add parent directory to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from config import engine

async def migrate():
    print("Starting Popularity Migration...")
    async with engine.begin() as conn:
        # Add columns if they don't exist
        columns = [
            ("letterboxd_rating", "FLOAT"),
        ]
        
        for col_name, col_type in columns:
            try:
                print(f"Adding column {col_name}...")
                await conn.execute(text(f"ALTER TABLE movies ADD COLUMN IF NOT EXISTS {col_name} {col_type}"))
            except Exception as e:
                print(f"Error adding {col_name}: {e}")
                
    print("Migration complete!")

if __name__ == "__main__":
    asyncio.run(migrate())
