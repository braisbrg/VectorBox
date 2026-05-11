"""
Movie debug script — diagnostic tool to understand why a specific movie appears
(or doesn't) in recommendations. Shows DB metadata, vector neighborhood, and
signal-by-signal analysis when --user-id is provided.

Usage:
    docker compose exec backend python scripts/debug_movie.py "Spirited Away"
    docker compose exec backend python scripts/debug_movie.py --tmdb-id 129
    docker compose exec backend python scripts/debug_movie.py "Spirited Away" --user-id 212
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

# Fix paths for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
sys.path.append(backend_dir)

from sqlalchemy import select, or_, desc, func

from config import AsyncSessionLocal
from models.database import Movie, UserRating, UserCluster, User
from services.qdrant_service import QdrantService
from services.tmdb_client import TMDBClient
from services.trakt_client import get_trakt_client

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("debug_movie")


def _print(s: str = ""):
    print(s, flush=True)


async def _find_movie(db, title: str | None, tmdb_id: int | None) -> Movie | None:
    if tmdb_id is not None:
        result = await db.execute(select(Movie).where(Movie.tmdb_id == tmdb_id))
        return result.scalar_one_or_none()

    # Title lookup: exact first, then ILIKE fuzzy
    result = await db.execute(select(Movie).where(Movie.title == title))
    movie = result.scalar_one_or_none()
    if movie:
        return movie

    result = await db.execute(
        select(Movie).where(Movie.title.ilike(f"%{title}%")).order_by(desc(Movie.vote_count)).limit(5)
    )
    matches = result.scalars().all()
    if not matches:
        return None
    if len(matches) > 1:
        _print(f"Multiple matches for '{title}':")
        for m in matches:
            _print(f"  - {m.title} ({m.year}) tmdb_id={m.tmdb_id} votes={m.vote_count}")
        _print(f"Using top match: {matches[0].title} ({matches[0].year})")
    return matches[0]


async def _print_movie_header(movie: Movie, qdrant: QdrantService) -> bool:
    _print(f"=== MOVIE DEBUG: {movie.title} ({movie.year}) ===")
    _print(f"DB id: {movie.id} | tmdb_id: {movie.tmdb_id}")
    _print(
        f"vectorbox_score: {movie.vectorbox_score} | vote_count: {movie.vote_count} | vote_average: {movie.vote_average}"
    )
    has_vec = await qdrant.get_vector(movie.tmdb_id)
    in_qdrant = has_vec is not None
    eq_str = (
        f"{movie.embedding_quality_score:.3f}"
        if movie.embedding_quality_score is not None
        else "unchecked"
    )
    _print(
        f"has_enriched_embedding: {bool(movie.has_enriched_embedding)} | "
        f"embedding in Qdrant: {'YES' if in_qdrant else 'NO'} | "
        f"embedding_quality_score: {eq_str}"
    )
    _print(f"genres: {movie.genres or []}")
    _print(f"directors: {movie.directors or []}")
    _print(f"runtime: {movie.runtime} | popularity: {movie.popularity}")
    if movie.cinematic_description:
        _print(f"cinematic_description: {movie.cinematic_description[:200]}...")
    else:
        _print("cinematic_description: NOT SAVED (needs re-enrichment)")
    _print("")
    return in_qdrant


async def _print_neighborhood(movie: Movie, qdrant: QdrantService, db) -> None:
    vec = await qdrant.get_vector(movie.tmdb_id)
    if not vec:
        _print("=== VECTOR NEIGHBORHOOD ===")
        _print("No vector in Qdrant — cannot compute neighborhood.")
        _print("")
        return

    results = await qdrant.search_similar(query_vector=vec, limit=11, score_threshold=0.0)
    # First result is usually the movie itself
    neighbors = [r for r in results if r["movie_id"] != movie.tmdb_id][:10]
    if not neighbors:
        _print("=== VECTOR NEIGHBORHOOD ===")
        _print("No neighbors found.")
        _print("")
        return

    neighbor_ids = [r["movie_id"] for r in neighbors]
    db_result = await db.execute(select(Movie).where(Movie.tmdb_id.in_(neighbor_ids)))
    db_map = {m.tmdb_id: m for m in db_result.scalars().all()}

    _print("=== VECTOR NEIGHBORHOOD (top 10 closest in Qdrant) ===")
    for i, r in enumerate(neighbors, 1):
        m = db_map.get(r["movie_id"])
        if m:
            genres = "/".join((m.genres or [])[:2]) or "?"
            score = m.vectorbox_score if m.vectorbox_score is not None else "?"
            _print(f"{i:2d}. {m.title:35s} ({r['score']:.2f}) | {genres} | score={score}")
        else:
            _print(f"{i:2d}. tmdb_id={r['movie_id']} (not in DB) ({r['score']:.2f})")
    _print("")


async def _print_user_context(movie: Movie, user_id: int, db) -> set[int]:
    """Returns set of watched_internal_ids for downstream signal analysis."""
    user = await db.get(User, user_id)
    if not user:
        _print(f"=== USER CONTEXT (user_id={user_id}) ===")
        _print(f"User {user_id} not found.")
        _print("")
        return set()

    rating_result = await db.execute(
        select(UserRating).where(
            UserRating.user_id == user_id, UserRating.movie_id == movie.id
        )
    )
    user_rating = rating_result.scalar_one_or_none()

    watched_result = await db.execute(
        select(UserRating.movie_id).where(
            UserRating.user_id == user_id, UserRating.is_watched.is_(True)
        )
    )
    watched_internal_ids = set(watched_result.scalars().all())

    _print(f"=== USER CONTEXT (user_id={user_id}) ===")
    if user_rating:
        _print(
            f"UserRating: rating={user_rating.rating} | is_watched={user_rating.is_watched} | "
            f"is_liked={user_rating.is_liked} | watch_count={user_rating.watch_count} | "
            f"is_rejected={user_rating.is_rejected}"
        )
    else:
        _print("UserRating: (no rating record)")

    in_watched = movie.id in watched_internal_ids
    _print(
        f"In watched_ids: {'YES' if in_watched else 'NO'} → "
        f"{'excluded from recommendations' if in_watched else 'available for recommendation'}"
    )
    _print("")
    return watched_internal_ids


async def _signal_a_analysis(movie: Movie, user_id: int, db, qdrant: QdrantService) -> None:
    """Replicates G2 multi-anchor consensus from clustering_service.get_user_centric_recommendations.

    Signal A is no longer a single global centroid: it picks top-7 anchors by
    (rating + liked + log1p(rewatch)) * recency_decay(540d), runs per-anchor
    Qdrant search, merges with RRF, and prefers films contributed by ≥2 anchors.
    This function shows: (a) the 7 anchors, (b) which of them have this film
    in their top-20 neighbours, (c) the consensus tier (≥2) vs single-anchor.
    """
    import numpy as np

    def _recency_decay(ref, half_life_days: float = 540.0) -> float:
        if ref is None:
            return 0.5
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        days = max(0.0, (datetime.now(timezone.utc) - ref).total_seconds() / 86400.0)
        return 0.5 ** (days / half_life_days)

    result = await db.execute(
        select(UserRating, Movie)
        .join(Movie, UserRating.movie_id == Movie.id)
        .where(UserRating.user_id == user_id)
        .where(or_(UserRating.rating >= 4.0, UserRating.is_liked.is_(True)))
    )
    candidates = result.all()
    if not candidates:
        _print("Signal A (G2): no anchor candidates (★≥4.0 OR liked).")
        return

    scored = []
    for ur, m in candidates:
        base = max(0.0, ((ur.rating or 0) - 2.5) / 2.5) + (0.5 if ur.is_liked else 0.0)
        base += float(np.log1p(max(0, (ur.watch_count or 1) - 1))) * 0.3
        ref = ur.created_at or ur.watched_date
        scored.append((base * _recency_decay(ref), m))
    scored.sort(key=lambda x: -x[0])
    anchors = scored[:7]

    _print("Signal A (G2 — top 7 anchors):")
    for s, m in anchors:
        _print(f"  - {s:.3f}  {m.title} ({m.year})")

    # Check this film's appearance in each anchor's neighbourhood
    anchor_tmdb_ids = [m.tmdb_id for _, m in anchors]
    anchor_vec_map = await qdrant.get_vectors_batch(anchor_tmdb_ids)
    target_vec = await qdrant.get_vector(movie.tmdb_id)

    if not target_vec:
        _print("  → target film has no vector in Qdrant — cannot evaluate.")
        return

    contributors = []  # (anchor_title, rank_in_anchor, similarity)
    for _, anchor in anchors:
        avec = anchor_vec_map.get(anchor.tmdb_id)
        if not avec:
            continue
        hits = await qdrant.search_similar(
            query_vector=list(avec), limit=25, score_threshold=0.30,
            filters={"min_vote_count": 500},
        )
        # Drop the anchor itself, find rank of our target
        ranked = [h for h in hits if h["movie_id"] != anchor.tmdb_id][:20]
        for rank, h in enumerate(ranked):
            if h["movie_id"] == movie.tmdb_id:
                contributors.append((anchor.title, rank + 1, h["score"]))
                break

    n_contributors = len(contributors)
    if n_contributors == 0:
        _print(f"  → film NOT in any anchor's top-20 neighbours (no Signal A contribution).")
    else:
        tier = "CONSENSUS (≥2 anchors)" if n_contributors >= 2 else "SINGLE-ANCHOR"
        _print(f"  → {tier}: appears in {n_contributors}/{len(anchors)} anchors' top-20:")
        for title, rank, sim in contributors:
            _print(f"      via '{title}' at rank #{rank} (sim={sim:.3f})")

    quality_pass = (movie.vectorbox_score or 0) >= 55
    _print(f"  → passes quality gate (VBS {movie.vectorbox_score} >= 55): {'YES' if quality_pass else 'NO'}")
    surfaces = n_contributors >= 1 and quality_pass
    _print(f"  → would surface via Signal A: {'YES' if surfaces else 'NO'}")


async def _signal_auteur_analysis(movie: Movie, user_id: int, db) -> None:
    from services.recommendation_service import RecommendationService

    rs = RecommendationService(db)
    director_scores = await rs._compute_director_scores(user_id)
    if not director_scores:
        _print("Signal Auteur: no director scores (no rated/liked films).")
        return

    sorted_dirs = sorted(director_scores.items(), key=lambda x: x[1], reverse=True)
    top_3 = [name for name, _ in sorted_dirs[:3]]
    section_threshold = [name for name, sc in sorted_dirs if sc >= 2.0][:3]

    _print("Signal Auteur:")
    if not movie.directors:
        _print("  → No director metadata for this film.")
        return

    for d in movie.directors:
        score = director_scores.get(d, 0.0)
        rank = next((i for i, (n, _) in enumerate(sorted_dirs, 1) if n == d), None)
        _print(f"  → {d} score for user: {score:.2f} (rank #{rank})" if rank else f"  → {d}: not in user's history")

    in_top_3 = any(d in top_3 for d in movie.directors)
    _print(f"  → Director(s) in user top-3: {'YES' if in_top_3 else 'NO'}")

    score_pass = (movie.vectorbox_score or 0) >= 60
    votes_pass = (movie.vote_count or 0) >= 50
    year_pass = movie.year is not None
    _print(
        f"  → Passes auteur filters (score>=60, votes>=50, year set): "
        f"score={'✓' if score_pass else '✗'} "
        f"votes={'✓' if votes_pass else '✗'} "
        f"year={'✓' if year_pass else '✗'}"
    )

    in_section_directors = any(d in section_threshold for d in movie.directors)
    would_appear = score_pass and votes_pass and year_pass and in_section_directors
    _print(
        f"  → Would appear in auteur section: {'YES' if would_appear else 'NO'}"
        + (" (director below threshold 2.0)" if not in_section_directors else "")
    )


async def _signal_c_analysis(movie: Movie, user_id: int, db, qdrant: QdrantService) -> None:
    """Signal C ('Crowd') — Trakt-based collaborative filter.

    Picks up to 8 seeds (★≥4.0 OR liked) per recommendation_service constants,
    queries Trakt /movies/{slug}/related for each, applies the cross-validation
    gate (vec_sim ≥ 0.40 AND genre overlap with seed). Mirrors
    services/recommendation_service.py _compute_crowd_signal_raw.
    """
    import numpy as np

    trakt = get_trakt_client()
    _print("Signal C (Trakt — Crowd):")
    if not trakt.enabled:
        _print("  → TRAKT_CLIENT_ID not set; Signal C disabled.")
        return

    stmt = (
        select(Movie)
        .join(UserRating, Movie.id == UserRating.movie_id)
        .where(UserRating.user_id == user_id)
        .where(or_(UserRating.rating >= 4.0, UserRating.is_liked.is_(True)))
        .order_by(desc(UserRating.rating), desc(UserRating.watched_date))
        .limit(8)
    )
    seeds = (await db.execute(stmt)).scalars().all()
    if not seeds:
        _print("  → no high-quality seeds (★≥4.0 OR liked).")
        return

    target_vec = await qdrant.get_vector(movie.tmdb_id)
    target_genres = set(movie.genres or [])
    seed_vec_map = await qdrant.get_vectors_batch([s.tmdb_id for s in seeds])

    found_via = []  # (seed_title, vec_sim, genre_overlap, gate_pass)
    for seed in seeds:
        try:
            related = await trakt.related_by_tmdb(seed.tmdb_id, limit=10)
        except Exception as e:
            _print(f"  → Trakt /related failed for '{seed.title}': {e}")
            continue
        related_tmdb_ids = {
            r.get("ids", {}).get("tmdb") for r in related if r.get("ids", {}).get("tmdb")
        }
        if movie.tmdb_id not in related_tmdb_ids:
            continue

        # Cross-validation gate: vec_sim ≥ 0.40 AND genre overlap with seed
        sv = seed_vec_map.get(seed.tmdb_id)
        vec_sim = None
        if sv and target_vec:
            a, b = np.array(sv), np.array(target_vec)
            denom = float(np.linalg.norm(a) * np.linalg.norm(b))
            if denom > 0:
                vec_sim = float(np.dot(a, b) / denom)

        seed_genres = set(seed.genres or [])
        genre_overlap = bool(target_genres & seed_genres)

        gate = (vec_sim is not None and vec_sim >= 0.40) and genre_overlap
        found_via.append((seed.title, vec_sim, genre_overlap, gate))

    if not found_via:
        _print(f"  → not in Trakt /related of any of {len(seeds)} seeds.")
        return

    _print(f"  → appears in Trakt /related via {len(found_via)} seed(s):")
    any_passed = False
    for title, vec_sim, overlap, gate in found_via:
        sim_str = f"{vec_sim:.3f}" if vec_sim is not None else "n/a"
        _print(
            f"      via '{title}'  vec_sim={sim_str}  genre_overlap={overlap}  "
            f"cross-val={'PASS' if gate else 'FAIL'}"
        )
        if gate:
            any_passed = True

    # User's watched count → dynamic min_score for Signal C
    watch_count_stmt = (
        select(func.count())
        .select_from(UserRating)
        .where(UserRating.user_id == user_id, UserRating.is_watched.is_(True))
    )
    user_watch_count = (await db.execute(watch_count_stmt)).scalar() or 0
    if user_watch_count < 30:
        min_score = 60
    elif user_watch_count < 100:
        min_score = 65
    else:
        min_score = 68
    quality_pass = (movie.vectorbox_score or 0) >= min_score
    _print(
        f"  → quality gate (VBS {movie.vectorbox_score} >= {min_score} for "
        f"{user_watch_count} watched films): {'YES' if quality_pass else 'NO'}"
    )
    surfaces = any_passed and quality_pass
    _print(f"  → would surface via Signal C: {'YES' if surfaces else 'NO'}")


async def _print_cluster_membership(movie: Movie, user_id: int, db, qdrant: QdrantService) -> None:
    """Show which user cluster this film sits closest to (medoid cosine sim)."""
    import numpy as np

    clusters_result = await db.execute(
        select(UserCluster).where(UserCluster.user_id == user_id)
        .order_by(UserCluster.cluster_id)
    )
    clusters = clusters_result.scalars().all()
    if not clusters:
        _print("=== CLUSTER MEMBERSHIP ===")
        _print("User has no clusters yet (run reset_profiles.py).")
        _print("")
        return

    target_vec = await qdrant.get_vector(movie.tmdb_id)
    if not target_vec:
        _print("=== CLUSTER MEMBERSHIP ===")
        _print("Target film has no vector — cannot compute cluster proximity.")
        _print("")
        return

    medoid_ids = [c.medoid_movie_id for c in clusters if c.medoid_movie_id]
    medoid_map = {}
    if medoid_ids:
        rows = await db.execute(select(Movie).where(Movie.id.in_(medoid_ids)))
        medoid_map = {m.id: m for m in rows.scalars().all()}

    target_np = np.array(target_vec)
    t_norm = float(np.linalg.norm(target_np))

    _print("=== CLUSTER MEMBERSHIP ===")
    sims = []
    for c in clusters:
        m = medoid_map.get(c.medoid_movie_id)
        if not m:
            continue
        mv = await qdrant.get_vector(m.tmdb_id)
        if not mv:
            continue
        mv_np = np.array(mv)
        denom = t_norm * float(np.linalg.norm(mv_np))
        sim = float(np.dot(target_np, mv_np) / denom) if denom > 0 else 0.0
        sims.append((sim, c, m))

    sims.sort(key=lambda x: -x[0])
    for i, (sim, c, m) in enumerate(sims, 1):
        marker = "← closest" if i == 1 else ""
        _print(
            f"  cl{c.cluster_id} '{c.cluster_label}' (n={c.movie_count}, avg★ {c.avg_rating:.2f}) "
            f"medoid='{m.title}' sim={sim:.3f}  {marker}"
        )
    _print("")


async def _print_signal_membership(movie: Movie, user_id: int, db, qdrant: QdrantService) -> None:
    """Confirms which Trident signal(s) actually surface this movie.

    Mirrors the production paths used by Picked For You:
      - Signal A (Vibe): G2 multi-anchor consensus via
        clustering_service.get_user_centric_recommendations.
      - Signal Auteur: top directors by weighted score >= 3.0, vectorbox_score > 70.
      - Signal C (Crowd): Trakt /related films of up to 8 high-quality user seeds,
        with cross-validation gate (vec_sim ≥ 0.40 AND genre overlap).
    """
    from services.clustering_service import ClusteringService

    _print("=== SIGNAL MEMBERSHIP ===")

    # Signal A — call the actual production code path
    cs = ClusteringService(qdrant=qdrant)
    raw_recs = await cs.get_user_centric_recommendations(
        user_id, db, filters={"min_vote_count": 500}, limit=50
    )
    rank = next(
        (i for i, r in enumerate(raw_recs, 1) if r["movie_id"] == movie.tmdb_id),
        None,
    )
    if rank:
        score = next(r["score"] for r in raw_recs if r["movie_id"] == movie.tmdb_id)
        _print(f"Signal A (G2 multi-anchor): rank #{rank} of {len(raw_recs)} (rrf={score:.4f})")
    else:
        _print(f"Signal A (G2 multi-anchor): NOT in top-{len(raw_recs)} candidates.")

    # Signal Auteur
    if movie.directors:
        from services.recommendation_service import RecommendationService

        rs = RecommendationService(db)
        scores = await rs._compute_director_scores(user_id)
        sorted_dirs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_5_active = [name for name, score in sorted_dirs if score >= 3.0][:5]
        movie_dirs_active = [d for d in movie.directors if d in top_5_active]
        score_pass = (movie.vectorbox_score or 0) > 70
        if movie_dirs_active and score_pass:
            _print(f"Signal Auteur: ELIGIBLE via {movie_dirs_active} (VBS {movie.vectorbox_score} > 70)")
        elif movie_dirs_active:
            _print(f"Signal Auteur: director match {movie_dirs_active} but VBS {movie.vectorbox_score} <= 70")
        else:
            _print("Signal Auteur: no director(s) in user top-5 active (score>=3.0).")
    else:
        _print("Signal Auteur: no director metadata.")

    _print("")


async def _print_summary(movie: Movie, user_id: int, db) -> None:
    """Best-effort 'why it appears' summary based on signal eligibility."""
    _print("=== WHY IT APPEARS / DOESN'T APPEAR ===")
    paths = []

    # Auteur
    if movie.directors:
        from services.recommendation_service import RecommendationService

        rs = RecommendationService(db)
        scores = await rs._compute_director_scores(user_id)
        sorted_dirs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_3 = [n for n, _ in sorted_dirs[:3]]
        if any(d in top_3 for d in movie.directors) and (movie.vectorbox_score or 0) >= 60:
            paths.append(f"Auteur section ({', '.join(d for d in movie.directors if d in top_3)})")

    # Hidden Gems eligibility (rough)
    if (
        (movie.vectorbox_score or 0) >= 60
        and (movie.popularity or 999) <= 40
        and (movie.vote_count or 0) >= 200
    ):
        paths.append("Hidden Gems (Signal C eligibility)")

    if paths:
        _print(f"→ Eligible paths: {paths}")
    else:
        _print("→ No clear eligibility path detected. Check signal sections above for specific reasons.")
    _print("")


async def main():
    parser = argparse.ArgumentParser(description="Debug a single movie's recommendation eligibility.")
    parser.add_argument("title", nargs="?", help="Movie title (fuzzy match)")
    parser.add_argument("--tmdb-id", type=int, help="Lookup by tmdb_id")
    parser.add_argument("--user-id", type=int, help="Run user-context signal analysis")
    args = parser.parse_args()

    if not args.title and not args.tmdb_id:
        parser.error("Provide either a title or --tmdb-id")

    qdrant = QdrantService()
    trakt = get_trakt_client()

    try:
        async with AsyncSessionLocal() as db:
            movie = await _find_movie(db, args.title, args.tmdb_id)
            if not movie:
                ident = args.title or f"tmdb_id={args.tmdb_id}"
                _print(f"Movie not found: {ident}")
                return

            await _print_movie_header(movie, qdrant)
            await _print_neighborhood(movie, qdrant, db)

            if args.user_id is not None:
                await _print_user_context(movie, args.user_id, db)
                await _print_cluster_membership(movie, args.user_id, db, qdrant)
                _print(f"=== SIGNAL ANALYSIS (user_id={args.user_id}) ===")
                await _signal_a_analysis(movie, args.user_id, db, qdrant)
                _print("")
                await _signal_auteur_analysis(movie, args.user_id, db)
                _print("")
                await _signal_c_analysis(movie, args.user_id, db, qdrant)
                _print("")
                await _print_signal_membership(movie, args.user_id, db, qdrant)
                await _print_summary(movie, args.user_id, db)
    finally:
        await trakt.aclose()
        await qdrant.client.close()


if __name__ == "__main__":
    asyncio.run(main())
