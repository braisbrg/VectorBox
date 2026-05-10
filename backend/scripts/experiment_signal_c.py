"""Compare Signal C filtering strategies on a curated pool of seeds.

For each seed (niche / art-house / popular), fetches both TMDB endpoints
(/recommendations and /similar), computes vector cosine + genre overlap
against the seed, and shows what each candidate strategy would keep.

Strategies evaluated:
  A — current: raw /recommendations (no filter)
  B — vec_sim ≥ 0.45
  C — vec_sim ≥ 0.50
  D — genre overlap (≥1 genre shared)
  E — combo: vec_sim ≥ 0.45 AND genre overlap   ← strongest precision
  F — combo: vec_sim ≥ 0.40 AND genre overlap   ← more permissive
  G — /similar endpoint, raw
  H — /similar + vec_sim ≥ 0.45 + genre overlap

Bottom: aggregate pass-rate per strategy across all seeds,
plus multi-seed analysis for user 212 (for strategy "appears in N seeds").

Usage:
    docker compose exec backend python scripts/experiment_signal_c.py
"""
import asyncio
import os
import sys
from collections import Counter, defaultdict

sys.path.append(os.getcwd())

import numpy as np
from sqlalchemy import select, or_, func, desc

from config import AsyncSessionLocal
from models.database import Movie, UserRating
from services.tmdb_client import TMDBClient
from services.qdrant_service import QdrantService

# ---------------- curated seeds ----------------

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

USER_ID_FOR_MULTISEED = 212


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# ---------------- strategy predicates ----------------


def passes(strategy: str, cos: float | None, genres_overlap: bool, source: str) -> bool:
    """source ∈ {'rec','similar'}"""
    if cos is None:
        cos = 0.0
    if strategy == "A":  return source == "rec"
    if strategy == "B":  return source == "rec" and cos >= 0.45
    if strategy == "C":  return source == "rec" and cos >= 0.50
    if strategy == "D":  return source == "rec" and genres_overlap
    if strategy == "E":  return source == "rec" and cos >= 0.45 and genres_overlap
    if strategy == "F":  return source == "rec" and cos >= 0.40 and genres_overlap
    if strategy == "G":  return source == "similar"
    if strategy == "H":  return source == "similar" and cos >= 0.45 and genres_overlap
    return False


STRATEGY_LABELS = {
    "A": "raw /recommendations",
    "B": "vec_sim ≥ 0.45",
    "C": "vec_sim ≥ 0.50",
    "D": "genre overlap",
    "E": "vec_sim≥0.45 + genres",
    "F": "vec_sim≥0.40 + genres",
    "G": "raw /similar",
    "H": "/similar + vec≥0.45 + genres",
}


# ---------------- main ----------------


async def fetch_tmdb_pool(seed: Movie, tmdb: TMDBClient) -> list[dict]:
    """Returns list of {tmdb_id, title, year, genre_ids, source} for both endpoints."""
    pool = []
    try:
        recs = await tmdb.get_movie_recommendations(seed.tmdb_id) or []
    except Exception:
        recs = []
    for r in recs[:10]:
        pool.append({
            "tmdb_id": r.get("id"),
            "title": r.get("title", "?"),
            "year": (r.get("release_date") or "????")[:4],
            "genre_ids": set(r.get("genre_ids", []) or []),
            "source": "rec",
        })
    try:
        similar = await tmdb._make_request(f"/movie/{seed.tmdb_id}/similar", {})
        sim_results = similar.get("results", []) if similar else []
    except Exception:
        sim_results = []
    for r in sim_results[:10]:
        pool.append({
            "tmdb_id": r.get("id"),
            "title": r.get("title", "?"),
            "year": (r.get("release_date") or "????")[:4],
            "genre_ids": set(r.get("genre_ids", []) or []),
            "source": "similar",
        })
    return pool


async def main():
    print("=" * 100)
    print("Signal C strategy comparison")
    print("=" * 100)

    tmdb = TMDBClient()
    qd = QdrantService()

    pass_counts = defaultdict(int)
    seed_count = 0
    multiseed_appearances: Counter[tuple[int, str]] = Counter()

    async with AsyncSessionLocal() as db:
        # Resolve seeds
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
                print(f"  !! Seed not found: {title}")
                continue
            seeds.append((category, m))

        # Per-seed analysis
        for category, seed in seeds:
            seed_count += 1
            seed_vec_raw = await qd.get_vector(seed.tmdb_id)
            seed_vec = np.array(seed_vec_raw) if seed_vec_raw else None
            seed_genres = set(seed.genres or [])

            pool = await fetch_tmdb_pool(seed, tmdb)
            if not pool:
                print(f"\n[{category}] {seed.title} — TMDB returned nothing.")
                continue

            print(f"\n{'-' * 100}")
            print(f"[{category}] SEED: {seed.title} ({seed.year}) | genres={sorted(seed_genres)}")
            print(f"{'-' * 100}")

            # Annotate pool with cosine + genre overlap
            tmdb_ids = [p["tmdb_id"] for p in pool if p["tmdb_id"]]
            db_rows = (await db.execute(select(Movie).where(Movie.tmdb_id.in_(tmdb_ids)))).scalars().all()
            genres_by_id = {m.tmdb_id: set(m.genres or []) for m in db_rows}

            cand_vecs_map = await qd.get_vectors_batch(tmdb_ids) if tmdb_ids else {}
            for c in pool:
                cand_vec_raw = cand_vecs_map.get(c["tmdb_id"])
                cand_vec = np.array(cand_vec_raw) if cand_vec_raw else None
                c["cos"] = cosine(seed_vec, cand_vec) if (seed_vec is not None and cand_vec is not None) else None
                cand_genres = genres_by_id.get(c["tmdb_id"], set())
                c["genres_overlap"] = bool(cand_genres & seed_genres) if cand_genres else False
                c["cand_genres"] = sorted(cand_genres)[:3]

            # Print top 8 from /recommendations with annotations
            recs = [c for c in pool if c["source"] == "rec"][:8]
            print("  /recommendations:")
            for c in recs:
                cos_str = f"{c['cos']:.3f}" if c["cos"] is not None else " —  "
                go = "✓" if c["genres_overlap"] else "✗"
                marks = "".join(
                    "✓" if passes(s, c["cos"], c["genres_overlap"], c["source"]) else "·"
                    for s in "ABCDEFGH"
                )
                title_yr = f"{c['title']} ({c['year']})"[:48]
                print(f"    cos={cos_str}  genres={go}  [{marks}]  {title_yr:<50} {c['cand_genres']}")

            # Print top 8 from /similar with annotations
            sims = [c for c in pool if c["source"] == "similar"][:8]
            if sims:
                print("  /similar:")
                for c in sims:
                    cos_str = f"{c['cos']:.3f}" if c["cos"] is not None else " —  "
                    go = "✓" if c["genres_overlap"] else "✗"
                    marks = "".join(
                        "✓" if passes(s, c["cos"], c["genres_overlap"], c["source"]) else "·"
                        for s in "ABCDEFGH"
                    )
                    title_yr = f"{c['title']} ({c['year']})"[:48]
                    print(f"    cos={cos_str}  genres={go}  [{marks}]  {title_yr:<50} {c['cand_genres']}")

            # Tally pass-counts
            for c in pool:
                for s in "ABCDEFGH":
                    if passes(s, c["cos"], c["genres_overlap"], c["source"]):
                        pass_counts[s] += 1

        # ---- multi-seed analysis for user 212 ----
        print(f"\n{'=' * 100}")
        print(f"Multi-seed agreement for user_id={USER_ID_FOR_MULTISEED}")
        print(f"{'=' * 100}")

        u_seeds = (await db.execute(
            select(Movie)
            .join(UserRating, Movie.id == UserRating.movie_id)
            .where(UserRating.user_id == USER_ID_FOR_MULTISEED)
            .where(or_(
                UserRating.rating >= 4.5,
                UserRating.is_liked.is_(True),
                UserRating.watch_count > 1,
            ))
            .order_by(desc(UserRating.rating), desc(UserRating.watched_date))
            .limit(8)
        )).scalars().all()

        print(f"User seeds (top 8 high-quality): {[s.title for s in u_seeds]}\n")

        rec_to_sources: dict[int, set[str]] = defaultdict(set)
        rec_to_meta: dict[int, dict] = {}
        for s in u_seeds:
            try:
                recs = await tmdb.get_movie_recommendations(s.tmdb_id) or []
            except Exception:
                recs = []
            for r in recs[:10]:
                rid = r.get("id")
                if not rid:
                    continue
                rec_to_sources[rid].add(s.title)
                if rid not in rec_to_meta:
                    rec_to_meta[rid] = {"title": r.get("title", "?"), "year": (r.get("release_date") or "????")[:4]}

        # How many candidates appear in ≥2 seeds?
        multi_seed = {rid: srcs for rid, srcs in rec_to_sources.items() if len(srcs) >= 2}
        single_seed = {rid: srcs for rid, srcs in rec_to_sources.items() if len(srcs) == 1}
        print(f"Total unique TMDB-rec candidates across all seeds: {len(rec_to_sources)}")
        print(f"  Appearing in 1 seed only: {len(single_seed)}")
        print(f"  Appearing in ≥2 seeds:    {len(multi_seed)}")
        print(f"\n  Multi-seed candidates (would survive 'multi-seed agreement' filter):")
        for rid, srcs in sorted(multi_seed.items(), key=lambda x: -len(x[1]))[:15]:
            meta = rec_to_meta.get(rid, {})
            print(f"    [{len(srcs)} seeds] {meta.get('title','?')} ({meta.get('year','?')})  ← {sorted(srcs)}")

        # ---- aggregate ----
        print(f"\n{'=' * 100}")
        print("Aggregate pass-counts per strategy (across all seeds, max 20 candidates per seed)")
        print(f"{'=' * 100}")
        max_per_strategy = {
            "A": seed_count * 10,  # /recommendations top 10
            "B": seed_count * 10,
            "C": seed_count * 10,
            "D": seed_count * 10,
            "E": seed_count * 10,
            "F": seed_count * 10,
            "G": seed_count * 10,  # /similar top 10
            "H": seed_count * 10,
        }
        for s in "ABCDEFGH":
            cnt = pass_counts[s]
            mx = max_per_strategy[s]
            pct = 100 * cnt / mx if mx else 0
            print(f"  {s}: {STRATEGY_LABELS[s]:<32} {cnt:3d}/{mx} ({pct:5.1f}%)")

    await tmdb.aclose()


if __name__ == "__main__":
    asyncio.run(main())
