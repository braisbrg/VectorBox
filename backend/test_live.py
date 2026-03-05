import asyncio
import os
import sys
sys.path.append(os.getcwd())

from services.omdb_client import OMDbClient

async def test():
    api_key = os.getenv('OMDB_API_KEY')
    if not api_key:
        print("❌ OMDB_API_KEY not found")
        return
        
    client = OMDbClient(api_key)
    score = client.calculate_vectorbox_score(
        await client.fetch_movie_data('tt0468569'), 
        8.5
    )
    print(f'The Dark Knight VB score: {score.score}')
    assert score.score is not None and score.score > 75
    print('Live score test passed ✅')

if __name__ == "__main__":
    asyncio.run(test())
