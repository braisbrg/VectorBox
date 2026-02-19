import asyncio
import logging
import sys
import os

# Ensure backend directory is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.qdrant_service import QdrantService
from qdrant_client.http import models

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def create_indexes():
    logger.info("Connecting to Qdrant...")
    service = QdrantService()
    
    collection_name = "movies" # Assuming 'movies' is the collection name, QdrantService defaults to it?
    # QdrantService reads COLLECTION_NAME from config or defaults.
    # Let's check QdrantService to be sure, but usually we iterate collections?
    # No, usually just one main collection.
    
    # We can check the init of QdrantService, but for now we assume it manages the client correctly.
    # But QdrantService wrapper methods handle connection. We need raw client or new methods?
    # QdrantService wraps operations. We should access the client directly or add a method.
    # QdrantService exposes `self.client` (AsyncQdrantClient).
    
    client = service.client
    collection_name = service.COLLECTION_NAME
    
    logger.info(f"Targeting collection: {collection_name}")

    fields_to_index = [
        ("vote_count", models.PayloadSchemaType.INTEGER),
        ("vectorbox_score", models.PayloadSchemaType.FLOAT),
        ("popularity", models.PayloadSchemaType.FLOAT),
        ("year", models.PayloadSchemaType.INTEGER),
        ("genres", models.PayloadSchemaType.KEYWORD),
        ("vote_average", models.PayloadSchemaType.FLOAT),
        ("runtime", models.PayloadSchemaType.INTEGER),
        ("original_language", models.PayloadSchemaType.KEYWORD),
        ("keywords", models.PayloadSchemaType.KEYWORD)
    ]
    
    for field_name, field_type in fields_to_index:
        logger.info(f"Creating index for field: {field_name} ({field_type})")
        try:
            await client.create_payload_index(
                collection_name=collection_name,
                field_name=field_name,
                field_schema=field_type
            )
            logger.info(f"Index created for {field_name}")
        except Exception as e:
            logger.warning(f"Failed to create index for {field_name}: {e}")

    logger.info("Yielding QdrantService...")
    # No close needed if singleton usage or script usage, but good practice if standalone.
    # The client might need explicit close if not using context manager?
    # Client is async.
    await client.close()
    logger.info("Done.")

if __name__ == "__main__":
    asyncio.run(create_indexes())
