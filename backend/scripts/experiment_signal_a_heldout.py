"""Held-out relevance validation for Signal A strategies.

Settles the question raised after experiment_signal_a.py: is the higher
Intra-List Diversity (ILD) of Strategy G2 a real signal of better
recommendations, or is it just synthetic diversity? ILD doesn't measure
relevance — it just measures vector spread.

Method:
  1. Get the user's high-rated films (rating ≥ 4.5).
  2. Random split 50/50 (seeded). Half becomes the "anchor pool" (used
     to construct the taste vector); the other half is "held-out" (films
     we know the user loved but we'll pretend we didn't).
  3. For each strategy, compute the taste vector from anchor pool only,
     query Qdrant top-K, exclude the user's watched list AND the anchor
     pool (held-out films stay available to be "found").
  4. Count how many of the held-out films appear in the top-K. Higher
     means the strategy is better at recovering films the user actually
     loved — a real proxy for relevance.

Strategies compared (subset of experiment_signal_a.py):
  A — Global centroid of anchor-pool vectors (current production).
  G2 — Multi-anchor consensus: pick top N=5 anchors, Qdrant-search each,
       keep only films voted by ≥2 anchors, sort by combined RRF score.

If G2 wins on this metric, its diversity gain in experiment_signal_a is
genuine (broader cluster representation finds more held-out hits). If A
wins, G2's diversity is hurting recall.

Usage:
    docker compose exec backend python scripts/experiment_signal_a_heldout.py
"""
from __future__ import annotations

import asyncio
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from sqlalchemy import select
from qdrant_client.models import SearchParams

from config import AsyncSessionLocal
from models.database import Movie, UserRating
from services.qdrant_service import QdrantService


USERS = [210, 212, 236, 237, 238, 239, 240, 241]
TOP_K = 40              # recall window
N_ANCHORS_G2 = 5        # top-N anchors used by G2
PER_ANCHOR_K = 50       # Qdrant top-K per anchor in G2
MIN_HIGH = 8            # need at least 8 high-rated films to split
SEED = 42


async def _get_high_rated_with_vectors(user_id: int, qd: QdrantService) -> list[tuple[int, list[float]]]:
    """Return [(tmdb_id, vector)] for user's films rated >= 4.5 that have a stored vector."""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(UserRating.rating, Movie.tmdb_id)
            .join(Movie, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(UserRating.rating >= 4.5)
            .where(Movie.tmdb_id.isnot(None))
        )).all()
    tmdb_ids = [r.tmdb_id for r in rows]
    if not tmdb_ids:
        return []
    vec_map = await qd.get_vectors_batch(tmdb_ids)
    return [(tid, vec_map[tid]) for tid in tmdb_ids if tid in vec_map]


async def _all_watched_tmdb_ids(user_id: int) -> set[int]:
    """Films we must exclude from results (the user has interacted with them)."""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Movie.tmdb_id)
            .join(UserRating, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(Movie.tmdb_id.isnot(None))
        )).all()
    return {r.tmdb_id for r in rows}


async def _strategy_a_topk(qd: QdrantService, anchor_vectors: list[list[float]],
                           exclude: set[int], top_k: int) -> list[int]:
    """Global centroid: mean of anchor vectors, L2-normalized, one Qdrant query."""
    if not anchor_vectors:
        return []
    centroid = np.mean(np.array(anchor_vectors), axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm > 0:
        centroid = centroid / norm
    res = await qd.client.query_points(
        collection_name="movies",
        query=centroid.tolist(),
        limit=top_k + len(exclude),  # over-fetch so we have enough after exclusion
        search_params=SearchParams(hnsw_ef=128),
    )
    out = []
    for p in res.points:
        tid = int(p.id)
        if tid in exclude:
            continue
        out.append(tid)
        if len(out) >= top_k:
            break
    return out


async def _strategy_g2_topk(qd: QdrantService, anchor_vectors: list[list[float]],
                            exclude: set[int], top_k: int) -> list[int]:
    """Multi-anchor consensus: each anchor runs its own Qdrant query, RRF-merge,
    require >= 2 anchors voting before a film qualifies."""
    if len(anchor_vectors) < 2:
        return []
    # Pick top-N anchors. Simplest: just take the first N (split is random already).
    anchors = anchor_vectors[:N_ANCHORS_G2]

    # Per-anchor Qdrant query + RRF accumulation
    rrf_scores: dict[int, float] = {}
    votes: dict[int, int] = {}
    rrf_k = 60  # standard RRF constant
    for vec in anchors:
        res = await qd.client.query_points(
            collection_name="movies",
            query=vec,
            limit=PER_ANCHOR_K + len(exclude),
            search_params=SearchParams(hnsw_ef=128),
        )
        rank = 0
        for p in res.points:
            tid = int(p.id)
            if tid in exclude:
                continue
            rank += 1
            rrf_scores[tid] = rrf_scores.get(tid, 0.0) + 1.0 / (rrf_k + rank)
            votes[tid] = votes.get(tid, 0) + 1
            if rank >= PER_ANCHOR_K:
                break

    # Consensus filter: keep only films voted by >= 2 anchors
    consensus = [tid for tid, n in votes.items() if n >= 2]
    consensus.sort(key=lambda t: rrf_scores[t], reverse=True)
    return consensus[:top_k]


async def evaluate_user(user_id: int, qd: QdrantService) -> dict:
    high_rated = await _get_high_rated_with_vectors(user_id, qd)
    if len(high_rated) < MIN_HIGH:
        return {"user_id": user_id, "skipped": True, "reason": f"only {len(high_rated)} high-rated with vectors"}

    rng = random.Random(SEED + user_id)
    shuffled = list(high_rated)
    rng.shuffle(shuffled)
    half = len(shuffled) // 2
    anchor_pool = shuffled[:half]
    held_out = {tid for tid, _ in shuffled[half:]}
    anchor_vectors = [v for _, v in anchor_pool]

    all_watched = await _all_watched_tmdb_ids(user_id)
    # Exclude: every watched film MINUS the held-out (we want held-out to be available).
    exclude = all_watched - held_out

    a_top = await _strategy_a_topk(qd, anchor_vectors, exclude, TOP_K)
    g2_top = await _strategy_g2_topk(qd, anchor_vectors, exclude, TOP_K)

    a_hits = sum(1 for t in a_top if t in held_out)
    g2_hits = sum(1 for t in g2_top if t in held_out)

    return {
        "user_id": user_id,
        "skipped": False,
        "n_high": len(high_rated),
        "n_anchor": len(anchor_pool),
        "n_heldout": len(held_out),
        "A_hits": a_hits,
        "A_recall": a_hits / len(held_out),
        "A_returned": len(a_top),
        "G2_hits": g2_hits,
        "G2_recall": g2_hits / len(held_out),
        "G2_returned": len(g2_top),
    }


async def main():
    qd = QdrantService()
    print(f"Held-out relevance test: top-{TOP_K}, N_anchors_G2={N_ANCHORS_G2}, seed={SEED}\n")
    print(f"{'user':>5s}  {'n_hi':>5s}  {'split':>10s}  "
          f"{'A_hits':>7s}  {'A_recall':>9s}  "
          f"{'G2_hits':>8s}  {'G2_recall':>10s}  {'winner':>7s}")
    print("-" * 90)
    totals = {"A_hits": 0, "G2_hits": 0, "A_returned": 0, "G2_returned": 0, "heldout": 0, "n_users": 0}
    for uid in USERS:
        r = await evaluate_user(uid, qd)
        if r["skipped"]:
            print(f"{uid:>5d}  [skip] {r['reason']}")
            continue
        winner = "G2" if r["G2_hits"] > r["A_hits"] else ("A" if r["A_hits"] > r["G2_hits"] else "tie")
        print(f"{uid:>5d}  {r['n_high']:>5d}  {r['n_anchor']:>3d}/{r['n_heldout']:>4d}  "
              f"{r['A_hits']:>7d}  {r['A_recall']:>9.3f}  "
              f"{r['G2_hits']:>8d}  {r['G2_recall']:>10.3f}  {winner:>7s}")
        totals["A_hits"] += r["A_hits"]
        totals["G2_hits"] += r["G2_hits"]
        totals["heldout"] += r["n_heldout"]
        totals["A_returned"] += r["A_returned"]
        totals["G2_returned"] += r["G2_returned"]
        totals["n_users"] += 1

    print("-" * 90)
    if totals["heldout"] > 0:
        print(f"\nAggregate ({totals['n_users']} users, {totals['heldout']} held-out films total):")
        print(f"  A:  hits={totals['A_hits']}/{totals['heldout']} = {totals['A_hits']/totals['heldout']*100:.1f}% recall@{TOP_K}")
        print(f"  G2: hits={totals['G2_hits']}/{totals['heldout']} = {totals['G2_hits']/totals['heldout']*100:.1f}% recall@{TOP_K}")
        delta_pct = (totals["G2_hits"] - totals["A_hits"]) / max(1, totals["A_hits"]) * 100
        print(f"  Δ:  G2 vs A = {delta_pct:+.1f}%")


# Guarded 2026-08-06: sin esto, IMPORTAR este módulo ejecutaba el experimento entero
# y dejaba el pool de asyncpg atado a un event loop ya cerrado ("attached to a different
# loop") para quien importase sus funciones. El comportamiento al ejecutarlo directo es
# idéntico. Ver experiment_signal_a_heldout_anchored.py, que reutiliza estas estrategias.
if __name__ == "__main__":
    asyncio.run(main())
