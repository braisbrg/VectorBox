"""
K-Means Clustering Service for User Taste Profiles
Implements dynamic clustering: n_clusters = min(5, max(2, total_movies // 20))
"""
from sklearn.cluster import KMeans
from collections import Counter
import numpy as np
import math
from typing import List, Dict, Tuple, Optional, Set
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, desc, func
import asyncio
import time
import functools

from models.database import UserRating, Movie, UserCluster
from services.qdrant_service import QdrantService

logger = logging.getLogger(__name__)

from config import AsyncSessionLocal

async def _enrich_movie_background(tmdb_id: int):
    """Background-safe enrichment: creates its own DB session. Never re-raises."""
    from services.movie_service import MovieService
    from models.database import Movie
    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(select(Movie).where(Movie.tmdb_id == tmdb_id))
            movie = result.scalar_one_or_none()
            if movie:
                movie_service = MovieService(session)
                await movie_service.enrich_movie(movie)
                await session.commit()
        except Exception as e:
            logger.error(f"Background enrichment failed for tmdb_id={tmdb_id}: {e}")

# Signal A anchor freshness: how many of the 7 anchor slots are reserved for the
# newest anchor-eligible watches, and how far back "newest" reaches. Nothing watched
# in the window → both fall back to the all-time ranking (no regression).
RECENT_ANCHORS = 2
RECENT_ANCHOR_DAYS = 90

# The two jobs are split: the reserved slots above carry freshness, so the remaining
# five carry the taste core and want a LONG half-life. Measured by time-travelling
# the real profiles of users 210/212 (anchor set at D vs D+30, 12 windows), the
# 30-day turnover of those five slots and their mean age:
#   180d → 32% / 30d   365d → 24% / 70d   540d → 20% / 70d   1095d → 14% / 220d
# At 540d the core is already 4/5 films from the last two months — In the Mood for
# Love and Past Lives fall out of a profile they define. 1095d keeps them and still
# follows a taste that shifts over years.
ANCHOR_HALF_LIFE_DAYS = 1095.0


def select_anchors(scored, recent, n_anchors: int):
    """Anchor set for Signal A: best-of-all-time, minus RECENT_ANCHORS slots given
    to the newest anchor-eligible watches.

    Without the reserved slots the anchor set is FROZEN: `base` tops out at 1.5
    (5★ + liked) while a fresh 4.5★ scores 0.8, and the decay term reads
    `created_at` — the IMPORT date for bulk-synced history, identical for hundreds
    of rows. Measured on user 212 (939 ratings): five new watches moved nothing.
    The ≥4.0-or-liked filter upstream is the quality gate; here recency orders.
    ponytail: newest-first, not best-of-window — responsiveness is the point.

    `scored` is [(score, Movie)] sorted desc, `recent` is [(watched_date, Movie)]
    within the window. Empty `recent` → pure all-time ranking, as before.
    """
    fresh = [m for _, m in sorted(recent, key=lambda x: -x[0].timestamp())[:RECENT_ANCHORS]]
    fresh_ids = {m.tmdb_id for m in fresh}
    top = [m for _, m in scored if m.tmdb_id not in fresh_ids][: n_anchors - len(fresh)]
    return top + fresh


class ClusteringService:
    """Create and manage user taste clusters"""
    
    def __init__(self, qdrant: QdrantService = None):
        self.qdrant = qdrant or QdrantService()
    
    @staticmethod
    def calculate_optimal_clusters(
        n_movies: int,
        X: Optional[np.ndarray] = None,
        sample_weights: Optional[np.ndarray] = None,
    ) -> int:
        """
        Pick k via silhouette score over k ∈ [3, min(12, n//20)].

        Falls back to the old fixed formula `min(5, max(2, n // 20))` when:
          - X is not provided (callers that only know n_movies)
          - n_movies < 30 (silhouette is unreliable on very small samples)

        Silhouette is sub-sampled (max 500 points, seeded random) so cost stays
        ≤2-3s even for 1700-film users.
        """
        if X is None or n_movies < 30:
            n_clusters = max(2, n_movies // 20)
            return min(5, n_clusters)

        from sklearn.metrics import silhouette_score

        k_max = min(12, max(3, n_movies // 20))
        if k_max < 4:
            return k_max  # not enough headroom to compare

        best_k = 3
        best_score = -1.0
        for k in range(3, k_max + 1):
            km = KMeans(n_clusters=k, random_state=42, n_init=5, max_iter=200)
            if sample_weights is not None:
                labels = km.fit_predict(X, sample_weight=sample_weights)
            else:
                labels = km.fit_predict(X)
            # silhouette is O(n²) — subsample for speed; same seed for fair comparison
            sample_size = min(500, n_movies)
            score = float(silhouette_score(X, labels, sample_size=sample_size, random_state=42))
            if score > best_score:
                best_score = score
                best_k = k
        logger.info(f"Silhouette-optimal k={best_k} (score={best_score:.3f}, n={n_movies})")
        return best_k

    @staticmethod
    async def get_user_genre_preferences(
        user_id: int,
        db: AsyncSession,
        *,
        min_rating: float = 3.5,
    ) -> List[Tuple[str, float]]:
        """Returns [(genre, weight)] sorted desc by weight.

        Weight per film is: (rating_part + liked_bonus + log1p(watch_count-1)*0.3)
        multiplied by recency_decay (730-day half-life). The weight is then
        accumulated across every genre the film has.

        Used by the 4 feed sections that previously derived "user genres" from
        cluster.dominant_genres. Going directly to ratings is cleaner because
        clusters can mis-represent breadth (biggest cluster ≠ most loved genres
        — see scripts/experiment_feed_sections.py for empirical evidence on
        user 210 where Drama dominates by weight but Action led the biggest cluster).
        """
        result = await db.execute(
            select(UserRating, Movie)
            .join(Movie, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(or_(UserRating.rating >= min_rating, UserRating.is_liked.is_(True)))
        )
        weights: Dict[str, float] = {}
        now = datetime.now(timezone.utc)
        rows = result.all()

        def _decay(ref):
            if ref.tzinfo is None:
                ref = ref.replace(tzinfo=timezone.utc)
            return 0.5 ** (max(0.0, (now - ref).total_seconds() / 86400.0) / 730.0)

        # Neutro relativo, no un 0.5 fijo: el fijo no decae mientras todo lo demás sí, así
        # que en un perfil mixto las filas sin fecha acaban decidiendo los géneros solas.
        # Medido 2026-08-11 en user 212 (46% sin fecha tras reimportar): la cuota del peso
        # que aportan pasa de 38.7% hoy a **78.1% a los 5 años y 95.3% a los 10** con el
        # 0.5 fijo, y se queda clavada en 44.0% con la mediana. Misma corrección que en el
        # scorer de anchors y en `_score_anchor_candidate` de recommendation_engine.
        _fechados = [_decay(ur.watched_date) for ur, _ in rows if ur.watched_date is not None]
        neutro = float(np.median(_fechados)) if _fechados else 0.5

        for ur, m in rows:
            if not m.genres:
                continue
            base = max(0.0, ((ur.rating or 0) - 2.5) / 2.5) + (0.5 if ur.is_liked else 0.0)
            base += float(np.log1p(max(0, (ur.watch_count or 1) - 1))) * 0.3
            if base <= 0:
                continue
            # watched_date ONLY: `created_at` es la fecha de import.
            decay = neutro if ur.watched_date is None else _decay(ur.watched_date)
            w = base * decay
            for g in m.genres:
                weights[g] = weights.get(g, 0.0) + w
        return sorted(weights.items(), key=lambda x: -x[1])

    @staticmethod
    def _compute_medoid(vectors: np.ndarray) -> tuple[np.ndarray, int]:
        """
        Find the vector in `vectors` closest to the cluster centroid.
        Returns (medoid_vector, medoid_index).
        """
        centroid = np.mean(vectors, axis=0)
        distances = np.linalg.norm(vectors - centroid, axis=1)
        medoid_idx = int(np.argmin(distances))
        return vectors[medoid_idx], medoid_idx

    @staticmethod
    async def generate_cluster_label(
        sample_films: list[dict],
        dominant_genres: list[str],
        groq_client,
        cluster_size: Optional[int] = None,
        avg_rating: Optional[float] = None,
    ) -> str:
        """
        Generate a cinematic cluster label using Groq.

        `sample_films` is a list of dicts with `title`, `year` keys.
        Falls back to genre-based label on failure.

        The richer context (year span, cluster size, avg rating) helps the LLM
        decide whether the cluster has a coherent theme worth naming, or whether
        it's a generic bucket that should get a plain '[Adjective] [Genre]' label
        — preventing the confabulation we saw with 5-title prompts.
        """
        fallback = ", ".join(dominant_genres[:2]) if dominant_genres else "Cinema"

        if groq_client is None or not sample_films:
            return fallback

        # Build a richer prompt: up to 12 films with year, year-range, genres, stats.
        titles_with_year = [
            f"{f['title']} ({f.get('year') or '?'})" for f in sample_films[:12]
        ]
        years = [f["year"] for f in sample_films if f.get("year")]
        year_range = ""
        if years:
            y_min, y_max = min(years), max(years)
            year_range = f"{y_min}-{y_max}" if y_min != y_max else str(y_min)

        genres_str = ", ".join(dominant_genres[:5]) if dominant_genres else "Various"
        stats_line = []
        if cluster_size:
            stats_line.append(f"{cluster_size} films total")
        if year_range:
            stats_line.append(f"year span {year_range}")
        if avg_rating is not None:
            stats_line.append(f"avg★ {avg_rating:.2f}")
        stats_str = " | ".join(stats_line) if stats_line else ""

        prompt = (
            f"Films in this cluster (top by rating):\n  - "
            + "\n  - ".join(titles_with_year)
            + f"\n\nDominant genres: {genres_str}"
            + (f"\nStats: {stats_str}" if stats_str else "")
            + "\n\nName this cluster in 2-4 words (English). "
            "Examples of GOOD labels for coherent themes: "
            "'Slow Burn Noir', 'European Art House', '80s Synth Sci-Fi', "
            "'Studio Ghibli Wonder', 'Korean Revenge Cinema'. "
            "\n\nIMPORTANT: A cluster can span MANY decades and still have a "
            "coherent MOOD/STYLE (e.g. 'Quiet Character Drama', 'Visual Auteur Cinema', "
            "'Studio Ghibli Wonder'). Don't reject thematic labels just because of a "
            "wide year range — focus on whether the films share a common emotional "
            "register, visual style, or directorial sensibility. "
            "\n\nONLY if the films genuinely lack any common mood/theme/style "
            "(e.g. random blockbusters thrown together, or unrelated dramas that "
            "happen to be highly rated), return a HONEST GENERIC label like "
            "'Mixed Drama', 'Genre Crowd-Pleasers', '[Dominant Genre] Mix'. "
            "Do NOT confabulate a fake theme, but DO surface a real one when present. "
            "\n\nRespond with ONLY the label. No quotes, no explanation, no trailing punctuation."
        )

        from services.cinematic_enricher import (
            _strip_think, _get_model_chain, _REASONING_EFFORT,
            _parse_retry_after, _is_daily_limit,
        )
        # Cascade across ENRICH_CHAIN (qwen3.6-27b → gpt-oss-120b → gpt-oss-20b) —
        # each model has a SEPARATE TPM bucket, so a 429 on one tries the next
        # instead of degrading. Labels are tiny (~500 tok) but the buckets are
        # small (qwen3.6-27b ~8K TPM), so re-clustering many users back-to-back
        # used to 429 and drop straight to a genre label. If the WHOLE chain is
        # per-minute rate-limited, wait the suggested time ONCE and retry rather
        # than degrade — genres is the true last resort (chain fully exhausted /
        # daily limit). _strip_think guards <think> leaks.
        messages = [
            {
                "role": "system",
                "content": (
                    "You name film clusters honestly. Respond with ONLY a 2-4 word English label. "
                    "No punctuation at the end. If films don't share a coherent theme, use a generic label."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        async def _try_chain():
            """One cascade pass over the model chain.

            Returns (label, shortest_minute_429_wait). label is None if every
            model failed; the wait is set only when the failures were recoverable
            per-minute 429s (not daily limits / hard errors).
            """
            min_wait = None
            for model_name in _get_model_chain():
                effort = _REASONING_EFFORT.get(model_name)
                extra_body = {"reasoning_effort": effort} if effort else None
                try:
                    response = await groq_client.chat.completions.create(
                        model=model_name,
                        messages=messages,
                        temperature=0.3,
                        max_tokens=120,  # reasoning headroom even at effort='none'
                        extra_body=extra_body,
                    )
                    label = _strip_think(response.choices[0].message.content or "").strip().rstrip(".!,;:")
                    if label and 1 < len(label) < 60:
                        logger.info(f"LLM cluster label '{label}' (model={model_name})")
                        return label, None
                except Exception as e:
                    err = str(e)
                    if "429" in err and not _is_daily_limit(err):
                        w = _parse_retry_after(err)
                        if w and (min_wait is None or w < min_wait):
                            min_wait = w
            return None, min_wait

        label, wait = await _try_chain()
        if label:
            return label
        # Whole chain per-minute rate-limited → wait once (capped — this runs in a
        # background task, but don't stall a label for minutes) and retry.
        if wait and wait < 30:
            logger.info(f"Cluster naming: whole chain minute-limited, waiting {wait:.0f}s then retrying")
            await asyncio.sleep(wait + 1)
            label, _ = await _try_chain()
            if label:
                return label
        logger.warning("LLM cluster label fell back to genres (model chain exhausted)")
        return fallback
    
    async def create_user_clusters(
        self,
        user_id: int,
        db: AsyncSession,
        use_recency_bias: bool = False,
        background_tasks = None,
        groq_client = None
    ) -> List[UserCluster]:
        """
        Generate K-Means clusters for a user's taste profile
        Returns list of UserCluster objects
        """
        # Pre-clustering filter: only films that signal real "taste" drive geometry.
        # Drops ★<3.5 unrated noise (casual viewings) but keeps:
        #   - rating ≥ 3.5 (positive taste signal)
        #   - is_liked = True (explicit affinity)
        #   - watch_count ≥ 2 (rewatches → strong implicit signal even without high rating)
        # This addresses user 210's "rate-everything" pattern that dragged centroids
        # toward generic averages. See scripts/experiment_signal_a.py findings.
        result = await db.execute(
            select(UserRating, Movie)
            .join(Movie, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(
                or_(
                    UserRating.rating >= 3.5,
                    UserRating.is_liked.is_(True),
                    UserRating.watch_count >= 2,
                )
            )
        )
        ratings_movies = result.all()

        ratings_movies.sort(key=lambda x: x[1].id)

        if len(ratings_movies) < 10:
            if len(ratings_movies) < 5:
                logger.warning(f"User {user_id} has too few rated/liked movies ({len(ratings_movies)}) for clustering")
                return []
        
        # FIX: Never block the feed path with inline enrichment.
        # Vectors already exist in Qdrant at ingest time — keywords are optional metadata.
        # Only schedule as background task if context is available.
        for _, movie in ratings_movies:
            if movie.keywords is None and background_tasks:
                background_tasks.add_task(_enrich_movie_background, movie.tmdb_id)
            # If no background_tasks: skip silently. Vector exists, enrichment is cosmetic here.

        # Retrieve vectors from Qdrant (batch, no N+1)
        movie_tmdb_ids = [movie.tmdb_id for _, movie in ratings_movies]
        raw_vectors_map = await self.qdrant.get_vectors_batch(movie_tmdb_ids)

        vectors = []
        ratings = []
        movies_data = []

        for rating, movie in ratings_movies:
            vector = raw_vectors_map.get(movie.tmdb_id)
            if vector is not None:
                effective_rating = rating.rating
                if effective_rating is None and rating.is_liked:
                    effective_rating = 4.5
                
                if effective_rating is None:
                    continue

                vectors.append(vector)
                ratings.append(effective_rating)
                movies_data.append({
                    "id": movie.id,
                    "tmdb_id": movie.tmdb_id,
                    "title": movie.title,
                    "year": movie.year,
                    "genres": movie.genres or [],
                    "rating": effective_rating,
                    "vectorbox_score": movie.vectorbox_score or 0,
                    "embedding_quality_score": movie.embedding_quality_score,
                })
        
        if len(vectors) < 5:
            logger.warning("Insufficient movie vectors for clustering (Minimum 5 required)")
            return []
        
        X = np.array(vectors)
        
        if X.shape[1] != QdrantService.VECTOR_SIZE:
            logger.error(f"Vector Dimension Mismatch! Expected {QdrantService.VECTOR_SIZE}, got {X.shape[1]}. Aborting clustering.")
            return []
        
        from sklearn.preprocessing import normalize
        try:
            X_normalized = normalize(X)
        except Exception as e:
            logger.error(f"Normalization failed: {e}")
            return []
        
        ratings_array = np.array(ratings)
        
        weights = []
        now = datetime.now(timezone.utc)
        
        for i, r in enumerate(ratings):
            if r >= 4.5:
                w = 1.0
            elif r >= 4.0:
                w = 0.85
            elif r >= 3.5:
                w = 0.65
            elif r >= 3.0:
                w = 0.35
            elif r >= 2.0:
                w = 0.15
            else:
                w = 0.05

            rating_obj = ratings_movies[i][0]

            if use_recency_bias:
                # Sólo `watched_date`. Caer a `created_at` mezcla dos marcos
                # temporales: es la fecha de import, idéntica para cientos de filas
                # y "hoy" después de re-subir el ZIP, así que hundía lo fechado por
                # debajo de lo no fechado. Sin fecha → sin decay (peso neutro).
                date = rating_obj.watched_date

                if date:
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)
                    age_days = max(0, (now - date).days)
                    decay = max(0.6, 0.5 ** (age_days / 730))
                    w *= decay

            # Rewatch boost: movies seen multiple times signal stronger preference
            watch_count = getattr(rating_obj, 'watch_count', 1) or 1
            if watch_count >= 3:
                w = min(w * 1.5, 1.0)
            elif watch_count == 2:
                w = min(w * 1.2, 1.0)

            weights.append(w)

        weights_array = np.array(weights)

        # Use sklearn's sample_weight (proper weighted k-means) instead of multiplying
        # vectors by weight — multiplying distorts the vector space (puts low-weight
        # films closer to origin, creating spurious "small magnitude" clusters).
        # sample_weight only re-weights the centroid means, leaving vector positions intact.
        n_clusters = self.calculate_optimal_clusters(
            len(vectors), X=X_normalized, sample_weights=weights_array
        )

        kmeans = KMeans(
            n_clusters=n_clusters,
            random_state=42,
            n_init=10,
            max_iter=300,
        )

        loop = asyncio.get_running_loop()
        start_time = time.perf_counter()

        cluster_labels = await loop.run_in_executor(
            None,
            functools.partial(kmeans.fit_predict, X_normalized, sample_weight=weights_array),
        )
        
        duration = time.perf_counter() - start_time
        logger.info(f"K-Means clustering for user {user_id} took {duration:.4f}s")
        
        cluster_objects = []
        
        from sqlalchemy import delete
        await db.execute(delete(UserCluster).where(UserCluster.user_id == user_id))
        
        # Part A: Compute globally dominant genres (appear in >50% of all movies)
        total_movies = len(movies_data)
        global_genre_movie_count: dict[str, int] = {}
        for m in movies_data:
            for g in set(m["genres"]):  # set() to count once per movie
                global_genre_movie_count[g] = global_genre_movie_count.get(g, 0) + 1
        
        globally_dominant = {
            g for g, count in global_genre_movie_count.items()
            if count / total_movies > 0.50
        }
        if globally_dominant:
            logger.info(f"User {user_id}: globally dominant genres (>50%): {globally_dominant}")
        
        for cluster_id in range(n_clusters):
            cluster_indices = np.where(cluster_labels == cluster_id)[0]
            cluster_movies = [movies_data[i] for i in cluster_indices]
            
            if not cluster_movies:
                continue
            
            if len(cluster_movies) < 5:
                continue
                
            avg_rating = np.mean([m["rating"] for m in cluster_movies])
            
            all_genres = []
            for m in cluster_movies:
                all_genres.extend(m["genres"])
            
            genre_counts = Counter(all_genres)
            # Filter out globally dominant genres from per-cluster labels
            filtered_genres = [g for g, c in genre_counts.most_common(6) if g not in globally_dominant]
            # Fall back to unfiltered if filtering removes everything
            dominant_genres = filtered_genres[:3] if filtered_genres else [g for g, c in genre_counts.most_common(3)]
            
            years = [m["year"] for m in cluster_movies if m["year"]]
            if years:
                avg_year = int(np.mean(years))
                decade = f"{(avg_year // 10) * 10}s"
            else:
                decade = "Various"
            
            # Compute medoid: find the real film closest to the cluster center,
            # filtered by quality floor and genre coherence with dominant_genres.
            cluster_vectors = X_normalized[cluster_indices]
            centroid = np.mean(cluster_vectors, axis=0)
            distances = np.linalg.norm(cluster_vectors - centroid, axis=1)
            ranked_local_idxs = np.argsort(distances).tolist()

            MIN_MEDOID_SCORE = 55
            GENERIC_GENRES = {"Action", "Drama", "Comedy", "Adventure", "Thriller"}
            cluster_genres_set = set(dominant_genres or [])
            distinctive_genres = cluster_genres_set - GENERIC_GENRES
            filter_genres = distinctive_genres if distinctive_genres else cluster_genres_set

            def _qualifies(m, require_genre: bool) -> bool:
                if (m.get("vectorbox_score") or 0) < MIN_MEDOID_SCORE:
                    return False
                # T-03: Reject medoids with known-corrupt embeddings — the medoid drives every
                # recommendation in this cluster. NULL means unchecked, which we allow.
                eq = m.get("embedding_quality_score")
                if eq is not None and eq < 0.25:
                    return False
                if require_genre and filter_genres:
                    if not (set(m.get("genres") or []) & filter_genres):
                        return False
                return True

            qualified = [i for i in ranked_local_idxs if _qualifies(cluster_movies[i], require_genre=True)]
            if len(qualified) < 3:
                qualified = [i for i in ranked_local_idxs if _qualifies(cluster_movies[i], require_genre=False)]
            if not qualified:
                qualified = ranked_local_idxs

            medoid_local_idx = qualified[0]
            medoid_movie_data = cluster_movies[medoid_local_idx]
            medoid_movie_id = medoid_movie_data["id"]

            # Generate cluster label via LLM or fall back to genre-based.
            # Pass top 12 films (title + year) plus stats so the LLM can decide
            # whether to label a coherent theme or honestly return 'Mixed X'.
            cluster_movies_sorted = sorted(cluster_movies, key=lambda x: x["rating"], reverse=True)
            sample_films = [
                {"title": m["title"], "year": m.get("year")}
                for m in cluster_movies_sorted[:12]
            ]
            sample_movie_ids = [m["id"] for m in cluster_movies_sorted[:5]]
            cluster_avg_rating = (
                float(np.mean([m["rating"] for m in cluster_movies]))
                if cluster_movies else None
            )

            label = await self.generate_cluster_label(
                sample_films=sample_films,
                dominant_genres=dominant_genres,
                groq_client=groq_client,
                cluster_size=len(cluster_movies),
                avg_rating=cluster_avg_rating,
            )
            
            user_cluster = UserCluster(
                user_id=user_id,
                cluster_id=cluster_id,
                cluster_label=label,
                movie_count=len(cluster_movies),
                avg_rating=float(avg_rating),
                dominant_genres=dominant_genres,
                sample_movie_ids=sample_movie_ids,
                medoid_movie_id=medoid_movie_id
            )
            
            db.add(user_cluster)
            cluster_objects.append(user_cluster)
        
        try:
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error(f"DB commit failed during user cluster creation: {e}")
            raise
        
        return cluster_objects
    
    async def get_cluster_recommendations(
        self,
        user_id: int,
        cluster_id: int,
        db: AsyncSession,
        filters: Dict = None,
        limit: int = 20,
        page: int = 1,
        query_vector_override: list[float] = None
    ) -> List[Dict]:
        """
        Get movie recommendations for a specific cluster (mood)
        or use a custom query vector override (e.g. Profile Summary).
        """
        offset = (page - 1) * limit
        
        cluster_center = query_vector_override

        if cluster_center is None:
            result = await db.execute(
                select(UserCluster)
                .where(UserCluster.user_id == user_id)
                .where(UserCluster.cluster_id == cluster_id)
            )
            cluster = result.scalar_one_or_none()
            
            if not cluster:
                raise ValueError(f"Cluster {cluster_id} not found for user {user_id}")
            
            # Medoid-first: use the real film closest to the cluster center
            if cluster.medoid_movie_id:
                # Map medoid internal ID → tmdb_id
                medoid_result = await db.execute(
                    select(Movie.tmdb_id).where(Movie.id == cluster.medoid_movie_id)
                )
                medoid_tmdb_id = medoid_result.scalar_one_or_none()
                if medoid_tmdb_id:
                    medoid_vector = await self.qdrant.get_vector(medoid_tmdb_id)
                    if medoid_vector:
                        cluster_center = medoid_vector if isinstance(medoid_vector, list) else medoid_vector.tolist()
                    else:
                        logger.debug(f"Medoid vector not found in Qdrant for tmdb_id={medoid_tmdb_id}, falling back to centroid")
                else:
                    logger.debug(f"Medoid movie_id={cluster.medoid_movie_id} not found in DB, falling back to centroid")
            else:
                logger.debug(f"No medoid_movie_id set for cluster {cluster_id}, falling back to centroid")
            
            # Fallback to centroid if medoid is unavailable
            if cluster_center is None:
                CENTROID_CAP = 50
                result = await db.execute(
                    select(UserRating, Movie)
                    .join(Movie, UserRating.movie_id == Movie.id)
                    .where(UserRating.user_id == user_id)
                    .where(or_(UserRating.rating.isnot(None), UserRating.is_liked.is_(True)))
                    .order_by(desc(UserRating.rating))
                    .limit(CENTROID_CAP)
                )
                all_ratings = result.all()

                all_tmdb_ids = [movie.tmdb_id for _, movie in all_ratings]
                vectors_map = await self.qdrant.get_vectors_batch(all_tmdb_ids)
                cluster_vectors = list(vectors_map.values())

                if not cluster_vectors:
                    raise ValueError("No vectors found for cluster centroid fallback")

                cluster_center = np.mean(cluster_vectors, axis=0).tolist()
        
        search_filters = filters or {}
        effective_threshold = 0.15 if query_vector_override is not None else 0.3

        results = await self.qdrant.search_similar(
            query_vector=cluster_center,
            limit=limit * 5,
            offset=offset,
            score_threshold=effective_threshold,
            filters=search_filters
        )
        
        # AGENTS.md:245 — Qdrant uses tmdb_id; UserRating.movie_id is internal.
        # Convert the watched set to tmdb_ids before comparing against Qdrant hits.
        watched_result = await db.execute(
            select(Movie.tmdb_id)
            .join(UserRating, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(UserRating.is_watched.is_(True))
        )
        watched_tmdb_ids = set(watched_result.scalars().all())

        recommendations = [
            r for r in results
            if r["movie_id"] not in watched_tmdb_ids
        ][:limit]

        return recommendations

    async def get_user_centric_recommendations(
        self,
        user_id: int,
        db: AsyncSession,
        filters: Dict = None,
        limit: int = 20,
        page: int = 1,
        background_tasks = None
    ) -> List[Dict]:
        """Multi-anchor consensus (G2 strategy) — Signal A backbone.

        Picks top-N anchor films from the user's loved films, runs per-anchor
        Qdrant similarity search, and merges results with Reciprocal Rank Fusion.
        Films that are a neighbour of ≥2 anchors are RANKED FIRST — `CONSENSUS_MIN`
        sorts, it does not filter: single-anchor films follow, none are dropped.
        Measured 2026-08-06 at PER_ANCHOR_LIMIT=20: 19 of the 21 anchor pairs share
        no film at all, so that consensus head is only 3-5 films and the list is in
        practice a round-robin of the 7 neighbourhoods. Turning this into a real
        filter would leave 3-5 candidates.

        Rationale: for users with diverse tastes, a single geometric mean of
        all rated vectors lands in a "nowhereland" of vector space. Multi-anchor
        consensus surfaces films that sit at the intersection of multiple tastes
        — empirically +9-10 VBS vs global-centroid baseline on both coherent
        (user 212) and diverse (user 210) profiles. See scripts/experiment_signal_a.py.

        Falls back to single-anchor RRF order (by neighbour proximity) if too few
        consensus picks; falls back to global centroid if no anchors have vectors.
        """
        offset = (page - 1) * limit
        search_filters = filters or {}

        # Anchor candidates: rating ≥ 4.0 OR liked. Lower threshold (vs ★≥4.5)
        # gives more anchors so consensus signal is achievable for users
        # with few 5★ ratings.
        result = await db.execute(
            select(UserRating, Movie)
            .join(Movie, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(or_(UserRating.rating >= 4.0, UserRating.is_liked.is_(True)))
        )
        all_ratings = result.all()

        # FIX: Never block path with inline enrichment — background only.
        for _, movie in all_ratings:
            if movie.keywords is None and background_tasks:
                background_tasks.add_task(_enrich_movie_background, movie.tmdb_id)

        if not all_ratings:
            return []

        # Watched set (tmdb_ids — Qdrant point id convention)
        watched_result = await db.execute(
            select(Movie.tmdb_id)
            .join(UserRating, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(UserRating.is_watched.is_(True))
        )
        watched_tmdb_ids = set(watched_result.scalars().all())

        # Score each candidate as a potential anchor.
        # base = rating_part + liked_bonus + rewatch_signal; decayed by recency.
        def _recency_decay(ref_date, half_life_days: float = ANCHOR_HALF_LIFE_DAYS) -> float:
            if ref_date is None:
                # A CONSTANT neutral does not decay while everything else does, so on a
                # MIXED profile (part diary, part undated) it becomes a floor that ends up
                # above the whole profile — measured 2026-08-06: at +5 years it takes all 7
                # anchor slots. Safe today only because eligible rows are all-or-nothing on
                # dates (ratings.csv always carries one). If a mixed profile ever shows up,
                # make this relative to the user (median of their own decays), not a constant.
                return 0.5
            if ref_date.tzinfo is None:
                ref_date = ref_date.replace(tzinfo=timezone.utc)
            days = max(0.0, (datetime.now(timezone.utc) - ref_date).total_seconds() / 86400.0)
            return 0.5 ** (days / half_life_days)

        cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_ANCHOR_DAYS)
        # Neutro para las filas SIN fecha: la MEDIANA del decay de las filas fechadas de
        # ESTE usuario, no una constante. Un 0.5 fijo no decae mientras todo lo demás sí,
        # así que en un perfil mixto acaba siendo un suelo por encima del perfil entero.
        # Medido el 2026-08-11 sobre user 212 (51% de sus elegibles sin fecha tras
        # reimportar el ZIP, que le quitó las fechas de log del backfill):
        #     horizonte      0.5 fijo      mediana
        #     hoy                   0            0     anchors sin fecha en el top-7
        #     +2 años               0            0
        #     +5 años           **7/7**          0
        #     +10 años          **7/7**          0
        # Hoy los anchors salen IDÉNTICOS con las dos, así que esto no cambia nada ahora
        # y evita que la señal se coma a sí misma dentro de unos años.
        # Descartado usar el decay de la fila fechada más antigua: también es estable, pero
        # penaliza sistemáticamente el dato ausente — y eso son 84 de las 164 películas
        # queridas de user 212, por un hueco de datos, no por una preferencia.
        fechados = [
            _recency_decay(ur.watched_date) for ur, _ in all_ratings if ur.watched_date is not None
        ]
        neutro = float(np.median(fechados)) if fechados else 0.5

        scored: List[Tuple[float, Movie]] = []
        recent: List[Tuple[datetime, Movie]] = []
        for ur, m in all_ratings:
            base = max(0.0, ((ur.rating or 0) - 2.5) / 2.5) + (0.5 if ur.is_liked else 0.0)
            base += float(np.log1p(max(0, (ur.watch_count or 1) - 1))) * 0.3
            # watched_date ONLY: `created_at` is the import timestamp, identical for
            # every bulk-synced row, which made the decay a constant.
            decay = neutro if ur.watched_date is None else _recency_decay(ur.watched_date)
            scored.append((base * decay, m))
            wd = ur.watched_date
            if wd is not None:
                if wd.tzinfo is None:
                    wd = wd.replace(tzinfo=timezone.utc)
                if wd >= cutoff:
                    recent.append((wd, m))
        scored.sort(key=lambda x: -x[0])

        N_ANCHORS = 7
        PER_ANCHOR_LIMIT = 20
        K_RRF = 60
        CONSENSUS_MIN = 2

        anchors = select_anchors(scored, recent, N_ANCHORS)
        anchor_tmdb_ids = [m.tmdb_id for m in anchors]
        anchor_vecs_map = await self.qdrant.get_vectors_batch(anchor_tmdb_ids)

        # Per-anchor search + RRF aggregation
        rrf_scores: Dict[int, float] = {}
        anchor_count: Dict[int, int] = {}
        anchors_used = 0
        for m in anchors:
            vec = anchor_vecs_map.get(m.tmdb_id)
            if not vec:
                continue
            anchors_used += 1
            hits = await self.qdrant.search_similar(
                query_vector=list(vec),
                limit=PER_ANCHOR_LIMIT + 5,
                filters=search_filters,
            )
            # Drop the anchor itself and already-watched films
            ranked = [
                h for h in hits
                if h["movie_id"] != m.tmdb_id and h["movie_id"] not in watched_tmdb_ids
            ][:PER_ANCHOR_LIMIT]
            for rank, h in enumerate(ranked):
                mid = h["movie_id"]
                rrf_scores[mid] = rrf_scores.get(mid, 0.0) + 1.0 / (K_RRF + rank)
                anchor_count[mid] = anchor_count.get(mid, 0) + 1

        # Fallback if no anchor had a vector or all hits filtered out
        if not rrf_scores:
            logger.warning(
                f"User {user_id} G2 Recs: no anchor-based results "
                f"(anchors_used={anchors_used}); falling back to global centroid."
            )
            return await self._global_centroid_fallback(
                user_id, db, watched_tmdb_ids, search_filters, limit, offset
            )

        # Split consensus (≥2 anchors) vs single-anchor; consensus ranks first
        consensus_sorted = sorted(
            [mid for mid, n in anchor_count.items() if n >= CONSENSUS_MIN],
            key=lambda mid: -rrf_scores[mid],
        )
        single_sorted = sorted(
            [mid for mid, n in anchor_count.items() if n < CONSENSUS_MIN],
            key=lambda mid: -rrf_scores[mid],
        )
        # Over-fetch (limit * 5) so caller's quality gate has headroom
        target = max(limit * 5, limit + offset)
        result_ids = (consensus_sorted + single_sorted)[offset : offset + target]

        recommendations = [
            {"movie_id": mid, "score": rrf_scores[mid]}
            for mid in result_ids
        ][:limit * 5]

        consensus_n = len(consensus_sorted)
        logger.info(
            f"User {user_id} G2 Recs: anchors_used={anchors_used}/{N_ANCHORS}, "
            f"consensus={consensus_n}, single={len(single_sorted)}, "
            f"returned={len(recommendations)} (watched filtered upstream)."
        )
        return recommendations

    async def _global_centroid_fallback(
        self,
        user_id: int,
        db: AsyncSession,
        watched_tmdb_ids: set,
        search_filters: Dict,
        limit: int,
        offset: int,
    ) -> List[Dict]:
        """Old A-strategy: global mean of all rated/liked vectors. Used only
        when G2 cannot produce anchor-based results (rare).
        """
        result = await db.execute(
            select(Movie.tmdb_id)
            .join(UserRating, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(or_(UserRating.rating.isnot(None), UserRating.is_liked.is_(True)))
        )
        tmdb_ids = result.scalars().all()
        if not tmdb_ids:
            return []
        raw_vectors_map = await self.qdrant.get_vectors_batch(tmdb_ids)
        vectors = [v for v in raw_vectors_map.values() if v]
        if not vectors:
            return []
        global_center = np.mean(vectors, axis=0).tolist()
        results = await self.qdrant.search_similar(
            query_vector=global_center,
            limit=limit * 5,
            offset=offset,
            filters=search_filters,
        )
        return [r for r in results if r["movie_id"] not in watched_tmdb_ids][:limit * 5]

    def calculate_quality_weight(self, score: float) -> float:
        """Peso de calidad (0-1) del tridente a partir del VectorBox Score.

        ⚠️ Sigmoide PROPIA a propósito, distinta de la de Magic Search. Se probó
        unificarlas el 2026-08-11 (REV-5) y se REVIRTIÓ el mismo día. El porqué, porque
        el impulso de volver a unificarlas va a repetirse:

        Esta curva (`k=0.15, x0=65`) da un rango de **5.4×** entre VBS 55 y 90. La
        canónica de Magic Search (midpoint 55, steepness 0.10, suelo 0.20) da **1.6×**.
        Unificar parecía obviamente correcto —una curva para un trabajo— hasta que se
        midió qué ordena de verdad el tridente:

          - El score RRF tiene un rango de **1.32×**, y encima el 100% de los candidatos
            los vota UN solo anchor, así que el RRF es un round-robin con empates.
          - Medido con hold-out: ordenar por coseno en vez de por rank NO mejora
            (+0.10 / +0.05, ruido). Pesar los anchors por cuánto los amas da signos
            OPUESTOS en los dos usuarios (+0.25 / −0.45).
          - O sea: **el orden RRF no lleva información**. La calidad es lo único que
            ordena con criterio.

        Bajarla a 1.6× le daba voz a un componente mudo. Con la métrica actual no se puede
        demostrar cuál da mejores recomendaciones (el hold-out premia canonicidad), así que
        se conserva la que domina, que es la que estaba en producción.

        **Defectos conocidos de esta curva, aceptados hasta tener métrica:** `x0=65` cae en
        el p68 del catálogo (mediana real 58.3), así que la película mediana se multiplica
        por 0.23; y sin suelo, el cine de culto se anula del todo (VBS 40 → 0.023) en vez
        de sólo bajar. Si algún día se toca, la opción medida es mover x0 a ~58 SIN cambiar
        k=0.15: corrige el anclaje conservando la dominancia. Ver BACKLOG 2026-08-11.
        """
        if score is None:
            return 0.5

        k = 0.15
        x0 = 65
        return 1 / (1 + math.exp(-k * (score - x0)))

    async def get_item_based_recommendations(
        self,
        user_id: int,
        db: AsyncSession,
        filters: Dict = None,
        limit: int = 20,
        page: int = 1,
        background_tasks = None,
    ) -> List[Dict]:
        """
        Get recommendations using Weighted Item-Item Collaborative Filtering.
        FIX: Batch vector fetch + parallel search_similar to eliminate N+1 sequential calls.
        """
        offset = (page - 1) * limit
        
        result = await db.execute(
            select(UserRating, Movie)
            .join(Movie, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(or_(UserRating.rating >= 3.5, UserRating.is_liked.is_(True)))
            # nullslast y sin coalesce a created_at: en Postgres `DESC` pone los
            # NULL PRIMERO, así que sin esto las películas sin fecha encabezaban la
            # lista como si fueran las más recientes.
            .order_by(desc(UserRating.rating), desc(UserRating.watched_date).nullslast())
        )
        raw_seeds = result.all()
        
        if not raw_seeds:
            return []

        # FIX: Background-only enrichment, never inline.
        for _, movie in raw_seeds:
            if movie.keywords is None and background_tasks:
                background_tasks.add_task(_enrich_movie_background, movie.tmdb_id)

        # Input De-duplication (Franchise Collapsing)
        collection_map = {}
        standalone_seeds = []
        
        for rating, movie in raw_seeds:
            if movie.collection_id:
                if movie.collection_id not in collection_map:
                    collection_map[movie.collection_id] = []
                collection_map[movie.collection_id].append((rating, movie))
            else:
                standalone_seeds.append((rating, movie))
        
        final_seeds = []
        
        for rating, movie in standalone_seeds:
            final_seeds.append({
                "movie": movie,
                "rating": rating,
                "is_super_seed": False
            })
            
        for cid, items in collection_map.items():
            if len(items) > 1:
                items.sort(key=lambda x: (x[0].rating or 0), reverse=True)
                best_rating, best_movie = items[0]
                final_seeds.append({
                    "movie": best_movie,
                    "rating": best_rating,
                    "is_super_seed": True,
                    "collection_items": items
                })
            else:
                rating, movie = items[0]
                final_seeds.append({
                    "movie": movie,
                    "rating": rating,
                    "is_super_seed": False
                })

        # FIX: Filter qualifying seeds first, then batch-fetch ALL vectors in one call.
        qualifying_seeds = []
        for seed in final_seeds:
            rating_obj = seed["rating"]
            effective_rating = rating_obj.rating
            if effective_rating is None and rating_obj.is_liked:
                effective_rating = 4.5
            if effective_rating is None or effective_rating < 4.0:
                continue
            seed["effective_rating"] = effective_rating
            qualifying_seeds.append(seed)

        if not qualifying_seeds:
            return []

        # ONE batch call to Qdrant instead of N individual get_vector calls
        seed_tmdb_ids = [s["movie"].tmdb_id for s in qualifying_seeds]
        seed_vectors_map = await self.qdrant.get_vectors_batch(seed_tmdb_ids)
        logger.info(f"Item-Based: Fetched {len(seed_vectors_map)}/{len(seed_tmdb_ids)} seed vectors in batch.")

        # FIX: Run all search_similar calls concurrently via asyncio.gather
        async def search_for_seed(seed: Dict):
            movie = seed["movie"]
            vector = seed_vectors_map.get(movie.tmdb_id)
            if not vector:
                return seed, []
            similar = await self.qdrant.search_similar(
                query_vector=vector,
                limit=int(limit * 5),
                offset=offset,
                filters=filters
            )
            return seed, similar

        start_time = time.perf_counter()
        search_results = await asyncio.gather(*[search_for_seed(s) for s in qualifying_seeds])
        duration = time.perf_counter() - start_time
        logger.info(f"Item-Based: {len(qualifying_seeds)} parallel Qdrant searches took {duration:.4f}s")

        # Collect all TMDB IDs across all results for one batch DB lookup
        all_tmdb_ids: set = set()
        for seed, similar in search_results:
            for res in similar:
                if res["movie_id"] != seed["movie"].tmdb_id:
                    all_tmdb_ids.add(res["movie_id"])

        db_movies: Dict[int, Movie] = {}
        if all_tmdb_ids:
            stmt = select(Movie).where(Movie.tmdb_id.in_(all_tmdb_ids))
            movie_res = await db.execute(stmt)
            db_movies = {m.tmdb_id: m for m in movie_res.scalars().all()}

        # Accumulate candidates
        candidates: Dict[int, Dict] = {}

        for seed, similar in search_results:
            movie = seed["movie"]
            effective_rating = seed["effective_rating"]
            
            weight = 1.0 - (5.0 - effective_rating) * 0.05
            if seed["is_super_seed"]:
                weight *= 1.1

            for res in similar:
                tmdb_id = res["movie_id"]
                if tmdb_id == movie.tmdb_id:
                    continue
                
                db_movie = db_movies.get(tmdb_id)
                if not db_movie:
                    continue
                    
                internal_id = db_movie.id
                title = db_movie.title
                vb_score = db_movie.vectorbox_score
                
                effective_score = vb_score if vb_score is not None and vb_score > 0 else 50
                
                if effective_score < 40:
                    continue
                
                quality_weight = self.calculate_quality_weight(effective_score)
                similarity = res["score"]
                weighted_score = similarity * quality_weight
                
                if weighted_score > 1.0:
                    weighted_score = 1.0
                
                contribution = weighted_score * weight
                
                if internal_id not in candidates:
                    candidates[internal_id] = {
                        "movie_id": internal_id,
                        "tmdb_id": tmdb_id,
                        "score": 0.0,
                        "contributors": [],
                        "vector": None,
                        "raw_similarity": similarity,
                        "quality_weight": quality_weight,
                        "vb_score": vb_score,
                        "title": title
                    }
                
                candidates[internal_id]["score"] += contribution
                candidates[internal_id]["contributors"].append({
                    "seed_title": movie.title + (" (Franchise)" if seed["is_super_seed"] else ""),
                    "contribution": contribution
                })
        
        # Filter watched/watchlist
        watched_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id)
            .where(UserRating.is_watched.is_(True))
        )
        watched_ids = set(watched_result.scalars().all())
        
        logger.info(f"Item-Based Recs: Accumulated {len(candidates)} unique candidates from {len(qualifying_seeds)} seeds.")

        watchlist_ids = set()
        watchlist_only = filters.get("watchlist_only", False) if filters else False
        
        if watchlist_only:
            watchlist_result = await db.execute(
                select(UserRating.movie_id)
                .where(UserRating.user_id == user_id)
                .where(UserRating.is_watchlist.is_(True))
                .where(UserRating.is_watched.is_(False))
            )
            watchlist_ids = set(watchlist_result.scalars().all())
        
        pre_mmr_candidates = []
        for c in candidates.values():
            if c["movie_id"] in watched_ids:
                continue
            
            if watchlist_only and c["movie_id"] not in watchlist_ids:
                continue
                
            c["contributors"].sort(key=lambda x: x["contribution"], reverse=True)
            pre_mmr_candidates.append(c)
        
        pre_mmr_candidates.sort(key=lambda x: x["score"], reverse=True)
        
        logger.info(f"Item-Based Recs: {len(pre_mmr_candidates)} candidates remaining after Watched/Watchlist filter.")
        if pre_mmr_candidates:
            top_debug = [f"{c['title']} ({c['score']:.2f}, VB:{c['vb_score']})" for c in pre_mmr_candidates[:5]]
            logger.info(f"Top 5 candidates before Streaming/MMR: {top_debug}")

        # Streaming Filter (Pre-selection)
        valid_candidates = pre_mmr_candidates

        # MMR Reranking
        pool_size = max(50, limit)
        top_candidates = valid_candidates[:pool_size]
        
        if not top_candidates:
            return []

        candidate_ids = [c["movie_id"] for c in top_candidates]
        final_results = top_candidates
        
        try:
            stmt = select(Movie.id, Movie.tmdb_id).where(Movie.id.in_(candidate_ids))
            res = await db.execute(stmt)
            id_map = {row.id: row.tmdb_id for row in res.all()}
            
            tmdb_ids = [id_map[mid] for mid in candidate_ids if mid in id_map]
            
            if tmdb_ids:
                vectors_map = {}
                points = await self.qdrant.client.retrieve(
                    collection_name=self.qdrant.COLLECTION_NAME,
                    ids=tmdb_ids,
                    with_vectors=True
                )
                for p in points:
                    internal_id = next((iid for iid, tid in id_map.items() if tid == p.id), None)
                    dense = self.qdrant._dense_of(p.vector)
                    if internal_id and dense is not None:
                        vectors_map[internal_id] = np.array(dense)
                
                loop = asyncio.get_running_loop()
                start_time = time.perf_counter()
                
                mmr_func = functools.partial(
                    self.mmr_rerank, 
                    top_candidates, 
                    vectors_map, 
                    limit, 
                    lambda_param=0.7
                )
                
                final_results = await loop.run_in_executor(None, mmr_func)
                
                duration = time.perf_counter() - start_time
                logger.info(f"MMR Reranking took {duration:.4f}s for {len(top_candidates)} candidates")
                
        except Exception as e:
            logger.error(f"MMR Reranking failed: {e}. Returning raw list.")
        
        if final_results:
            max_score = final_results[0]["score"]
            if max_score > 0:
                for res in final_results:
                    res["score"] = 0.50 + (0.20 * (res["score"] / max_score))
        
        return final_results[:limit]

    def mmr_rerank(self, candidates: List[Dict], vectors_map: Dict[int, np.ndarray], limit: int, lambda_param: float = 0.7) -> List[Dict]:
        """
        Maximal Marginal Relevance (MMR) Reranking
        """
        selected = []
        pool = candidates.copy()
        
        while len(selected) < limit and pool:
            best_item = None
            best_mmr_score = -1.0
            
            for item in pool:
                relevance = item["score"]
                max_sim_to_selected = 0.0
                item_vec = vectors_map.get(item["movie_id"])
                
                if item_vec is not None and selected:
                    for selected_item in selected:
                        sel_vec = vectors_map.get(selected_item["movie_id"])
                        if sel_vec is not None:
                            sim = np.dot(item_vec, sel_vec) / (np.linalg.norm(item_vec) * np.linalg.norm(sel_vec))
                            if sim > max_sim_to_selected:
                                max_sim_to_selected = sim
                
                mmr_score = (lambda_param * relevance) - ((1 - lambda_param) * max_sim_to_selected)
                
                if mmr_score > best_mmr_score:
                    best_mmr_score = mmr_score
                    best_item = item
            
            if best_item:
                selected.append(best_item)
                pool.remove(best_item)
            else:
                break
                
        return selected

    async def clear_user_cache(self, user_id: int):
        """
        Manually invalidate the Redis cache for this user's recommendations
        after cluster regeneration. Covers feed sections, signal cache, and
        cluster rotation key. Uses SCAN to avoid blocking.
        """
        import os
        import redis.asyncio as redis
        from config import FEED_CACHE_VERSION

        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        r = None
        try:
            from services.cache_service import scan_and_delete
            r = redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
            total_deleted = 0
            for pattern in (
                f"fastapi-cache:*{user_id}*",
                f"section:{FEED_CACHE_VERSION}:{user_id}:*",
                f"signal_cache:{user_id}:*",
            ):
                total_deleted += await scan_and_delete(r, pattern)
            await r.delete(f"cluster_rotation:{FEED_CACHE_VERSION}:{user_id}")
            if total_deleted:
                logger.info(f"Cleared {total_deleted} cache keys due to cluster regeneration (user_id={user_id}).")
        except Exception as e:
            logger.error(f"Failed to clear user cache: {e}")
        finally:
            if r:
                await r.close()