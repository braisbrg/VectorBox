#!/usr/bin/env python3
"""
apply_qdrant_quantization.py — one-off script to enable INT8 scalar quantization
on the existing 'movies' Qdrant collection.

Effect:
  - Reduces in-memory footprint by ~4x (float32 → int8)
  - `always_ram=True` keeps quantized vectors in RAM for fast ANN search
  - Full-precision vectors are stored on disk for reranking (rescoring=True)
  - Re-indexing happens in the background; the collection stays available

Run inside the container:
    docker compose exec backend python scripts/apply_qdrant_quantization.py

This is idempotent: re-running when quantization is already enabled is a no-op.
"""
import asyncio
import logging
import os

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    ScalarQuantization,
    ScalarQuantizationConfig,
    ScalarType,
    QuantizationConfig,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

COLLECTION_NAME = "movies"


async def main() -> None:
    qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
    client = AsyncQdrantClient(url=qdrant_url)

    # Check current collection info
    info = await client.get_collection(COLLECTION_NAME)
    existing_quant = info.config.quantization_config
    if existing_quant is not None:
        logger.info(
            "Quantization already configured on '%s': %s — nothing to do.",
            COLLECTION_NAME,
            existing_quant,
        )
        await client.close()
        return

    logger.info("Applying INT8 scalar quantization to '%s'...", COLLECTION_NAME)
    await client.update_collection(
        collection_name=COLLECTION_NAME,
        quantization_config=QuantizationConfig(
            scalar=ScalarQuantization(
                scalar=ScalarQuantizationConfig(
                    type=ScalarType.INT8,
                    # always_ram=True: quantized index stays in memory for fast ANN
                    # Full-precision vectors are used for rescoring (higher accuracy)
                    always_ram=True,
                )
            )
        ),
        # Optimizers will rebuild the index in the background.
        # The collection remains queryable throughout.
    )
    logger.info(
        "Quantization config submitted. Qdrant will re-index '%s' in the background.",
        COLLECTION_NAME,
    )
    logger.info(
        "Monitor progress: GET %s/collections/%s", qdrant_url, COLLECTION_NAME
    )
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
