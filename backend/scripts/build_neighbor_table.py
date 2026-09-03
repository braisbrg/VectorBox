"""Precompute each film's nearest neighbours once, so group sync never queries
Qdrant per film at request time.

A film's neighbours only change when the catalogue does, so this is a batch job:
measured 2026-08-03 on 20.418 points — 9.2 s to load the vectors, 7.9 s for the
full centred kNN, 0.4 s to write 2.5 MB to Redis. At request time a group of
three then reads 350 neighbour lists in 1.5 ms (one MGET) instead of spending
565 ms on live batched search — that is FASTER than the centroid path it
replaces, which burns 312 ms just retrieving pool vectors.

Vectors are centred (alpha 0.5) before the kNN: two random films sit at cosine
0.482 in this space, and an uncentred neighbourhood of an averaged query is
dominated by a few hundred hub films. Centring measured -3.7pt [-4.5, -3.0] on
its own.

Run after anything that changes the catalogue or the vectors:
    docker compose exec backend python scripts/build_neighbor_table.py
"""
import asyncio
import json
import logging
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.qdrant_service import QdrantService  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TOP_K = 20            # 50 and 100 both measured worse — wide neighbourhoods drift
CENTER_ALPHA = 0.5    # full centring (1.0) measured worse than half
BLOCK = 2000
KEY = "knn:v1:{}"
META_KEY = "knn:v1:meta"


def _dense(point):
    """The collection carries named vectors since the sparse/lexical migration,
    so `point.vector` is a dict for most points and a bare list for the rest."""
    vector = point.vector
    if isinstance(vector, dict):
        for candidate in vector.values():
            if hasattr(candidate, "__len__") and len(candidate) == QdrantService.VECTOR_SIZE:
                return candidate
        return None
    if vector is not None and len(vector) == QdrantService.VECTOR_SIZE:
        return vector
    return None


async def main() -> int:
    started = time.perf_counter()
    qdrant = QdrantService()

    points, _ = await qdrant.client.scroll(
        collection_name=qdrant.COLLECTION_NAME, limit=100_000,
        with_payload=False, with_vectors=True,
    )
    usable = [p for p in points if _dense(p) is not None]
    skipped = len(points) - len(usable)
    if not usable:
        logger.error("No vectors in %s — nothing to build", qdrant.COLLECTION_NAME)
        return 1
    ids = np.array([int(p.id) for p in usable])
    vectors = np.array([_dense(p) for p in usable], dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    logger.info("Loaded %d vectors (%d skipped, no dense vector) in %.1fs",
                len(vectors), skipped, time.perf_counter() - started)

    centroid = vectors.mean(axis=0)
    centroid /= np.linalg.norm(centroid)
    centred = vectors - CENTER_ALPHA * np.outer(vectors @ centroid, centroid)
    centred /= np.linalg.norm(centred, axis=1, keepdims=True)

    t_knn = time.perf_counter()
    total = len(centred)
    neighbours = np.empty((total, TOP_K), dtype=np.int32)
    for start in range(0, total, BLOCK):
        sims = centred[start:start + BLOCK] @ centred.T
        for row in range(sims.shape[0]):
            sims[row, start + row] = -9.0          # never your own neighbour
        part = np.argpartition(-sims, TOP_K, axis=1)[:, :TOP_K]
        rows = np.arange(sims.shape[0])[:, None]
        order = np.argsort(-sims[rows, part], axis=1)
        neighbours[start:start + BLOCK] = part[rows, order]
    logger.info("kNN top-%d for %d films in %.1fs", TOP_K, total, time.perf_counter() - t_knn)

    import redis.asyncio as aioredis
    redis = aioredis.from_url(os.getenv("REDIS_URL", "redis://redis:6379"), decode_responses=True)
    try:
        t_write = time.perf_counter()
        pipe = redis.pipeline()
        for i in range(total):
            pipe.set(KEY.format(int(ids[i])), json.dumps([int(ids[j]) for j in neighbours[i]]))
            if i % 5000 == 4999:
                await pipe.execute()
                pipe = redis.pipeline()
        await pipe.execute()
        await redis.set(META_KEY, json.dumps({
            "films": total, "top_k": TOP_K, "alpha": CENTER_ALPHA,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }))
        logger.info("Wrote %d neighbour lists to Redis in %.1fs", total, time.perf_counter() - t_write)
    finally:
        await redis.close()

    logger.info("Done in %.1fs total", time.perf_counter() - started)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
