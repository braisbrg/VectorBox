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
    """Replicates anchor selection from get_because_you_watched_section."""
    from services.recommendation_engine import _score_anchor_candidate

    result = await db.execute(
        select(UserRating, Movie)
        .join(Movie, UserRating.movie_id == Movie.id)
        .where(
            UserRating.user_id == user_id,
            or_(UserRating.rating >= 3.5, UserRating.is_liked.is_(True)),
        )
        .where(
            or_(
                Movie.embedding_quality_score >= 0.25,
                Movie.embedding_quality_score.is_(None),
            )
        )
        .limit(100)
    )
    candidates = result.all()
    if not candidates:
        _print("Signal A: no eligible anchors (user has no rated/liked films).")
        return

    now = datetime.utcnow()
    scored = []
    for ur, m in candidates:
        s = _score_anchor_candidate(
            rating=ur.rating,
            watched_date=ur.watched_date or ur.created_at,
            now=now,
            watch_count=getattr(ur, "watch_count", 1) or 1,
        )
        scored.append((s, ur, m))
    scored.sort(key=lambda x: x[0], reverse=True)

    top_anchor = scored[0][2]
    anchor_vec = await qdrant.get_vector(top_anchor.tmdb_id)
    target_vec = await qdrant.get_vector(movie.tmdb_id)

    sim = None
    if anchor_vec and target_vec:
        import numpy as np

        a = np.array(anchor_vec)
        b = np.array(target_vec)
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denom > 0:
            sim = float(np.dot(a, b) / denom)

    _print(
        f"Signal A anchor: {top_anchor.title} ({top_anchor.year}) | "
        f"similarity to this movie: {sim:.3f}" if sim is not None else
        f"Signal A anchor: {top_anchor.title} ({top_anchor.year}) | similarity: n/a (missing vector)"
    )

    # Where would this film rank in Signal A candidates?
    if anchor_vec and target_vec:
        sim_results = await qdrant.search_similar(
            query_vector=anchor_vec, limit=500, score_threshold=0.25
        )
        rank = None
        for i, r in enumerate(sim_results, 1):
            if r["movie_id"] == movie.tmdb_id:
                rank = i
                break
        total = len(sim_results)
        if rank:
            _print(f"  → rank in Signal A candidates: #{rank} of {total}")
        else:
            _print(f"  → not in Signal A top-{total} (similarity below 0.25 floor)")

    in_top_15 = sim is not None and sim >= 0.7
    quality_pass = (movie.vectorbox_score or 0) >= 55
    _print(
        f"  → passes quality gate (score {movie.vectorbox_score} >= 55): "
        f"{'YES' if quality_pass else 'NO'}"
    )
    _print(
        f"  → likely in top-15 MMR output: "
        f"{'YES' if in_top_15 and quality_pass else 'NO'}"
    )


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


async def _signal_c_analysis(movie: Movie, user_id: int, db, tmdb: TMDBClient) -> None:
    # Get up to 5 high-quality seed movies for the user
    stmt = (
        select(Movie)
        .join(UserRating, Movie.id == UserRating.movie_id)
        .where(UserRating.user_id == user_id)
        .where(
            or_(
                UserRating.rating >= 4.5,
                UserRating.is_liked.is_(True),
                UserRating.watch_count > 1,
            )
        )
        .order_by(desc(UserRating.rating), desc(UserRating.watched_date))
        .limit(5)
    )
    seeds = (await db.execute(stmt)).scalars().all()
    _print("Signal C:")
    if not seeds:
        _print("  → no high-quality seeds found for user.")
        return

    found_in = []
    for seed in seeds:
        try:
            recs = await tmdb.get_movie_recommendations(seed.tmdb_id)
        except Exception as e:
            _print(f"  → TMDB recs failed for seed {seed.title}: {e}")
            continue
        for r in recs[:5]:
            if r.get("id") == movie.tmdb_id:
                found_in.append(seed.title)
                break

    if found_in:
        _print(f"  → Found in TMDB recs for: {found_in}")
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
        passes = (movie.vectorbox_score or 0) >= min_score
        _print(
            f"  → passes quality threshold (score {movie.vectorbox_score} >= {min_score}): "
            f"{'YES' if passes else 'NO'}"
        )
        _print(f"  → kept={'YES' if passes else 'NO'}")
    else:
        _print(f"  → Not found in TMDB recommendations of any user seed (checked {len(seeds)} seeds).")


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
    tmdb = TMDBClient()

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
                _print(f"=== SIGNAL ANALYSIS (user_id={args.user_id}) ===")
                await _signal_a_analysis(movie, args.user_id, db, qdrant)
                _print("")
                await _signal_auteur_analysis(movie, args.user_id, db)
                _print("")
                await _signal_c_analysis(movie, args.user_id, db, tmdb)
                _print("")
                await _print_summary(movie, args.user_id, db)
    finally:
        await tmdb.close()
        await qdrant.client.close()


if __name__ == "__main__":
    asyncio.run(main())
