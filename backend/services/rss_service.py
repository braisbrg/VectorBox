"""
RSS Service for syncing Letterboxd data and calculating group vibes
"""
import feedparser
import logging
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import delete, cast, select, Date, func, case
import numpy as np

from models.database import User, Movie, UserRating
from services.tmdb_client import TMDBClient
from services.qdrant_service import QdrantService
from services.omdb_client import OMDbClient
from services.embedding_service import EmbeddingService

from services.movie_service import MovieService

logger = logging.getLogger(__name__)

class RSSService:
    def __init__(
        self,
        db: Session,
        tmdb: TMDBClient = None,
        qdrant: QdrantService = None,
        embedding_service: EmbeddingService = None,
        omdb: OMDbClient = None,
    ):
        self.db = db
        self.tmdb = tmdb  # callers must pass — no fallback
        self.omdb = omdb  # callers must pass — no fallback
        self.qdrant = qdrant
        self.embedding_service = embedding_service
        import os
        self.groq_client = None
        try:
            from openai import AsyncOpenAI
            if os.getenv("GROQ_API_KEY"):
                self.groq_client = AsyncOpenAI(
                    api_key=os.getenv("GROQ_API_KEY"),
                    base_url="https://api.groq.com/openai/v1",
                    max_retries=0,
                )
            elif os.getenv("GEMINI_API_KEY"):
                self.groq_client = AsyncOpenAI(
                    api_key=os.getenv("GEMINI_API_KEY"),
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                )
        except ImportError:
            logger.warning("openai package not found, LLM features disabled for RSS")
        self.movie_service = MovieService(db, tmdb=self.tmdb, groq_client=self.groq_client)

    async def fetch_user_rss(self, username: str) -> List[Dict]:
        """
        Fetch and parse Letterboxd RSS feed for a user
        Returns a list of watched movies with metadata
        """
        url = f"https://letterboxd.com/{username}/rss/"
        logger.info(f"Fetching RSS feed for {username}: {url}")
        
        feed = feedparser.parse(url)
        
        if feed.bozo:
            logger.error(f"Error parsing RSS feed for {username}: {feed.bozo_exception}")
            return []
            
        watched_items = []
        
        for entry in feed.entries:
            # We only care about watched films, which usually have letterboxd_filmtitle
            if not hasattr(entry, 'letterboxd_filmtitle'):
                continue
                
            # Extract data
            item = {
                'title': entry.letterboxd_filmtitle,
                'year': int(entry.letterboxd_filmyear) if hasattr(entry, 'letterboxd_filmyear') else None,
                'uri': entry.link,
                'watched_date': None,
                'rating': None,
                'rewatch': False,
                'is_liked': False,
            }

            # Parse watched date
            if hasattr(entry, 'letterboxd_watcheddate'):
                try:
                    item['watched_date'] = datetime.strptime(entry.letterboxd_watcheddate, "%Y-%m-%d")
                except ValueError:
                    pass

            # Parse rating (member rating is 0-5)
            if hasattr(entry, 'letterboxd_memberrating'):
                try:
                    item['rating'] = float(entry.letterboxd_memberrating)
                except ValueError:
                    pass

            # Parse rewatch
            if hasattr(entry, 'letterboxd_rewatch'):
                item['rewatch'] = entry.letterboxd_rewatch == 'Yes'

            # Parse like / heart. Letterboxd exposes this as <letterboxd:liked>true|false</letterboxd:liked>
            # in the diary RSS feed. Without this extraction, RSS-only users (no ZIP import)
            # never get is_liked populated.
            if hasattr(entry, 'letterboxd_liked'):
                raw = (entry.letterboxd_liked or '').strip().lower()
                item['is_liked'] = raw in ('true', 'yes', '1')

            # Parse TMDB ID (Crucial for accurate matching)
            if hasattr(entry, 'tmdb_movieid'):
                try:
                    item['tmdb_id'] = int(entry.tmdb_movieid)
                except ValueError:
                    item['tmdb_id'] = None
            else:
                item['tmdb_id'] = None
                
            watched_items.append(item)
            
        return watched_items

    async def sync_user_rss(self, username: str, user_id: int) -> Dict:
        """
        Sync user's RSS feed to database
        1. Fetch RSS
        2. For new movies: fetch TMDB, vectorise, save to DB
        3. Save UserRating
        """
        items = await self.fetch_user_rss(username)
        stats = {"processed": 0, "new_movies": 0, "new_ratings": 0, "updated_ratings": 0, "errors": 0}

        # Pre-fetch existing movies in two batched queries (was N+1 per item)
        all_tmdb_ids = [i['tmdb_id'] for i in items if i.get('tmdb_id')]
        all_uris = [i['uri'] for i in items if i.get('uri')]
        movies_by_tmdb: Dict[int, Movie] = {}
        movies_by_uri: Dict[str, Movie] = {}
        if all_tmdb_ids:
            r = await self.db.execute(select(Movie).where(Movie.tmdb_id.in_(all_tmdb_ids)))
            movies_by_tmdb = {m.tmdb_id: m for m in r.scalars().all()}
        if all_uris:
            r = await self.db.execute(select(Movie).where(Movie.letterboxd_uri.in_(all_uris)))
            movies_by_uri = {m.letterboxd_uri: m for m in r.scalars().all()}

        # Skip enrichment for movies enriched in the last 7 days
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        enrich_cutoff = _dt.now(_tz.utc) - _td(days=7)

        async def _maybe_enrich(m: Movie) -> None:
            le = m.last_enriched
            if le is not None:
                if le.tzinfo is None:
                    le = le.replace(tzinfo=_tz.utc)
                if le >= enrich_cutoff:
                    return
            await self.movie_service.enrich_movie(m)

        # N+1 FIX: Pre-fetch all existing UserRatings for this user in one query.
        # The loop below previously fired one SELECT per item; now it's a single batch.
        # We collect all internal movie IDs we already know about from our batch lookups.
        _known_movie_ids: set[int] = set()
        for m in movies_by_tmdb.values():
            _known_movie_ids.add(m.id)
        for m in movies_by_uri.values():
            _known_movie_ids.add(m.id)

        existing_ratings_by_movie_id: Dict[int, "UserRating"] = {}
        if _known_movie_ids:
            _er_result = await self.db.execute(
                select(UserRating).where(
                    UserRating.user_id == user_id,
                    UserRating.movie_id.in_(list(_known_movie_ids))
                )
            )
            existing_ratings_by_movie_id = {ur.movie_id: ur for ur in _er_result.scalars().all()}

        for item in items:
            stats["processed"] += 1
            try:
                movie = None

                # 1. Try matching by TMDB ID (Most accurate)
                if item.get('tmdb_id'):
                    movie = movies_by_tmdb.get(item['tmdb_id'])

                    if movie:
                        # Update URI if missing
                        if not movie.letterboxd_uri:
                            movie.letterboxd_uri = item['uri']
                            try:
                                await self.db.commit()
                            except Exception as e:
                                await self.db.rollback()
                                logger.error(f"DB commit failed updating letterboxd_uri: {e}")
                                raise
                    if movie:
                        await _maybe_enrich(movie)

                # 2. Fallback: Try matching by Letterboxd URI
                if not movie:
                    movie = movies_by_uri.get(item['uri'])

                    if movie:
                         logger.info(f"Found movie by URI: {movie.title}")
                         await _maybe_enrich(movie)

                # 3. Fallback: Try Title + Year (Only if no TMDB ID was provided)
                if not movie and item['year'] and not item.get('tmdb_id'):
                    stmt = select(Movie).where(
                        Movie.title == item['title'],
                        Movie.year == item['year']
                    )
                    result = await self.db.execute(stmt)
                    movie = result.scalar_one_or_none()
                    if movie:
                        logger.info(f"Found movie by Title+Year: {movie.title}")
                        # Update URI if missing
                        if not movie.letterboxd_uri:
                            movie.letterboxd_uri = item['uri']
                            try:
                                await self.db.commit()
                            except Exception as e:
                                await self.db.rollback()
                                logger.error(f"DB commit failed updating letterboxd_uri: {e}")
                                raise
                        await _maybe_enrich(movie)
                
                # 4. If movie doesn't exist, fetch from TMDB and create it
                if not movie:
                    tmdb_id = item.get('tmdb_id')
                    
                    if not tmdb_id:
                        # If we don't have an ID from RSS, we must search (fallback)
                        logger.info(f"No TMDB ID in RSS for {item['title']}. Searching TMDB...")
                        search_result = await self.tmdb.search_movie(item['title'], item['year'])
                        
                        if not search_result:
                            logger.warning(f"Could not find movie on TMDB: {item['title']}")
                            stats["errors"] += 1
                            continue
                            
                        # Strict Year Check
                        if item['year']:
                            result_date = search_result.get('release_date', '')
                            result_year = int(result_date[:4]) if result_date else None
                            if result_year and abs(result_year - item['year']) > 1:
                                logger.warning(f"Rejected TMDB match for {item['title']} ({item['year']}): Found {search_result['title']} ({result_year})")
                                stats["errors"] += 1
                                continue
                        
                        tmdb_id = search_result['id']
                    else:
                        logger.info(f"Using TMDB ID from RSS: {tmdb_id} for {item['title']}")

                    # Use MovieService to get or create (with full enrichment)
                    movie = await self.movie_service.get_or_create_movie(
                        tmdb_id=tmdb_id, 
                        letterboxd_uri=item['uri']
                    )
                    
                    if movie:
                        stats["new_movies"] += 1
                    else:
                        stats["errors"] += 1
                        continue
                # 7. Upsert rating safely
                # Use pre-fetched batch map for known movies; fall back to a live query for
                # movies just created via get_or_create_movie (not in the initial batch).
                if movie.id in existing_ratings_by_movie_id:
                    existing_rating = existing_ratings_by_movie_id[movie.id]
                else:
                    existing_rating_result = await self.db.execute(
                        select(UserRating).where(
                            UserRating.user_id == user_id,
                            UserRating.movie_id == movie.id
                        )
                    )
                    existing_rating = existing_rating_result.scalar_one_or_none()

                # watch_count bump rule (idempotent):
                #   - Only when RSS flags this entry as a rewatch (<letterboxd:rewatch>Yes</...>).
                #   - AND the incoming watched_date is strictly later than the existing one.
                # Both conditions together prevent the historical bug where Wolf Beach,
                # Eterna, etc. ended up with watch_count=3 simply because the RSS sync
                # re-processed the same diary entry on every cron tick. ZIP uploads
                # remain authoritative — they overwrite watch_count via excluded.
                incoming_date = item.get('watched_date')
                existing_date = existing_rating.watched_date if existing_rating else None
                rewatch_flag = bool(item.get('rewatch')) and (
                    existing_date is None or (incoming_date is not None and incoming_date > existing_date)
                )

                stmt = insert(UserRating).values(
                    user_id=user_id,
                    movie_id=movie.id,
                    rating=item.get('rating'),
                    is_watched=True, # Always True for RSS items
                    is_liked=item.get('is_liked', False),
                    watched_date=item.get('watched_date'),
                    review=item.get('review'),
                    watch_count=1,  # initial value for new rows
                ).on_conflict_do_update(
                    index_elements=["user_id", "movie_id"],
                    set_={
                        "rating": getattr(insert(UserRating).excluded, "rating"),
                        "is_watched": True, # Ensure it's marked as watched
                        # is_liked: RSS now extracts <letterboxd:liked> per item, so we
                        # update it to reflect Letterboxd's current state. RSS is
                        # authoritative for likes once the parser captures the field.
                        "is_liked": getattr(insert(UserRating).excluded, "is_liked"),
                        "watched_date": getattr(insert(UserRating).excluded, "watched_date"),
                        "review": getattr(insert(UserRating).excluded, "review"),
                        "watch_count": (
                            func.coalesce(UserRating.watch_count, 1) + 1
                            if rewatch_flag
                            else func.coalesce(UserRating.watch_count, 1)
                        ),
                    }
                )

                try:
                    result = await self.db.execute(stmt)
                    # Check if a row was inserted or updated using pre-fetched existing_rating
                    if result.rowcount > 0:
                        if existing_rating is None:
                            stats["new_ratings"] += 1
                        else:
                            stats["updated_ratings"] += 1
                    await self.db.commit()
                except Exception as e:
                    await self.db.rollback()
                    logger.error(f"DB commit failed syncing rss item: {e}")
                    raise

                
                # CLEANUP: Check for "Phantom Movies" (Same date, different movie, NOT in RSS)
                # This fixes the "Away" vs "Spirited Away" issue where both might exist on the same date
                if item['watched_date']:
                    # Get all TMDB IDs from the current RSS feed to know what is "valid"
                    rss_tmdb_ids = {i.get('tmdb_id') for i in items if i.get('tmdb_id')}
                    
                    # Find other ratings for this user on this same date
                    stmt = select(UserRating).join(Movie).where(
                        UserRating.user_id == user_id,
                        cast(UserRating.watched_date, Date) == item['watched_date'].date(),
                        UserRating.movie_id != movie.id
                    )
                    result = await self.db.execute(stmt)
                    conflicts = result.scalars().all()
                    
                    for conflict in conflicts:
                        # If the conflicting movie is NOT in our RSS feed, it's likely a phantom/incorrect match
                        # We need to fetch the movie to check its TMDB ID
                        conflict_movie = await self.db.get(Movie, conflict.movie_id)
                        if conflict_movie and conflict_movie.tmdb_id not in rss_tmdb_ids:
                            logger.warning(f"Removing phantom rating: {conflict_movie.title} on {item['watched_date'].date()} (Not in RSS)")
                            await self.db.delete(conflict)
                            stats["processed"] += 1 # Count as an action

                try:
                    await self.db.commit()
                except Exception as e:
                    await self.db.rollback()
                    logger.error(f"DB commit failed syncing rss item: {e}")
                    raise
            except Exception as e:
                logger.error(f"Error processing item {item.get('title')}: {e}")
                stats["errors"] += 1
                await self.db.rollback()
                
        return stats

    async def get_group_recommendations_hybrid(self, usernames: List[str]) -> List[Dict]:
        """
        Group Vibe 2.0: Hybrid Watchlist Priority + Discovery
        1. Collect Taste Vectors & Watchlists for all users (DB & Guest).
        2. Generate Candidates:
           - Priority: Movies in DB users' watchlists.
           - Fallback: If pool is small, add "Discovery" movies (via Centroid Search).
        3. Score Candidates:
           - Cosine Similarity to EACH user's vector (not just average).
           - Bonus for being on a watchlist.
           - Penalty for being very dissimilar to ANY single user (avoid polarizing movies).
        4. Sort & Return.
        """
        # 1. Collect Data
        user_data = [] # List of {'username': str, 'vector': np.array}
        watchlist_candidates = set()
        excluded_ids = set()
        
        for username in usernames:
            # Find user in DB
            stmt = select(User).where(User.username == username)
            result = await self.db.execute(stmt)
            user = result.scalar_one_or_none()
            
            user_vector = None
            
            if user:
                # --- DB USER ---
                # A. Get Taste Vector (Avg of recent 4+ star movies)
                stmt = select(Movie.tmdb_id).join(UserRating).where(
                    UserRating.user_id == user.id,
                    UserRating.rating >= 4.0
                ).order_by(UserRating.watched_date.desc()).limit(50)
                
                result = await self.db.execute(stmt)
                tmdb_ids = result.scalars().all()
                
                if tmdb_ids:
                    vectors = await self._fetch_vectors(tmdb_ids)
                    if vectors:
                        user_vector = np.mean(vectors, axis=0)
                
                # B. Get Watchlist (Candidates)
                stmt = select(Movie.tmdb_id).join(UserRating).where(
                    UserRating.user_id == user.id,
                    UserRating.is_watchlist.is_(True)
                )
                result = await self.db.execute(stmt)
                watchlist_ids = result.scalars().all()
                watchlist_candidates.update(watchlist_ids)
                
                # C. Get Watched (Exclusions)
                stmt = select(Movie.tmdb_id).join(UserRating).where(
                    UserRating.user_id == user.id,
                    UserRating.is_watched.is_(True)
                )
                result = await self.db.execute(stmt)
                watched_ids = result.scalars().all()
                excluded_ids.update(watched_ids)
                
            else:
                # --- GUEST USER (RSS) ---
                try:
                    items = await self.fetch_user_rss(username)
                    if not items:
                        continue
                        
                    # A. Get Taste Vector (Avg of top 50 recent items)
                    # RSS items are already sorted by date usually
                    target_items = items[:50]
                    tmdb_ids = [i['tmdb_id'] for i in target_items if i.get('tmdb_id')]
                    
                    if tmdb_ids:
                        vectors = await self._fetch_vectors(tmdb_ids)
                        if vectors:
                            user_vector = np.mean(vectors, axis=0)
                            titles = [i['title'] for i in target_items if i.get('tmdb_id')]
                            logger.info(f"Guest {username} Vector built from {len(titles)} movies: {', '.join(titles[:5])}...")
                        else:
                            logger.warning(f"Guest {username}: No vectors found for top 15 items.")
                            
                    # B. Get Watched (Exclusions)
                    # Note: We cannot get watchlist for guests via RSS easily
                    for item in items:
                        if item.get('tmdb_id'):
                            excluded_ids.add(item['tmdb_id'])
                            
                except Exception as e:
                    logger.error(f"Error processing guest user {username}: {e}")
                    continue
            
            if user_vector is not None:
                user_data.append({'username': username, 'vector': user_vector})

        if not user_data:
            return []
            
        # Extract vectors for centroid calc
        user_vectors = [u['vector'] for u in user_data]

        # 2. Candidate Generation
        # Remove watched movies from candidates
        final_candidates = list(watchlist_candidates - excluded_ids)
        
        # Fallback / Discovery Mode
        # If we have few candidates (e.g. < 50), fill with Discovery items
        if len(final_candidates) < 50:
            needed = 50 - len(final_candidates)
            # Increase fetch limit to 500 to cast a wider net for "good" movies that might be slightly further away
            fetch_limit = 500 + len(excluded_ids)
            logger.info(f"Low candidate count ({len(final_candidates)}). Fetching {fetch_limit} discovery items (needed: {needed}).")
            
            # Discovery Mode: Union of Individual Searches
            # Instead of searching for the "Average User" (which might be nobody),
            # we search for movies similar to EACH user and combine them.
            # This ensures every candidate is strongly liked by at least one person.
            
            discovery_candidates = set()
            
            # We need to fetch enough items to survive filtering
            per_user_limit = max(50, int(400 / len(user_vectors))) 
            
            logger.info(f"Low candidate count ({len(final_candidates)}). Discovery Mode: Union of Individual Searches (Limit {per_user_limit}/user).")

            for i, u_vec in enumerate(user_vectors):
                try:
                    # Search for this specific user
                    user_results = await self.qdrant.search_similar(
                        query_vector=u_vec.tolist(),
                        limit=per_user_limit,
                        score_threshold=0.0,
                        filters={"exclude_tmdb_ids": list(excluded_ids)}
                    )
                    
                    count = 0
                    for res in user_results:
                        if res['movie_id'] not in final_candidates and res['movie_id'] not in excluded_ids:
                            discovery_candidates.add(res['movie_id'])
                            count += 1
                            
                    logger.info(f"User {i+1} Discovery: Found {count} unique candidates.")
                    
                except Exception as e:
                    logger.error(f"Error in discovery search for user {i}: {e}")

            # Add unique discovery items to final candidates
            initial_count = len(final_candidates)
            for mid in discovery_candidates:
                if mid not in final_candidates:
                    final_candidates.append(mid)
            
            logger.info(f"Discovery complete. Added {len(final_candidates) - initial_count} items. Total candidates: {len(final_candidates)}")
        
        # 3. Scoring
        scored_results = []
        
        # Fetch vectors for all candidates
        candidate_vectors_map = {}
        # Batch fetch might be needed if list is huge, but for 50 it's fine
        # We need a way to get vectors for arbitrary IDs efficiently. 
        # qdrant.retrieve is good.
        
        try:
            points = await self.qdrant.client.retrieve(
                collection_name=self.qdrant.COLLECTION_NAME,
                ids=final_candidates,
                with_payload=False,
                with_vectors=True
            )
            for p in points:
                if p.vector:
                    candidate_vectors_map[p.id] = np.array(p.vector)
        except Exception as e:
            logger.error(f"Error fetching candidate vectors: {e}")
            return []

        for tmdb_id in final_candidates:
            vec = candidate_vectors_map.get(tmdb_id)
            if vec is None:
                continue
                
            # Calculate similarity to EACH user
            similarities = []
            contributors = []
            
            for u_data in user_data:
                u_vec = u_data['vector']
                username = u_data['username']
                
                # Cosine Similarity
                sim = np.dot(vec, u_vec) / (np.linalg.norm(vec) * np.linalg.norm(u_vec))
                similarities.append(sim)
                
                # Store individual contribution (clamped to 0-1 for UI)
                contributors.append({
                    "username": username,
                    "score": float(max(0, sim)) # Ensure non-negative for UI bars
                })
            
            # Scoring Logic
            # CHANGED: Use MAX similarity instead of AVG similarity.
            # Why? We want to surface movies that at least one person LOVES.
            # The "Hate Penalty" below will still protect us from polarizing movies.
            max_sim = np.max(similarities)
            avg_sim = np.mean(similarities)
            min_sim = np.min(similarities)
            
            # Base Score = Max Similarity (Reward passion)
            final_score = max_sim
            
            # Penalty: If ANY user hates it (similarity < 0.65), penalize heavily
            # This ensures "Group Cohesion" - no movie that one person hates
            if min_sim < 0.65:
                final_score *= 0.5 # 50% penalty
            
            # Bonus: Watchlist
            if tmdb_id in watchlist_candidates:
                final_score += 0.15 # Significant boost
                
            scored_results.append({
                "tmdb_id": tmdb_id,
                "score": final_score,
                "avg_sim": avg_sim,
                "min_sim": min_sim,
                "is_watchlist": tmdb_id in watchlist_candidates,
                "contributors": contributors
            })
            
        # 4. Sort & Return
        scored_results.sort(key=lambda x: x['score'], reverse=True)
        
        # Log top 5 for debug
        for i, res in enumerate(scored_results[:5]):
            logger.info(f"Top {i+1}: ID={res['tmdb_id']}, Score={res['score']:.3f} (Avg={res['avg_sim']:.3f}, Min={res['min_sim']:.3f}, WL={res['is_watchlist']})")
            
        # Return top 50 (to allow filtering of invalid/missing movies downstream)
        return scored_results[:50]

    async def _fetch_vectors(self, tmdb_ids: List[int]) -> List[np.ndarray]:
        """Helper to fetch vectors from Qdrant"""
        vectors = []
        try:
            points = await self.qdrant.client.retrieve(
                collection_name=self.qdrant.COLLECTION_NAME,
                ids=tmdb_ids,
                with_payload=False,
                with_vectors=True
            )
            for p in points:
                if p.vector:
                    vectors.append(np.array(p.vector))
        except Exception as e:
            logger.error(f"Error fetching vectors: {e}")
        return vectors
