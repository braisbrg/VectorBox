
import logging
import asyncio
from typing import List, Dict, Set, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, or_, func, text
from collections import Counter
import random
import math

from models.database import UserRating, Movie, UserCluster
from models.schemas import FeedSection, FeedItem
from services.tmdb_client import TMDBClient
from services.qdrant_service import QdrantService
from services.clustering_service import ClusteringService
from services.movie_service import MovieService
from services.provider_service import ProviderService

from utils.decorators import safe_execution

logger = logging.getLogger(__name__)

class RecommendationService:
    """
    The "Trident" Hybrid Recommender System.
    Merges 3 distinct signals:
    - Signal A: Vibe (Vector Embeddings)
    - Signal B: Auteur (Director Analysis)
    - Signal C: Crowd (TMDB Collaborative Filtering)
    """

    def __init__(self, db: AsyncSession, tmdb: TMDBClient = None, qdrant: QdrantService = None):
        self.db = db
        self.tmdb = tmdb
        self.qdrant = qdrant
        self.clustering = ClusteringService(qdrant=qdrant)
        self.movie_service = MovieService(db)

    @safe_execution(fallback_return=FeedSection(id="hybrid_picks", title="Hybrid Picks (Signal Lost)", items=[]))
    async def get_hybrid_picks_section(
        self, 
        user_id: int, 
        country: str,
        seen_ids: Set[int],
        provider_service: ProviderService = None,
        background_tasks = None
    ) -> FeedSection:
        """
        Main entry point for "The Trident" row.
        """
        logger.info(f"Generating Trident Hybrid Picks for User {user_id}")
        
        # 1. Gather Signals in Parallel
        import time
        start_time = time.time()
        
        # Define wrappers to measure individual signal time
        async def measure_signal(name, task):
            t0 = time.time()
            res = await task
            duration = (time.time() - t0) * 1000
            logger.info(f"[TRIDENT] Signal {name} took {duration:.2f}ms")
            return res

        signal_a_task = measure_signal("A (Vibe)", self.get_signal_a_vibe(user_id, exclude_ids=seen_ids, background_tasks=background_tasks))
        signal_b_task = measure_signal("B (Auteur)", self.get_signal_b_auteur(user_id, exclude_ids=seen_ids))
        signal_c_task = measure_signal("C (Crowd)", self.get_signal_c_crowd(user_id, exclude_ids=seen_ids))
        
        results = await asyncio.gather(signal_a_task, signal_b_task, signal_c_task, return_exceptions=True)
        
        total_time = (time.time() - start_time) * 1000
        logger.info(f"[TRIDENT] Full Trident gathering took {total_time:.2f}ms")
        
        # Handle exceptions gracefully
        candidates_lists = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(f"Signal {['A', 'B', 'C'][i]} failed: {res}")
                candidates_lists.append([])
            else:
                candidates_lists.append(res)
                
        signal_a, signal_b, signal_c = candidates_lists
        
        logger.info(f"[TRIDENT] Signal Counts -> A: {len(signal_a)}, B: {len(signal_b)}, C: {len(signal_c)}")
        
        # 2. Fusion (RRF)
        # We assume candidates are Movie objects (or dicts representing them)
        # We need uniform ID access. Let's make sure signals return Movie objects.
        
        rrf_scores = self.reciprocal_rank_fusion([signal_a, signal_b, signal_c])
        
        # 3. Post-Processing (Quality & Diversity)
        final_items = await self.hybrid_reranking(rrf_scores, user_id, country, provider_service)
        
        # Update seen_ids
        for item in final_items:
            seen_ids.add(item.id)
            
        return FeedSection(
            id="hybrid_picks",
            title="Hybrid Picks for You",
            items=final_items
        )

    async def get_signal_a_vibe(self, user_id: int, exclude_ids: Set[int], background_tasks = None) -> List[Movie]:
        """
        Signal A: The Vibe Expert (Vectors)
        Uses Qdrant via ClusteringService logic.
        """
        # We reuse the logic from Hidden Gems / User Centric but without strict filters
        raw_recs = await self.clustering.get_user_centric_recommendations(
            user_id=user_id,
            db=self.db,
            filters={"min_vote_count": 500}, # Basic quality filter
            limit=50,
            background_tasks=background_tasks
        )
        
        movie_ids = [r["movie_id"] for r in raw_recs if r["movie_id"] not in exclude_ids]
        
        if not movie_ids:
            return []
            
        # Fetch Movie objects
        stmt = select(Movie).where(Movie.id.in_(movie_ids))
        result = await self.db.execute(stmt)
        movies = result.scalars().all()
        
        # Re-sort match per raw_recs order
        movies_map = {m.id: m for m in movies}
        ordered = []
        for mid in movie_ids:
            if mid in movies_map:
                ordered.append(movies_map[mid])
                
        return ordered

    async def get_signal_b_auteur(self, user_id: int, exclude_ids: Set[int]) -> List[Movie]:
        """
        Signal B: The Auteur Expert (Metadata Graph)
        Finds user's top directors and recommends their high-quality unwatched movies.
        """
        # 1. Analyze Top Directors
        # Get highly rated movies
        stmt = select(Movie.directors).join(UserRating, Movie.id == UserRating.movie_id)\
            .where(UserRating.user_id == user_id, UserRating.rating >= 4.0)
            
        result = await self.db.execute(stmt)
        all_directors = []
        for row in result:
            if row.directors:
                all_directors.extend(row.directors)
                
        if not all_directors:
            return []
            
        # Top 5 Directors
        director_counts = Counter(all_directors)
        top_directors = [name for name, count in director_counts.most_common(5) if count >= 2]
        
        if not top_directors:
            return []
            
        # 2. Query DB for matches
        # We use ARRAY overlap operator "&&" if using specific dialect, but standard ANY is safer
        # SQL: SELECT * FROM movies WHERE directors && ARRAY['Nolan', 'Villeneuve']
        # SQLAlchemy pg dialect supports overlapping
        
        # Since we are using ARRAY(String), we can use overlap
        # But to be safe and simple, let's fetch candidates that have ANY of these directors
        # And VectorBox Score > 70 (High Quality)
        
        stmt = select(Movie).where(
            Movie.vectorbox_score > 70,
            Movie.directors.overlap(top_directors)
        ).limit(100)
        
        candidates = (await self.db.execute(stmt)).scalars().all()
        
        # Filter watched/excluded
        # We need watched IDs first
        watched_stmt = select(UserRating.movie_id).where(
            UserRating.user_id == user_id, UserRating.is_watched.is_(True)
        )
        watched_ids = set((await self.db.execute(watched_stmt)).scalars().all())
        
        final_list = []
        for m in candidates:
            if m.tmdb_id in exclude_ids or m.id in watched_ids:
                continue
            final_list.append(m)
            
        return final_list[:50]

    async def get_signal_c_crowd(self, user_id: int, exclude_ids: Set[int]) -> List[Movie]:
        """
        Signal C: The Crowd Expert (Collaborative Filtering via TMDB)
        "People who liked X also liked Y"
        """
        if not self.tmdb:
            logger.warning("[Signal C] No TMDBClient injected, skipping crowd signal.")
            return []
        
        # 1. Get 3 most recent 5-star movies
        stmt = select(Movie).join(UserRating, Movie.id == UserRating.movie_id)\
            .where(UserRating.user_id == user_id, UserRating.rating == 5.0)\
            .order_by(desc(UserRating.watched_date))\
            .limit(3)
            
        seeds = (await self.db.execute(stmt)).scalars().all()
        
        if not seeds:
            return []
            
        # 2. Collect all TMDB IDs from TMDB recommendations (parallel, no sequential N+1)
        all_tmdb_ids: List[int] = []
        tasks = [self.tmdb.get_movie_recommendations(seed.tmdb_id) for seed in seeds]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, recs in enumerate(results):
            if isinstance(recs, Exception):
                logger.warning(f"[Signal C] TMDB recs failed for seed {seeds[i].tmdb_id}: {recs}")
                continue
            for r in recs[:5]:
                tid = r['id']
                if tid not in exclude_ids:
                    all_tmdb_ids.append(tid)

        
        if not all_tmdb_ids:
            return []

        # 3. BATCH CHECK: which IDs already exist in DB? (single query, no N+1)
        existing_result = await self.db.execute(
            select(Movie).where(Movie.tmdb_id.in_(all_tmdb_ids))
        )
        existing_movies = existing_result.scalars().all()
        existing_tmdb_ids = {m.tmdb_id for m in existing_movies}

        # 4. Ingest only the missing ones (max 5 to avoid long waits)
        missing_ids = [tid for tid in all_tmdb_ids if tid not in existing_tmdb_ids][:5]
        newly_ingested: List[Movie] = []
        for tid in missing_ids:
            try:
                movie = await self.movie_service.get_or_create_movie(tid)
                if movie:
                    newly_ingested.append(movie)
            except Exception as e:
                logger.warning(f"[Signal C] Could not ingest TMDB {tid}: {e}")

        # 5. Combine and deduplicate
        all_movies = list(existing_movies) + newly_ingested
        seen_local: Set[int] = set()
        unique: List[Movie] = []
        for m in all_movies:
            if m.id not in seen_local and m.tmdb_id not in exclude_ids:
                unique.append(m)
                seen_local.add(m.id)
                
        return unique

    def reciprocal_rank_fusion(self, candidate_lists: List[List[Movie]], k=60) -> Dict[int, float]:
        """
        RRF Algorithm: Merges multiple ranked lists.
        Score = sum(1 / (k + rank))
        """
        scores = {}
        movies_map = {} # To keep track of objects
        
        for lst in candidate_lists:
            for rank, movie in enumerate(lst):
                if movie.id not in scores:
                    scores[movie.id] = 0.0
                    movies_map[movie.id] = movie
                
                scores[movie.id] += 1 / (k + rank)
                
        return scores

    async def hybrid_reranking(
        self,
        rrf_scores: Dict[int, float],
        user_id: int,
        country: str,
        provider_service: ProviderService
    ) -> List[FeedItem]:
        """
        Final polish: RRF Score * Quality Score -> Collection Collapsing -> Batch Provider Fetch
        """
        if not rrf_scores:
            return []

        # 1. Batch-fetch Movie objects from DB (single query)
        movie_ids = list(rrf_scores.keys())
        stmt = select(Movie).where(Movie.id.in_(movie_ids))
        result = await self.db.execute(stmt)
        movies = result.scalars().all()

        # 2. Score = RRF * Sigmoid Quality Weight
        candidates = []
        for m in movies:
            rrf_score = rrf_scores.get(m.id, 0)
            vb_score = m.vectorbox_score or 50
            quality_weight = self.clustering.calculate_quality_weight(vb_score)
            candidates.append({"movie": m, "score": rrf_score * quality_weight})

        candidates.sort(key=lambda x: x["score"], reverse=True)

        # 3. Batch-fetch Qdrant vectors for top 20 (single call, no N+1)
        import numpy as np
        import functools
        top_candidates = candidates[:20]
        candidate_tmdb_ids = [c["movie"].tmdb_id for c in top_candidates]
        raw_vectors = await self.qdrant.get_vectors_batch(candidate_tmdb_ids)
        # Map internal_id -> vector (get_vectors_batch returns tmdb_id -> vector)
        tmdb_to_internal = {c["movie"].tmdb_id: c["movie"].id for c in top_candidates}
        vectors_map = {
            tmdb_to_internal[tmdb_id]: np.array(v)
            for tmdb_id, v in raw_vectors.items()
            if tmdb_id in tmdb_to_internal
        }

        # 4. MMR Reranking (diversity-aware, CPU-bound → executor)
        loop = asyncio.get_running_loop()
        mmr_func = functools.partial(
            self.clustering.mmr_rerank,
            top_candidates,
            vectors_map,
            10,          # limit: 10 final items
            0.7          # lambda_param: 70% relevance, 30% diversity
        )
        try:
            final_list = await loop.run_in_executor(None, mmr_func)
        except Exception as e:
            logger.error(f"[Trident] MMR failed, falling back to top-10: {e}")
            # Fallback: simple collection collapsing
            seen_collections: set = set()
            final_list = []
            for c in candidates:
                m = c["movie"]
                if m.collection_id:
                    if m.collection_id in seen_collections:
                        continue
                    seen_collections.add(m.collection_id)
                final_list.append(c)
                if len(final_list) >= 10:
                    break

        # 5. Batch-fetch providers (single query, no N+1)
        feed_items = []
        if provider_service and final_list:
            movie_internal_ids = [item["movie"].id for item in final_list]
            providers_map = await provider_service.get_providers_batch(movie_internal_ids, country)
        else:
            providers_map = {}

        for item in final_list:
            movie = item["movie"]
            p_data = providers_map.get(movie.id, [])
            providers = [p["provider_name"] for p in p_data]
            feed_items.append(FeedItem(
                id=movie.tmdb_id,
                title=movie.title,
                poster_url=movie.poster_path,
                match_score=98,
                streaming_providers=list(set(providers)),
                year=movie.year,
                runtime=movie.runtime,
                letterboxd_uri=movie.letterboxd_uri,
                rating=movie.vote_average,
                overview=movie.overview,
                contributors=[],
                vectorbox_score=movie.vectorbox_score,
                imdb_rating=movie.imdb_rating,
                metacritic_rating=movie.metacritic_rating,
                rotten_tomatoes_rating=movie.rotten_tomatoes_rating,
                title_es=movie.title_es,
                overview_es=movie.overview_es
            ))

        return feed_items

    @safe_execution(fallback_return=FeedSection(id="auteur_picks", title="From Your Favorite Directors", items=[]))
    async def get_auteur_section(self, user_id: int, country: str, seen_ids: Set[int], provider_service: ProviderService = None) -> FeedSection:
        """
        Signal B Only Row: "From Your Favorite Directors"
        """
        candidates = await self.get_signal_b_auteur(user_id, seen_ids)
        
        # Batch fetch providers (no N+1)
        if provider_service and candidates:
            candidate_ids = [m.id for m in candidates]
            providers_map = await provider_service.get_providers_batch(
                candidate_ids, country
            )
        else:
            providers_map = {}
        
        items = []
        for m in candidates:
             p_data = providers_map.get(m.id, [])
             flat_providers = [p["provider_name"] for p in p_data]
             
             # Basic FeedItem creation
             items.append(FeedItem(
                id=m.tmdb_id,
                title=m.title,
                poster_url=m.poster_path,
                match_score=90,
                streaming_providers=flat_providers,
                year=m.year,
                overview=m.overview,
                vectorbox_score=m.vectorbox_score
             ))
             seen_ids.add(m.tmdb_id)
             
        return FeedSection(
            id="auteur_picks",
            title="From Your Favorite Directors",
            items=items
        )

    async def close(self):
        """Cleanup resources"""
        if self.tmdb:
            await self.tmdb.aclose()
        if self.movie_service:
            await self.movie_service.close()
