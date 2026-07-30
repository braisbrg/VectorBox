import asyncio
from qdrant_client import AsyncQdrantClient

async def main():
    client = AsyncQdrantClient('http://qdrant:6333')
    await client.delete_collection('movies')
    print('Collection deleted')
    await client.close()

if __name__ == '__main__':
    asyncio.run(main())
