import asyncio
from services.qdrant_service import QdrantService
from services.embedding_service import EmbeddingService

async def main():
    qdrant = QdrantService()
    embedding_service = EmbeddingService()
    
    query = "The Godfather"
    print(f"Searching for: {query}")
    
    # Generate simple embedding for search
    vector = embedding_service.generate_embedding({
        "title": query,
        "overview": "",
        "genres": [],
        "keywords": []
    }).tolist()
    
    # This calls the fixed search_similar method
    results = await qdrant.search_similar(query_vector=vector, limit=5)
    
    for r in results:
        metadata = r.get("metadata", {})
        title = metadata.get("title", "Unknown")
        print(f"\nFound: {title}")
        print(f"Score: {r['score']}")
        print(f"poster_path: {metadata.get('poster_path')}")
        print(f"vote_count: {metadata.get('vote_count')}")
        print(f"original_language: {metadata.get('original_language')}")
        print(f"vectorbox_score: {metadata.get('vectorbox_score')}")
        print(f"keywords: {metadata.get('keywords')}")
        print("-" * 20)

if __name__ == "__main__":
    asyncio.run(main())
