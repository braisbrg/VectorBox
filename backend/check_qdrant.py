
from qdrant_client import AsyncQdrantClient
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def check():
    url = os.getenv("QDRANT_URL", "http://localhost:6333")
    print(f"Connecting to {url}...")
    client = AsyncQdrantClient(url=url)
    try:
        collections = await client.get_collections()
        print("Connected!")
        print(f"Collections: {collections}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(check())
