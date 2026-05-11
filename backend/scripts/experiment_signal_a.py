"""Experiment: Signal A centroid strategies for "Picked For You".

Compares six strategies for generating the Vibe query vector:

  A — Global centroid: mean of ALL rated/liked film vectors (current behaviour).
  B — Medoid mean: mean of the cluster medoid vectors (one per cluster).
  C — Per-cluster (equal): Qdrant search per cluster, equal round-robin.
  D — Per-cluster (weighted): take_i ∝ log(size)*.3 + (avg_rating-3)*.5 + recency*.2.
  E — Quality-weighted global: weight per film = (rating-2.5)/2.5 + liked*0.5,
      decayed by recency (1-year half-life). One Qdrant query, no clusters.
  F — Top-quality slice: centroid of films with rating ≥ 4.5 OR is_liked.
  G — Multi-anchor RRF: pick top-N anchors, search Qdrant for each, merge with RRF.
      Avoids the "geometric mean of incompatible tastes" problem by keeping
      each anchor's neighbourhood independent (similar to BYW × N).

For each strategy prints the top-15 candidates with vec_sim, VBS, genres, and
the cluster it would belong to (cosine nearest among medoids).
Also prints intra-list diversity (mean pairwise cosine distance) as the key
signal — higher = more diverse results.

Usage:
    docker compose exec backend python scripts/experiment_signal_a.py --user 212 --limit 15
"""

import asyncio
import argparse
import sys
from itertools import islice

import numpy as np
from sqlalchemy import select, or_, text

sys.path.insert(0, "/app")

from config import AsyncSessionLocal
from models.database import UserRating, Movie, UserCluster
from services.qdrant_service import QdrantService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def intra_list_diversity(vecs: list[np.ndarray]) -> float:
    """Mean pairwise cosine *distance* (1 - similarity). Higher = more diverse."""
    if len(vecs) < 2:
        return 0.0
    sims = []
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            sims.append(cosine(vecs[i], vecs[j]))
    return 1.0 - float(np.mean(sims))


def assign_cluster(vec: np.ndarray, medoid_vecs: dict[int, np.ndarray]) -> int | None:
    """Return the cluster_id whose medoid is closest to vec."""
    best, best_cid = -2.0, None
    for cid, mv in medoid_vecs.items():
        s = cosine(vec, mv)
        if s > best:
            best, best_cid = s, cid
    return best_cid


def print_results(strategy: str, rows: list[dict], medoid_vecs: dict[int, np.ndarray]):
    print(f"\n{'='*78}")
    print(f"  Strategy {strategy}")
    print(f"{'='*78}")
    result_vecs = []
    cluster_dist = {}
    for i, r in enumerate(rows, 1):
        vec = r.get("vector")
        if vec is not None and len(vec) > 0:
            arr = np.array(vec)
            assigned_cid = assign_cluster(arr, medoid_vecs) if medoid_vecs else None
            result_vecs.append(arr)
        else:
            assigned_cid = None
        src = r.get("source_cluster")
        cid_label = f"src={src} near={assigned_cid}" if src is not None else f"near={assigned_cid}"
        cluster_dist[assigned_cid] = cluster_dist.get(assigned_cid, 0) + 1
        genres = ", ".join((r.get("genres") or [])[:3]) or "—"
        print(
            f"  {i:2}. [{r['vec_sim']:.3f}] vbs={(r['vbs'] if r['vbs'] is not None else '—'):>5}  "
            f"{cid_label:<18} {r['title'][:38]:<39} ({r['year'] or '?'})  {genres}"
        )
    ild = intra_list_diversity(result_vecs)
    print(f"\n  Intra-list diversity (↑ better): {ild:.4f}   n_vecs={len(result_vecs)}")
    print(f"  Nearest-medoid distribution: {dict(sorted(cluster_dist.items(), key=lambda x: -x[1]))}")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def get_rated_vectors(db, qdrant: QdrantService, user_id: int):
    """Returns (ratings_rows, vectors_map{tmdb_id: np.array})."""
    result = await db.execute(
        select(UserRating, Movie)
        .join(Movie, UserRating.movie_id == Movie.id)
        .where(UserRating.user_id == user_id)
        .where(or_(UserRating.rating.isnot(None), UserRating.is_liked.is_(True)))
    )
    rows = result.all()
    tmdb_ids = [m.tmdb_id for _, m in rows]
    raw = await qdrant.get_vectors_batch(tmdb_ids)
    return rows, {tid: np.array(v) for tid, v in raw.items()}


def _recency_decay(ref_date, half_life_days: float = 365.0) -> float:
    """Returns multiplier in (0, 1]. Films added recently → ~1, old → ~0."""
    from datetime import datetime, timezone
    if ref_date is None:
        return 0.5
    if ref_date.tzinfo is None:
        ref_date = ref_date.replace(tzinfo=timezone.utc)
    days = max(0.0, (datetime.now(timezone.utc) - ref_date).total_seconds() / 86400.0)
    return 0.5 ** (days / half_life_days)


async def get_clusters(db, user_id: int):
    result = await db.execute(
        select(UserCluster).where(UserCluster.user_id == user_id)
    )
    return result.scalars().all()


async def get_watched_tmdb_ids(db, user_id: int) -> set[int]:
    result = await db.execute(
        select(Movie.tmdb_id)
        .join(UserRating, UserRating.movie_id == Movie.id)
        .where(UserRating.user_id == user_id)
        .where(UserRating.is_watched.is_(True))
    )
    return set(result.scalars().all())


async def enrich_hits(db, qdrant: QdrantService, hits: list[dict], watched: set[int], limit: int) -> list[dict]:
    """Filter watched, fetch Movie rows + vectors, attach metadata."""
    tmdb_ids = [h["movie_id"] for h in hits if h["movie_id"] not in watched][:limit * 3]
    if not tmdb_ids:
        return []
    result = await db.execute(
        select(Movie).where(Movie.tmdb_id.in_(tmdb_ids))
    )
    movies_map = {m.tmdb_id: m for m in result.scalars().all()}
    # Fetch vectors for ILD computation — Qdrant search_similar doesn't return them
    vecs_map = await qdrant.get_vectors_batch(tmdb_ids)
    out = []
    for h in hits:
        if h["movie_id"] in watched:
            continue
        m = movies_map.get(h["movie_id"])
        if not m:
            continue
        out.append({
            "tmdb_id": m.tmdb_id,
            "title": m.title,
            "year": m.year,
            "genres": m.genres,
            "vbs": round(m.vectorbox_score, 1) if m.vectorbox_score else None,
            "vec_sim": round(h["score"], 4),
            "vector": vecs_map.get(m.tmdb_id, []),
        })
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------

async def strategy_a_global_centroid(db, qdrant: QdrantService, user_id: int, limit: int, watched: set) -> list[dict]:
    """Current behaviour: mean of all rated film vectors."""
    _, vec_map = await get_rated_vectors(db, qdrant, user_id)
    if not vec_map:
        return []
    global_center = np.mean(list(vec_map.values()), axis=0).tolist()
    hits = await qdrant.search_similar(query_vector=global_center, limit=limit * 5, score_threshold=0.10, filters={"min_vote_count": 500})
    return await enrich_hits(db, qdrant, hits, watched, limit)


async def strategy_b_medoid_mean(db, qdrant: QdrantService, user_id: int, limit: int, watched: set) -> list[dict]:
    """Mean of cluster medoid vectors (one per cluster)."""
    clusters = await get_clusters(db, user_id)
    if not clusters:
        return await strategy_a_global_centroid(db, qdrant, user_id, limit, watched)

    medoid_ids = [c.medoid_movie_id for c in clusters if c.medoid_movie_id]
    if not medoid_ids:
        return await strategy_a_global_centroid(db, qdrant, user_id, limit, watched)

    result = await db.execute(select(Movie.tmdb_id).where(Movie.id.in_(medoid_ids)))
    tmdb_ids = result.scalars().all()
    raw = await qdrant.get_vectors_batch(list(tmdb_ids))
    if not raw:
        return await strategy_a_global_centroid(db, qdrant, user_id, limit, watched)

    medoid_center = np.mean(list(raw.values()), axis=0).tolist()
    hits = await qdrant.search_similar(query_vector=medoid_center, limit=limit * 5, score_threshold=0.10, filters={"min_vote_count": 500})
    return await enrich_hits(db, qdrant, hits, watched, limit)


async def strategy_c_per_cluster(db, qdrant: QdrantService, user_id: int, limit: int, watched: set) -> list[dict]:
    """Separate Qdrant search per cluster, round-robin interleave."""
    clusters = await get_clusters(db, user_id)
    if not clusters:
        return await strategy_a_global_centroid(db, qdrant, user_id, limit, watched)

    per_cluster_hits: list[list[dict]] = []
    for c in clusters:
        if not c.medoid_movie_id:
            continue
        result = await db.execute(select(Movie.tmdb_id).where(Movie.id == c.medoid_movie_id))
        tmdb_id = result.scalar_one_or_none()
        if not tmdb_id:
            continue
        vec = await qdrant.get_vector(tmdb_id)
        if not vec:
            continue
        hits = await qdrant.search_similar(
            query_vector=vec if isinstance(vec, list) else vec.tolist(),
            limit=(limit // len(clusters) + 3) * 5,
            score_threshold=0.15,
            filters={"min_vote_count": 500},
        )
        enriched = await enrich_hits(db, qdrant, hits, watched, limit)
        per_cluster_hits.append((c.cluster_id, enriched))

    if not per_cluster_hits:
        return await strategy_a_global_centroid(db, qdrant, user_id, limit, watched)

    # Round-robin interleave — tag each result with its source cluster id
    seen = set()
    merged = []
    iterators = [(cid, iter(items)) for cid, items in per_cluster_hits]
    while len(merged) < limit and iterators:
        still_active = []
        for cid, it in iterators:
            item = next(it, None)
            if item is None:
                continue
            still_active.append((cid, it))
            if item["tmdb_id"] not in seen:
                seen.add(item["tmdb_id"])
                item = {**item, "source_cluster": cid}
                merged.append(item)
                if len(merged) >= limit:
                    break
        iterators = still_active
        if not iterators:
            break

    return merged[:limit]


async def strategy_d_weighted_per_cluster(db, qdrant: QdrantService, user_id: int, limit: int, watched: set) -> list[dict]:
    """Per-cluster search with takes proportional to a composite weight.

    cluster_weight = log(1+size)*0.3 + max(0, avg_rating-3)*0.5 + recency*0.2
    Recency = mean exp-decay over the films' created_at for that cluster
              (half-life 180d) — captures how 'currently active' that taste is.
    """
    from datetime import timedelta

    clusters = await get_clusters(db, user_id)
    if not clusters:
        return await strategy_a_global_centroid(db, qdrant, user_id, limit, watched)

    # Compute per-cluster recency from films in each cluster.
    # sample_movie_ids is too few to be representative; we look at every rating
    # that has been assigned to this cluster via UserCluster.sample_movie_ids
    # AS a proxy. (Real cluster_id-per-rating isn't stored; medoid is the anchor.)
    weights = []
    for c in clusters:
        size_w = np.log1p(c.movie_count or 0) * 0.3
        rating_w = max(0.0, (c.avg_rating or 0) - 3.0) * 0.5
        # Recency proxy: use sample_movie_ids' created_at (best we have).
        if c.sample_movie_ids:
            recency_rows = (await db.execute(
                select(UserRating.created_at)
                .where(UserRating.user_id == user_id)
                .where(UserRating.movie_id.in_(c.sample_movie_ids))
            )).all()
            decays = [_recency_decay(r.created_at, half_life_days=180.0) for r in recency_rows]
            recency_w = (float(np.mean(decays)) if decays else 0.5) * 0.2
        else:
            recency_w = 0.1
        total = size_w + rating_w + recency_w
        weights.append((c, size_w, rating_w, recency_w, total))

    total_sum = sum(w[4] for w in weights) or 1.0
    print("\n  [D weights]")
    for c, sw, rw, rcw, tot in weights:
        share = tot / total_sum
        print(f"    cl{c.cluster_id} ({c.cluster_label[:30]:<30}) size={sw:.2f} rating={rw:.2f} recency={rcw:.2f}  → {share:.1%}")

    # Compute integer takes — at least 1 per cluster with > 0 weight to avoid drop-outs
    takes = []
    for c, _, _, _, tot in weights:
        raw_take = limit * (tot / total_sum)
        takes.append((c, max(1, round(raw_take))))
    # Re-normalize if sum exceeds limit
    while sum(t for _, t in takes) > limit:
        # Shave from the cluster with smallest weight
        idx = min(range(len(takes)), key=lambda i: weights[i][4])
        if takes[idx][1] > 1:
            takes[idx] = (takes[idx][0], takes[idx][1] - 1)
        else:
            break

    merged = []
    seen = set()
    for c, take in takes:
        if not c.medoid_movie_id or take <= 0:
            continue
        result = await db.execute(select(Movie.tmdb_id).where(Movie.id == c.medoid_movie_id))
        tmdb_id = result.scalar_one_or_none()
        if not tmdb_id:
            continue
        vec = await qdrant.get_vector(tmdb_id)
        if not vec:
            continue
        hits = await qdrant.search_similar(
            query_vector=vec if isinstance(vec, list) else vec.tolist(),
            limit=take * 5,
            score_threshold=0.15,
            filters={"min_vote_count": 500},
        )
        enriched = await enrich_hits(db, qdrant, hits, watched, take)
        for item in enriched:
            if item["tmdb_id"] not in seen and len(merged) < limit:
                seen.add(item["tmdb_id"])
                merged.append({**item, "source_cluster": c.cluster_id})

    return merged[:limit]


async def strategy_e_quality_weighted_global(db, qdrant: QdrantService, user_id: int, limit: int, watched: set) -> list[dict]:
    """Single global centroid, but vectors weighted by rating + liked + recency.

    weight(film) = (max(0, rating-2.5)/2.5 + (0.5 if liked else 0)) * recency_decay(365d)
    """
    rows, vec_map = await get_rated_vectors(db, qdrant, user_id)
    if not vec_map:
        return []

    weighted_vectors = []
    weights = []
    for ur, m in rows:
        v = vec_map.get(m.tmdb_id)
        if v is None:
            continue
        rating_part = max(0.0, ((ur.rating or 0) - 2.5) / 2.5) if ur.rating else 0.0
        liked_part = 0.5 if ur.is_liked else 0.0
        base = rating_part + liked_part
        if base <= 0:
            continue
        ref = ur.created_at or ur.watched_date
        w = base * _recency_decay(ref, half_life_days=365.0)
        if w < 0.02:
            continue
        weighted_vectors.append(v * w)
        weights.append(w)

    if not weighted_vectors:
        return await strategy_a_global_centroid(db, qdrant, user_id, limit, watched)

    centroid = (np.sum(np.stack(weighted_vectors), axis=0) / float(sum(weights))).tolist()
    hits = await qdrant.search_similar(query_vector=centroid, limit=limit * 5, score_threshold=0.10, filters={"min_vote_count": 500})
    return await enrich_hits(db, qdrant, hits, watched, limit)


async def strategy_f_top_quality_slice(db, qdrant: QdrantService, user_id: int, limit: int, watched: set, threshold: float = 4.5) -> list[dict]:
    """Centroid of films with rating ≥ threshold OR is_liked. Filters noise upfront."""
    rows, vec_map = await get_rated_vectors(db, qdrant, user_id)
    if not vec_map:
        return []
    top_vectors = [
        vec_map[m.tmdb_id]
        for ur, m in rows
        if m.tmdb_id in vec_map and ((ur.rating or 0) >= threshold or ur.is_liked)
    ]
    if not top_vectors:
        return await strategy_a_global_centroid(db, qdrant, user_id, limit, watched)
    centroid = np.mean(top_vectors, axis=0).tolist()
    hits = await qdrant.search_similar(query_vector=centroid, limit=limit * 5, score_threshold=0.10, filters={"min_vote_count": 500})
    out = await enrich_hits(db, qdrant, hits, watched, limit)
    print(f"\n  [F threshold={threshold}] centroid built from {len(top_vectors)} top-quality films")
    return out


async def strategy_g_multi_anchor(
    db,
    qdrant: QdrantService,
    user_id: int,
    limit: int,
    watched: set,
    n_anchors: int = 5,
    per_anchor_limit: int = 12,
) -> list[dict]:
    """Pick top-N anchor films, run Qdrant search for each, merge with RRF.

    Anchor quality score:
        base = (rating - 2.5) / 2.5 + (0.5 if liked) + log1p(watch_count - 1) * 0.3
        score = base * recency_decay(created_at, half_life=540d)
    Each anchor produces a ranked list; merge via Reciprocal Rank Fusion.
    """
    result = await db.execute(
        select(UserRating, Movie)
        .join(Movie, UserRating.movie_id == Movie.id)
        .where(UserRating.user_id == user_id)
        .where(or_(UserRating.rating >= 4.0, UserRating.is_liked.is_(True)))
    )
    candidates = result.all()
    if not candidates:
        return []

    # Score each candidate as a potential anchor
    scored = []
    for ur, m in candidates:
        base = max(0.0, ((ur.rating or 0) - 2.5) / 2.5) + (0.5 if ur.is_liked else 0.0)
        base += np.log1p(max(0, (ur.watch_count or 1) - 1)) * 0.3
        ref = ur.created_at or ur.watched_date
        score = base * _recency_decay(ref, half_life_days=540.0)
        scored.append((score, m))
    scored.sort(key=lambda x: -x[0])
    anchors = scored[:n_anchors]
    print(f"\n  [G] anchors selected (top {n_anchors}):")
    for s, m in anchors:
        print(f"    score={s:.3f}  {m.title[:42]:<43} ({m.year})")

    # Fetch anchor vectors (prefer stored Qdrant vectors)
    anchor_tmdb_ids = [m.tmdb_id for _, m in anchors]
    anchor_vecs_map = await qdrant.get_vectors_batch(anchor_tmdb_ids)

    # Per-anchor ranked lists
    per_anchor_results = []
    for _, m in anchors:
        vec = anchor_vecs_map.get(m.tmdb_id)
        if not vec:
            continue
        hits = await qdrant.search_similar(
            query_vector=vec if isinstance(vec, list) else list(vec),
            limit=per_anchor_limit + 5,
            score_threshold=0.30,
            filters={"min_vote_count": 500},
        )
        # Drop the anchor itself + watched
        ranked = [h for h in hits if h["movie_id"] not in watched and h["movie_id"] != m.tmdb_id]
        per_anchor_results.append((m, ranked[:per_anchor_limit]))

    # Reciprocal Rank Fusion: score(film) = Σ 1/(k + rank_in_list_i)
    K_RRF = 60
    rrf_scores: dict[int, float] = {}
    contributors: dict[int, list[str]] = {}
    for anchor_m, ranked in per_anchor_results:
        for rank, h in enumerate(ranked):
            mid = h["movie_id"]
            rrf_scores[mid] = rrf_scores.get(mid, 0.0) + 1.0 / (K_RRF + rank)
            contributors.setdefault(mid, []).append(anchor_m.title[:20])

    # Order by RRF score and enrich
    sorted_ids = sorted(rrf_scores.keys(), key=lambda k: -rrf_scores[k])[: limit * 3]
    hits = [{"movie_id": mid, "score": rrf_scores[mid]} for mid in sorted_ids]
    enriched = await enrich_hits(db, qdrant, hits, watched, limit)
    # Tag with contributor count (how many anchors found this film)
    for item in enriched:
        item["anchor_count"] = len(contributors.get(item["tmdb_id"], []))
    return enriched


async def strategy_g2_consensus_anchors(db, qdrant: QdrantService, user_id: int, limit: int, watched: set) -> list[dict]:
    """G filtered: keep only films contributed by ≥ 2 anchors (consensus).

    Fallback if too few consensus hits: pad with G's single-anchor picks.
    """
    # Use more anchors and a wider per-anchor pool to find consensus
    g_results = await strategy_g_multi_anchor(db, qdrant, user_id, limit * 5, watched, n_anchors=7, per_anchor_limit=20)
    consensus = [r for r in g_results if r.get("anchor_count", 1) >= 2]
    print(f"\n  [G2] consensus picks (≥2 anchors): {len(consensus)}/{len(g_results)}")
    if len(consensus) >= limit:
        return consensus[:limit]
    # Pad with single-anchor picks ordered by VBS to favour quality
    fallback = [r for r in g_results if r not in consensus]
    fallback.sort(key=lambda r: -(r.get("vbs") or 0))
    return (consensus + fallback)[:limit]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(user_id: int, limit: int):
    qdrant = QdrantService()

    async with AsyncSessionLocal() as db:
        watched = await get_watched_tmdb_ids(db, user_id)
        clusters = await get_clusters(db, user_id)

        # Build medoid_vecs for cluster assignment in printout
        medoid_vecs: dict[int, np.ndarray] = {}
        for c in clusters:
            if not c.medoid_movie_id:
                continue
            result = await db.execute(select(Movie.tmdb_id).where(Movie.id == c.medoid_movie_id))
            tmdb_id = result.scalar_one_or_none()
            if tmdb_id:
                vec = await qdrant.get_vector(tmdb_id)
                if vec:
                    medoid_vecs[c.cluster_id] = np.array(vec)

        print(f"\nUser {user_id} — {len(clusters)} clusters, {len(watched)} watched films")
        for c in clusters:
            print(f"  Cluster {c.cluster_id}: {c.cluster_label or '(unlabelled)'}  ({c.movie_count} films, avg★ {c.avg_rating or '?'})")

        print(f"\nRunning strategies (top {limit} each)…")

        a = await strategy_a_global_centroid(db, qdrant, user_id, limit, watched)
        b = await strategy_b_medoid_mean(db, qdrant, user_id, limit, watched)
        c = await strategy_c_per_cluster(db, qdrant, user_id, limit, watched)
        d = await strategy_d_weighted_per_cluster(db, qdrant, user_id, limit, watched)
        e = await strategy_e_quality_weighted_global(db, qdrant, user_id, limit, watched)
        f = await strategy_f_top_quality_slice(db, qdrant, user_id, limit, watched, threshold=4.5)
        f4 = await strategy_f_top_quality_slice(db, qdrant, user_id, limit, watched, threshold=4.0)
        g = await strategy_g_multi_anchor(db, qdrant, user_id, limit, watched, n_anchors=5)
        g2 = await strategy_g2_consensus_anchors(db, qdrant, user_id, limit, watched)

    print_results("A — Global centroid (current)", a, medoid_vecs)
    print_results("B — Medoid mean", b, medoid_vecs)
    print_results("C — Per-cluster (equal)", c, medoid_vecs)
    print_results("D — Per-cluster (weighted)", d, medoid_vecs)
    print_results("E — Quality-weighted global", e, medoid_vecs)
    print_results("F — Top-quality slice (★≥4.5)", f, medoid_vecs)
    print_results("F4 — Top-quality slice (★≥4.0)", f4, medoid_vecs)
    print_results("G — Multi-anchor RRF (N=5)", g, medoid_vecs)
    print_results("G2 — Multi-anchor consensus (≥2 anchors)", g2, medoid_vecs)

    # Summary table
    def stats(rows):
        vecs = [np.array(r["vector"]) for r in rows if r.get("vector") is not None and len(r.get("vector", [])) > 0]
        vbs = [r["vbs"] for r in rows if r["vbs"] is not None]
        return intra_list_diversity(vecs), vbs

    summary = [
        ("A — Global centroid (current)",  *stats(a)),
        ("B — Medoid mean",                *stats(b)),
        ("C — Per-cluster (equal)",        *stats(c)),
        ("D — Per-cluster (weighted)",     *stats(d)),
        ("E — Quality-weighted global",    *stats(e)),
        ("F — Top-quality slice (★≥4.5)",  *stats(f)),
        ("F4 — Top-quality slice (★≥4.0)", *stats(f4)),
        ("G — Multi-anchor RRF (N=5)",     *stats(g)),
        ("G2 — Multi-anchor consensus",    *stats(g2)),
    ]

    print(f"\n{'='*78}")
    print("  Summary")
    print(f"{'='*78}")
    print(f"  {'Strategy':<35} {'ILD':>7} {'avg VBS':>8} {'n':>4}")
    print(f"  {'-'*55}")
    for name, ild, vbs in summary:
        avg_vbs = round(float(np.mean(vbs)), 1) if vbs else 0.0
        n = len(vbs)
        print(f"  {name:<35} {ild:>7.4f} {avg_vbs:>8.1f} {n:>4}")

    print(f"\n  ILD = Intra-List Diversity (↑ = more diverse results)\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", type=int, default=212)
    parser.add_argument("--limit", type=int, default=15)
    args = parser.parse_args()
    asyncio.run(main(args.user, args.limit))
