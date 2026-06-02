"""Curated bootstrap seed: guarantees the catalogue contains the canonical
sagas + studios that everyone expects to find (MCU, Star Wars, Pixar,
Ghibli, etc.). Idempotent — re-runs only ingest films missing from DB.

Each entry is one batch through the existing DatabaseSeeder. Companies use
`--strategy by_company` (paginated discover, capped by `limit`). Collections
use `--strategy by_collection` (single TMDB call, returns all parts).

To add a new essential: append a dict below. To run only one entry: use
`seed_db.py --strategy by_company|by_collection --company-id|--collection-id N`.
"""
import asyncio
import logging
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AsyncSessionLocal
from scripts.seed_db import DatabaseSeeder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


ESSENTIALS = [
    # ── Companies (broad sweeps, capped by limit) ───────────────────────
    {"kind": "company", "id": 420,   "name": "Marvel Studios",        "limit": 100},
    {"kind": "company", "id": 1,     "name": "Lucasfilm",             "limit": 50},
    {"kind": "company", "id": 3,     "name": "Pixar",                 "limit": 50},
    {"kind": "company", "id": 2,     "name": "Walt Disney Pictures",  "limit": 400},
    {"kind": "company", "id": 6125,  "name": "Walt Disney Animation", "limit": 100},
    {"kind": "company", "id": 10342, "name": "Studio Ghibli",         "limit": 50},
    {"kind": "company", "id": 521,   "name": "DreamWorks Animation",  "limit": 60},
    {"kind": "company", "id": 41077, "name": "A24",                   "limit": 200},
    {"kind": "company", "id": 6194,  "name": "Laika",                 "limit": 20},
    {"kind": "company", "id": 12,    "name": "New Line Cinema",       "limit": 200},
    {"kind": "company", "id": 7505,  "name": "Marvel Entertainment",  "limit": 150},
    {"kind": "company", "id": 9993,  "name": "DC Entertainment",      "limit": 100},
    {"kind": "company", "id": 21,    "name": "Metro-Goldwyn-Mayer",   "limit": 100},
    {"kind": "company", "id": 174,   "name": "Warner Bros.",          "limit": 200},
    {"kind": "company", "id": 33,    "name": "Universal Pictures",    "limit": 200},
    {"kind": "company", "id": 25,    "name": "20th Century Studios",  "limit": 200},

    # ── Collections (full enumeration, no cap needed) ───────────────────
    # Big franchises
    {"kind": "collection", "id": 10,     "name": "Star Wars"},
    {"kind": "collection", "id": 645,    "name": "James Bond"},
    {"kind": "collection", "id": 1241,   "name": "Harry Potter"},
    {"kind": "collection", "id": 119,    "name": "The Lord of the Rings"},
    {"kind": "collection", "id": 121938, "name": "The Hobbit"},
    {"kind": "collection", "id": 295,    "name": "Pirates of the Caribbean"},
    {"kind": "collection", "id": 328,    "name": "Jurassic Park"},
    {"kind": "collection", "id": 84,     "name": "Indiana Jones"},
    {"kind": "collection", "id": 87359,  "name": "Mission: Impossible"},
    {"kind": "collection", "id": 404609, "name": "John Wick"},
    {"kind": "collection", "id": 9485,   "name": "The Fast and the Furious"},
    {"kind": "collection", "id": 2344,   "name": "The Matrix"},
    {"kind": "collection", "id": 528,    "name": "The Terminator"},
    {"kind": "collection", "id": 8091,   "name": "Alien"},
    {"kind": "collection", "id": 399,    "name": "Predator"},
    {"kind": "collection", "id": 1570,   "name": "Die Hard"},
    {"kind": "collection", "id": 8945,   "name": "Mad Max"},
    {"kind": "collection", "id": 264,    "name": "Back to the Future"},
    {"kind": "collection", "id": 2980,   "name": "Ghostbusters"},
    {"kind": "collection", "id": 1575,   "name": "Rocky"},
    {"kind": "collection", "id": 230,    "name": "The Godfather"},
    {"kind": "collection", "id": 263,    "name": "The Dark Knight Trilogy"},
    {"kind": "collection", "id": 86311,  "name": "The Avengers"},
    {"kind": "collection", "id": 173710, "name": "Planet of the Apes (Reboot)"},
    # Pixar / animation sagas
    {"kind": "collection", "id": 10194,  "name": "Toy Story"},
    {"kind": "collection", "id": 87118,  "name": "Cars"},
    {"kind": "collection", "id": 468222, "name": "The Incredibles"},
    {"kind": "collection", "id": 137697, "name": "Finding Nemo"},
    {"kind": "collection", "id": 313086, "name": "Monsters, Inc."},
    {"kind": "collection", "id": 86029,  "name": "Inside Out"},
    {"kind": "collection", "id": 2150,   "name": "Shrek"},
    {"kind": "collection", "id": 86066,  "name": "Despicable Me"},
    {"kind": "collection", "id": 89137,  "name": "How to Train Your Dragon"},
    {"kind": "collection", "id": 14740,  "name": "Madagascar"},
    {"kind": "collection", "id": 9489,   "name": "Kung Fu Panda"},
    {"kind": "collection", "id": 33051,  "name": "Ice Age"},
    # Disney live-action / animated classics
    {"kind": "collection", "id": 87096,  "name": "Avatar"},
    {"kind": "collection", "id": 8650,   "name": "Toy Story Toons"},
    {"kind": "collection", "id": 24468,  "name": "The Hunger Games"},
    {"kind": "collection", "id": 131635, "name": "The Hunger Games (Mockingjay split)"},
    {"kind": "collection", "id": 33514,  "name": "The Twilight Saga"},
]


async def main():
    seeder = DatabaseSeeder(limit=0)
    try:
        await seeder.qdrant.init_collection()
        async with AsyncSessionLocal() as db:
            existing_ids = await seeder.get_existing_tmdb_ids(db)
            logger.info(f"Starting with {len(existing_ids)} existing movies in DB")

            for entry in ESSENTIALS:
                kind = entry["kind"]
                name = entry["name"]
                logger.info(f"--- {kind.upper()}: {name} (id={entry['id']}) ---")
                if kind == "company":
                    seeder.strategy = "by_company"
                    seeder.company_id = entry["id"]
                    seeder.collection_id = None
                    seeder.limit = entry.get("limit", 100)
                elif kind == "collection":
                    seeder.strategy = "by_collection"
                    seeder.collection_id = entry["id"]
                    seeder.company_id = None
                    seeder.limit = 9999  # ignored by /collection
                else:
                    logger.warning(f"Unknown kind '{kind}', skipping")
                    continue

                try:
                    await seeder.seed_batch(db, existing_ids)
                except Exception as e:
                    logger.error(f"Failed seeding {name}: {e}")

        logger.info(
            f"Essentials seed complete. Processed: {seeder.processed_count}, "
            f"Errors: {seeder.error_count}"
        )
    finally:
        await seeder.aclose()


if __name__ == "__main__":
    asyncio.run(main())
