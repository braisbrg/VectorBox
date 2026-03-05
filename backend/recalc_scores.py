import asyncio
import logging
import os
import sys

# Add current directory to path so it can find config/models/services
sys.path.append(os.getcwd())

from config import AsyncSessionLocal
from sqlalchemy import select
from models.database import Movie
from services.omdb_client import OMDbClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('recalc')

async def recalculate_all():
    api_key = os.getenv("OMDB_API_KEY")
    if not api_key:
        print("❌ OMDB_API_KEY not found in environment")
        return

    omdb = OMDbClient(api_key=api_key)
    updated = 0
    failed = 0
    skipped = 0

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Movie)
            .where(Movie.imdb_id.isnot(None))
            .order_by(Movie.id)
        )
        movies = result.scalars().all()
        total = len(movies)
        logger.info(f'Recalculating scores for {total} movies...')

        for i, movie in enumerate(movies, 1):
            try:
                # Fetch fresh data from OMDb
                omdb_data = await omdb.fetch_movie_data(movie.imdb_id)
                
                # Calculate new score
                score_obj = omdb.calculate_vectorbox_score(
                    omdb_data,
                    movie.vote_average
                )
                
                if score_obj.score is not None and score_obj.score > 0:
                    movie.vectorbox_score = score_obj.score
                    # Also update other ratings from breakdown
                    movie.imdb_rating = score_obj.breakdown.imdb
                    movie.metacritic_rating = score_obj.breakdown.meta
                    updated += 1
                else:
                    skipped += 1

                if i % 10 == 0:
                    await db.commit()
                    logger.info(
                        f'Progress: {i}/{total} '
                        f'(updated={updated}, '
                        f'skipped={skipped}, '
                        f'failed={failed})'
                    )
                
                # Rate limit if necessary (189 movies is fine for 1000/day limit)
                # await asyncio.sleep(0.05)

            except Exception as e:
                logger.warning(f'Failed {movie.title}: {e}')
                failed += 1
                continue

        # Final commit
        await db.commit()

    logger.info('=' * 50)
    logger.info(f'Recalculation complete!')
    logger.info(f'  Updated : {updated}')
    logger.info(f'  Skipped : {skipped} (no data available)')
    logger.info(f'  Failed  : {failed}')
    logger.info(f'  Total   : {total}')

    if updated > 0:
        print(f'✅ {updated} scores recalculated with new formula')
    else:
        print('⚠️  No scores updated — check OMDb API key')

if __name__ == "__main__":
    asyncio.run(recalculate_all())
