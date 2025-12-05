import asyncio
import os
import sys

# Add parent directory to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from config import engine

async def migrate():
    print("Starting Phase 12 Migration...")
    async with engine.begin() as conn:
        # Add columns if they don't exist
        columns = [
            ("imdb_id", "VARCHAR(20)"),
            ("imdb_rating", "FLOAT"),
            ("metacritic_rating", "INTEGER"),
            ("rotten_tomatoes_rating", "INTEGER"),
            ("vectorbox_score", "FLOAT"),
            ("title_es", "VARCHAR(500)"),
            ("overview_es", "TEXT")
        ]
        
        for col_name, col_type in columns:
            try:
                print(f"Adding column {col_name}...")
                await conn.execute(text(f"ALTER TABLE movies ADD COLUMN IF NOT EXISTS {col_name} {col_type}"))
            except Exception as e:
                print(f"Error adding {col_name}: {e}")
                
        # Add unique constraint for imdb_id
        try:
            print("Adding unique constraint for imdb_id...")
            await conn.execute(text("ALTER TABLE movies ADD CONSTRAINT uq_movies_imdb_id UNIQUE (imdb_id)"))
        except Exception as e:
            print(f"Constraint might already exist: {e}")

    print("Migration complete!")

if __name__ == "__main__":
    asyncio.run(migrate())
