"""Compare Trakt /related vs TMDB /recommendations as the data source for Signal C.

Trakt's collab filter is built from real user behaviour on trakt.tv (a tracking
service like Letterboxd, more mainstream user base). Quality may differ from
TMDB's algorithm — this script measures how much.

Setup:
  1. Sign up at https://trakt.tv (free).
  2. Create an API app at https://trakt.tv/oauth/applications (just pick a name,
     callback URL `urn:ietf:wg:oauth:2.0:oob` is fine for a script).
  3. Copy the "Client ID" — that's the API key (no OAuth needed for public reads).
  4. Add to your .env file:  TRAKT_CLIENT_ID=...

Usage:
    docker compose exec backend python scripts/experiment_trakt.py

Output: same format as experiment_signal_c.py — for each seed, lists Trakt's
top related films with vec_sim and genre overlap, plus aggregate stats.
"""
import asyncio
import os
import sys
from collections import defaultdict, Counter

sys.path.append(os.getcwd())

import httpx
import numpy as np
from sqlalchemy import select, or_, func

from config import AsyncSessionLocal
from models.database import Movie
from services.qdrant_service import QdrantService

TRAKT_BASE = "https://api.trakt.tv"

SEEDS = [
    ("niche", "Eterna"),
    ("niche", "Wolf Beach"),
    ("niche", "The Voice of Hind Rajab"),
    ("niche", "Puparia"),
    ("art-house", "La Chimera"),
    ("art-house", "Pan's Labyrinth"),
    ("art-house", "Howl's Moving Castle"),
    ("art-house", "In the Mood for Love"),
    ("popular", "Inception"),
    ("popular", "GoodFellas"),
    ("popular", "Pulp Fiction"),
    ("popular", "Spirited Away"),
]


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def trakt_headers(client_id: str) -> dict:
    return {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": client_id,
    }


async def trakt_lookup_by_tmdb(client: httpx.AsyncClient, tmdb_id: int, headers: dict) -> dict | None:
    """Resolve a TMDB ID to a Trakt entity."""
    try:
        r = await client.get(f"{TRAKT_BASE}/search/tmdb/{tmdb_id}", params={"type": "movie"}, headers=headers, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data:
            return None
        return data[0]  # first match
    except Exception as e:
        print(f"  !! Trakt lookup failed for tmdb={tmdb_id}: {e}")
        return None


async def trakt_related(client: httpx.AsyncClient, slug_or_id, headers: dict) -> list:
    try:
        r = await client.get(f"{TRAKT_BASE}/movies/{slug_or_id}/related", params={"limit": 10}, headers=headers, timeout=15)
        if r.status_code != 200:
            return []
        return r.json() or []
    except Exception as e:
        print(f"  !! Trakt /related failed for {slug_or_id}: {e}")
        return []


async def main():
    client_id = os.getenv("TRAKT_CLIENT_ID")
    if not client_id:
        print("ERROR: TRAKT_CLIENT_ID not set in environment.")
        print("Get one at https://trakt.tv/oauth/applications and add to .env:")
        print("  TRAKT_CLIENT_ID=your_client_id_here")
        return

    print("=" * 100)
    print("Trakt /related — Signal C alternative source comparison")
    print("=" * 100)

    headers = trakt_headers(client_id)
    qd = QdrantService()

    pass_counts = defaultdict(int)
    seed_count = 0
    in_catalog_count = 0
    total_recs_count = 0

    async with AsyncSessionLocal() as db, httpx.AsyncClient() as http:
        seeds = []
        for category, title in SEEDS:
            r = await db.execute(
                select(Movie).where(
                    or_(
                        func.lower(Movie.title) == title.lower(),
                        func.lower(Movie.original_title) == title.lower(),
                    )
                ).limit(1)
            )
            m = r.scalars().first()
            if not m:
                print(f"  !! Seed not in DB: {title}")
                continue
            seeds.append((category, m))

        for category, seed in seeds:
            seed_count += 1
            seed_vec_raw = await qd.get_vector(seed.tmdb_id)
            seed_vec = np.array(seed_vec_raw) if seed_vec_raw else None
            seed_genres = set(seed.genres or [])

            # Resolve TMDB → Trakt
            trakt_entry = await trakt_lookup_by_tmdb(http, seed.tmdb_id, headers)
            if not trakt_entry or "movie" not in trakt_entry:
                print(f"\n[{category}] {seed.title} — not found on Trakt.")
                continue
            trakt_slug = trakt_entry["movie"]["ids"].get("slug") or trakt_entry["movie"]["ids"].get("trakt")
            related = await trakt_related(http, trakt_slug, headers)
            if not related:
                print(f"\n[{category}] {seed.title} — Trakt returned no related films.")
                continue

            print(f"\n{'-' * 100}")
            print(f"[{category}] SEED: {seed.title} ({seed.year}) | genres={sorted(seed_genres)}")
            print(f"{'-' * 100}")

            # Resolve related films back to our DB by TMDB ID
            rec_tmdb_ids = [r["ids"].get("tmdb") for r in related if r.get("ids", {}).get("tmdb")]
            db_rows = (await db.execute(select(Movie).where(Movie.tmdb_id.in_(rec_tmdb_ids)))).scalars().all() if rec_tmdb_ids else []
            db_by_tmdb = {m.tmdb_id: m for m in db_rows}
            cand_vecs = await qd.get_vectors_batch(rec_tmdb_ids) if rec_tmdb_ids else {}

            for r in related[:10]:
                total_recs_count += 1
                tmdb_id = r["ids"].get("tmdb")
                title = r.get("title", "?")
                year = r.get("year", "?")
                in_db = tmdb_id in db_by_tmdb
                if in_db:
                    in_catalog_count += 1

                cand_vec_raw = cand_vecs.get(tmdb_id)
                cand_vec = np.array(cand_vec_raw) if cand_vec_raw else None
                cos = cosine(seed_vec, cand_vec) if (seed_vec is not None and cand_vec is not None) else None

                cand_genres = set(db_by_tmdb[tmdb_id].genres or []) if in_db else set()
                go = "✓" if (seed_genres and cand_genres and (seed_genres & cand_genres)) else "✗"

                # Strategy passes (same labels as experiment_signal_c)
                cos_v = cos or 0.0
                go_b = bool(seed_genres and cand_genres and (seed_genres & cand_genres))
                marks = []
                marks.append("✓")  # raw
                marks.append("✓" if cos_v >= 0.45 else "·")
                marks.append("✓" if cos_v >= 0.50 else "·")
                marks.append("✓" if go_b else "·")
                marks.append("✓" if (cos_v >= 0.45 and go_b) else "·")
                marks.append("✓" if (cos_v >= 0.40 and go_b) else "·")

                if marks[0] == "✓": pass_counts["raw"] += 1
                if marks[1] == "✓": pass_counts["v45"] += 1
                if marks[2] == "✓": pass_counts["v50"] += 1
                if marks[3] == "✓": pass_counts["genre"] += 1
                if marks[4] == "✓": pass_counts["v45_genre"] += 1
                if marks[5] == "✓": pass_counts["v40_genre"] += 1

                cos_str = f"{cos:.3f}" if cos is not None else " —  "
                in_db_str = "✓" if in_db else " "
                title_yr = f"{title} ({year})"[:50]
                cand_g_str = sorted(cand_genres)[:3] if cand_genres else "(not in DB)"
                print(f"    cos={cos_str}  genres={go}  in_db={in_db_str}  [{''.join(marks)}]  {title_yr:<52} {cand_g_str}")

        print(f"\n{'=' * 100}")
        print("Aggregate stats")
        print(f"{'=' * 100}")
        max_total = seed_count * 10
        print(f"  Total Trakt /related candidates: {total_recs_count}")
        print(f"  In our catalogue (have a vector): {in_catalog_count} ({100*in_catalog_count/max(1,total_recs_count):.1f}%)")
        print()
        print(f"  Raw (no filter):       {pass_counts['raw']:3d}/{max_total} ({100*pass_counts['raw']/max(1,max_total):4.1f}%)")
        print(f"  vec_sim ≥ 0.45:        {pass_counts['v45']:3d}/{max_total} ({100*pass_counts['v45']/max(1,max_total):4.1f}%)")
        print(f"  vec_sim ≥ 0.50:        {pass_counts['v50']:3d}/{max_total} ({100*pass_counts['v50']/max(1,max_total):4.1f}%)")
        print(f"  Genre overlap:         {pass_counts['genre']:3d}/{max_total} ({100*pass_counts['genre']/max(1,max_total):4.1f}%)")
        print(f"  vec≥0.45 + genres:     {pass_counts['v45_genre']:3d}/{max_total} ({100*pass_counts['v45_genre']/max(1,max_total):4.1f}%)")
        print(f"  vec≥0.40 + genres:     {pass_counts['v40_genre']:3d}/{max_total} ({100*pass_counts['v40_genre']/max(1,max_total):4.1f}%)")


if __name__ == "__main__":
    asyncio.run(main())
