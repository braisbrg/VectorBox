
import asyncio
import os
import sys

# Add backend directory to path
sys.path.append(os.getcwd())

from config import AsyncSessionLocal
from services.qdrant_service import QdrantService
from services.embedding_service import EmbeddingService
from services.tmdb_client import TMDBClient
from models.database import Movie
from sqlalchemy import select

async def debug_comparison(movie_title="Train Dreams"):
    print(f"--- Debugging Comparison for '{movie_title}' ---")
    
    async with AsyncSessionLocal() as db:
        # 1. Get Anchor Movie
        result = await db.execute(select(Movie).where(Movie.title.ilike(f"%{movie_title}%")))
        anchor = result.scalars().first()
        
        if not anchor:
            print(f"Movie '{movie_title}' not found in DB!")
            return

        print(f"Anchor: {anchor.title} (TMDB ID: {anchor.tmdb_id})")
        
        # 2. Generate Content-Only Vector (Feed Logic & Similar Logic)
        embedding_service = EmbeddingService()
        vector = embedding_service.generate_embedding({
            "title": anchor.title, 
            "overview": anchor.overview or "",
            "genres": anchor.genres or [],
            "keywords": [] # Simplified for debug
        }, include_title=False).tolist()
        
        print("Generated Content-Only Vector.")
        
        # 3. Search Qdrant (Raw Trace)
        qdrant = QdrantService()
        print("\n--- Qdrant Search (Top 10) ---")
        results = await qdrant.search_similar(
            query_vector=vector,
            limit=10,
            score_threshold=0.0 # No threshold for debug
        )
        
        
        with open("debug_results.txt", "w", encoding="utf-8") as f:
            f.write(f"Found {len(results)} raw candidates in Qdrant.\n")
            f.write(f"{'SCORE':<8} | {'TMDB_ID':<10} | {'TITLE':<30} | {'IN_DB?':<10}\n")
            f.write("-" * 70 + "\n")
            
            for res in results:
                score = res['score']
                mid = res['movie_id']
                meta = res.get('metadata', {})
                title = meta.get('title', 'Unknown')
                
                db_res = await db.execute(select(Movie).where(Movie.tmdb_id == mid))
                in_db = db_res.scalar_one_or_none() is not None
                
                f.write(f"{score:.4f}   | {mid:<10} | {title[:30]:<30} | {in_db}\n")
        
        print("Done. Wrote to debug_results.txt")
        # await qdrant.close() # Method might not exist on wrapper

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(debug_comparison())
