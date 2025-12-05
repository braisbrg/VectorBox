import httpx
import asyncio
import os
from dotenv import load_dotenv

# Load environment variables from the parent directory (root)
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

async def fetch_providers():
    token = os.getenv("TMDB_READ_TOKEN")
    if not token:
        print("Error: TMDB_READ_TOKEN not found in environment variables.")
        return

    url = "https://api.themoviedb.org/3/watch/providers/movie"
    params = {"watch_region": "ES"}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, params=params)
        if response.status_code == 200:
            data = response.json()
            results = data.get("results", [])
            print(f"Found {len(results)} providers for Spain (ES):")
            for provider in results:
                print(f"ID: {provider['provider_id']}, Name: {provider['provider_name']}")
        else:
            print(f"Error: {response.status_code} - {response.text}")

if __name__ == "__main__":
    asyncio.run(fetch_providers())
