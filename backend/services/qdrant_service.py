"""
Qdrant vector database service for semantic search
"""
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue,
    HnswConfigDiff, SearchParams, PayloadSchemaType,
)
from typing import List, Dict, Optional, Union
from models.external_schemas import QdrantPayload
import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

from telemetry import get_tracer
_tracer = get_tracer("qdrant")


class QdrantService:
    """Qdrant vector database operations"""
    
    COLLECTION_NAME = "movies"
    VECTOR_SIZE = 768  # google/embeddinggemma-300m embedding size
    
    def __init__(self):
        qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
        self.client = AsyncQdrantClient(url=qdrant_url)

    async def aclose(self):
        """Close the underlying AsyncQdrantClient. Only for OWNED instances
        (e.g. MovieService's lazy per-instance client) — never call on the
        injected singleton from dependencies.py (anti-pattern #3)."""
        await self.client.close()

    async def init_collection(self):
        """Initialize Qdrant collection if it doesn't exist"""
        try:
            collections = (await self.client.get_collections()).collections
            collection_names = [c.name for c in collections]
            
            if self.COLLECTION_NAME not in collection_names:
                logger.info(f"Creating Qdrant collection: {self.COLLECTION_NAME}")
                try:
                    await self.client.create_collection(
                        collection_name=self.COLLECTION_NAME,
                        vectors_config=VectorParams(
                            size=self.VECTOR_SIZE,
                            distance=Distance.COSINE,
                            # HNSW tuning: m=32 increases graph connectivity for better recall
                            # at the cost of ~2x index size vs default m=16. ef_construct=200
                            # improves build quality. Both are safe for a 768-dim collection.
                            hnsw_config=HnswConfigDiff(m=32, ef_construct=200),
                        )
                    )
                    logger.info("Collection created successfully")
                    # Initialize payload indexes for filterable fields
                    await self.init_payload_indexes()
                except Exception as e:
                    # Handle Race Condition: 409 Conflict means it was created by another process
                    if "409" in str(e) or "already exists" in str(e):
                        logger.warning(f"Collection creation race condition handled: {e}")
                    else:
                        raise e
            else:
                logger.info(f"Collection {self.COLLECTION_NAME} already exists")
                # Warn if pre-existing collection has legacy HNSW m=16 — the new
                # m=32 config only applies to freshly-created collections, so an
                # operator needs to rebuild (scripts/reembed_catalog.py) to pick it up.
                try:
                    info = await self.client.get_collection(self.COLLECTION_NAME)
                    current_m = info.config.hnsw_config.m
                    if current_m != 32:
                        logger.warning(
                            f"Qdrant collection HNSW m={current_m} (expected 32). "
                            f"The new tuning will not apply until the collection is rebuilt."
                        )
                except Exception as inspect_err:
                    logger.debug(f"Could not inspect HNSW config: {inspect_err}")
                # Ensure payload indexes exist (idempotent — no-op if already present)
                await self.init_payload_indexes()
        except Exception as e:
            logger.error(f"Failed to initialize Qdrant collection: {e}")
            raise
    
    async def init_payload_indexes(self) -> None:
        """
        Create payload indexes for filterable fields. Idempotent — safe to call
        on every startup; Qdrant returns a no-op for already-existing indexes.
        These replace full payload scans for the vectorbox_score >= 55 filter
        used in every recommendation path.
        """
        indexes = [
            ("vectorbox_score", PayloadSchemaType.FLOAT),
            ("vote_count", PayloadSchemaType.INTEGER),
            ("year", PayloadSchemaType.INTEGER),
            ("popularity", PayloadSchemaType.FLOAT),
            ("has_enriched_embedding", PayloadSchemaType.BOOL),
            # Payload-backed filters added 2026-07-29 so they narrow DURING the
            # search instead of being post-filtered out of twenty candidates.
            ("countries", PayloadSchemaType.KEYWORD),
            ("spoken_languages", PayloadSchemaType.KEYWORD),
            ("mpaa_rating", PayloadSchemaType.KEYWORD),
            ("oscar_wins", PayloadSchemaType.INTEGER),
            ("is_adult", PayloadSchemaType.BOOL),
        ]
        for field_name, field_schema in indexes:
            try:
                await self.client.create_payload_index(
                    collection_name=self.COLLECTION_NAME,
                    field_name=field_name,
                    field_schema=field_schema,
                )
                logger.debug(f"Payload index ensured: {field_name}")
            except Exception as e:
                # 400 / "already exists" is expected on subsequent boots
                if "already exists" in str(e).lower() or "400" in str(e):
                    pass
                else:
                    logger.warning(f"Could not create payload index for {field_name}: {e}")

    async def upsert_movie_vector(
        self,
        movie_id: int,
        vector: List[float],
        metadata: Union[Dict, QdrantPayload]
    ):
        """
        Insert or update movie vector
        Security: Validate vector dimensions
        """
        if len(vector) != self.VECTOR_SIZE:
            raise ValueError(f"Vector size mismatch. Expected {self.VECTOR_SIZE}, got {len(vector)}")
        
        # Accept either a Pydantic V2 model (QdrantPayload) or a plain dict.
        payload = metadata.model_dump(exclude_none=True) if hasattr(metadata, "model_dump") else metadata
        
        try:
            point = PointStruct(
                id=movie_id,
                vector=vector,
                payload=payload
            )
            
            await self.client.upsert(
                collection_name=self.COLLECTION_NAME,
                points=[point]
            )
            # Safe access for logging
            title = payload.get('title') if isinstance(payload, dict) else str(movie_id)
            logger.info(f"Successfully upserted vector for movie: {title}")
        except Exception as e:
            logger.error(f"Failed to upsert vector for movie {movie_id}: {e}")
            raise

    async def upsert_batch(self, points: List[PointStruct], check_exists: bool = False):
        """
        Upsert a batch of points to Qdrant.
        If check_exists is True, it will first retrieve existing points to skip redundant writes.
        """
        if not points:
            return

        collection_name = self.COLLECTION_NAME

        if check_exists:
            try:
                # Extract IDs to check
                point_ids = [p.id for p in points]
                from qdrant_client.http import models as rest
                
                existing, _ = await self.client.scroll(
                    collection_name=collection_name,
                    scroll_filter=rest.Filter(
                        must=[rest.HasIdCondition(has_id=point_ids)]
                    ),
                    limit=len(point_ids),
                    with_payload=True,
                    with_vectors=True
                )
                
                existing_map = {p.id: p for p in existing}
                
                # Filter points: Keep if not exists, or if payload/vector changed
                # (For simplicity here, we assume if it exists we skip, since we're just avoiding redundant initial upserts.
                # If full diffing is needed, we'd compare vectors/payloads, but skipping existing is a big win for concurrent paths.)
                filtered_points = []
                for p in points:
                    if p.id not in existing_map:
                        filtered_points.append(p)
                        
                points = filtered_points
                if not points:
                    logger.debug(f"All {len(point_ids)} points already exist in Qdrant {collection_name}. Skipping upsert.")
                    return
                
            except Exception as e:
                logger.warning(f"Failed to check existing points in Qdrant: {e}. Proceeding with full upsert.")
        
        try:
            await self.client.upsert(
                collection_name=collection_name,
                points=points
            )
            logger.info(f"Upserted {len(points)} points to {collection_name}")
        except Exception as e:
            logger.error(f"Failed to upsert batch to Qdrant: {e}")
            raise
    
    # Every key `search_similar` acts on. Anything else is a no-op that looks
    # like a constraint — see the check inside the method.
    FILTER_KEYS = frozenset({
        "include_unenriched", "year_min", "year_max", "genres", "include_genres",
        "min_runtime", "max_runtime", "min_rating", "min_vote_count",
        "max_vote_count", "max_popularity", "popularity_vibe", "original_language",
        "include_keywords", "include_tmdb_ids", "exclude_tmdb_ids",
        "min_vectorbox_score", "min_imdb_rating", "min_metacritic",
        # Payload-backed since 2026-07-29 (see scripts/sync_qdrant_payload.py).
        "countries", "spoken_languages", "mpaa_ratings", "min_oscar_wins",
        "exclude_adult",
    })

    async def search_similar(
        self,
        query_vector: List[float],
        limit: int = 20,
        offset: int = 0,
        score_threshold: float = 0.5,
        filters: Optional[Dict] = None
    ) -> List[Dict]:
        """
        STEP 3: Advanced hybrid search for movies with popularity vibe filtering
        Supports: year ranges, genre include/exclude, runtime, hidden gems vs blockbusters
        """
        if len(query_vector) != self.VECTOR_SIZE:
            raise ValueError(f"Query vector size mismatch")

        # Security: Limit results
        limit = min(limit, 1000)

        # A filter key this method does not know is the most dangerous kind of
        # bug in here, because the search still succeeds and the results still
        # look plausible — the constraint simply never happened. Three of them
        # were live until 2026-07-29 (mpaa_ratings, min_oscar_wins,
        # exclude_adult), passed by routers/search.py and dropped on the floor,
        # and a fourth was found the same day one layer up (the feed handing the
        # rail's constraints to the wrong dict).
        #
        # tests/test_qdrant_filter_contract.py is the real guard — it diffs
        # every key written anywhere in the codebase against FILTER_KEYS. This
        # is the runtime half, for a key that arrives from somewhere static
        # analysis cannot see.
        unknown = set(filters or {}) - self.FILTER_KEYS
        if unknown:
            logger.error(
                "search_similar ignoring unknown filter key(s) %s — the search will "
                "run WITHOUT that constraint. Add it to FILTER_KEYS and handle it.",
                sorted(unknown),
            )

        try:
            # Build advanced filter
            qdrant_filter = None
            filters = filters or {}
            must_conditions = []
            must_not_conditions = []

            # Enriched-vector gate (default ON): legacy-recipe vectors live in an
            # asymmetric text-space and pollute recommendation rankings. Opt out
            # with filters={"include_unenriched": True} (scripts/experiments only).
            if not filters.get("include_unenriched"):
                must_conditions.append(
                    FieldCondition(
                        key="has_enriched_embedding",
                        match=MatchValue(value=True)
                    )
                )

            if filters:

                # 1. Year range filters
                if "year_min" in filters and filters["year_min"]:
                    must_conditions.append(
                        FieldCondition(
                            key="year",
                            range={"gte": filters["year_min"]}
                        )
                    )
                if "year_max" in filters and filters["year_max"]:
                    must_conditions.append(
                        FieldCondition(
                            key="year",
                            range={"lte": filters["year_max"]}
                        )
                    )

                # 2. Genre filters
                if "genres" in filters and filters["genres"]:
                    from qdrant_client.models import MatchAny
                    must_conditions.append(
                        FieldCondition(
                            key="genres",
                            match=MatchAny(any=filters["genres"])
                        )
                    )
                
                if "include_genres" in filters and filters["include_genres"]:
                     from qdrant_client.models import MatchAny
                     must_conditions.append(
                        FieldCondition(
                            key="genres",
                            match=MatchAny(any=filters["include_genres"])
                        )
                    )

                # 3. Runtime filters
                if "min_runtime" in filters and filters["min_runtime"]:
                    must_conditions.append(
                        FieldCondition(
                            key="runtime",
                            range={"gte": filters["min_runtime"]}
                        )
                    )
                
                if "max_runtime" in filters and filters["max_runtime"]:
                    must_conditions.append(
                        FieldCondition(
                            key="runtime",
                            range={"lte": filters["max_runtime"]}
                        )
                    )
                
                # 5. POPULARITY VIBE FILTER
                if "popularity_vibe" in filters:
                    vibe = filters["popularity_vibe"]
                    
                    if vibe == "hidden_gem":
                        must_conditions.append(
                            FieldCondition(
                                key="vote_count",
                                range={"lt": 5000}
                            )
                        )
                        must_conditions.append(
                            FieldCondition(
                                key="vote_average",
                                range={"gte": 7.0}
                            )
                        )
                    
                    elif vibe == "blockbuster":
                        must_conditions.append(
                            FieldCondition(
                                key="vote_count",
                                range={"gte": 10000}
                            )
                        )

                # 6. Exclude specific TMDB IDs
                if "exclude_tmdb_ids" in filters and filters["exclude_tmdb_ids"]:
                    from qdrant_client.models import HasIdCondition
                    must_not_conditions.append(
                        HasIdCondition(has_id=filters["exclude_tmdb_ids"])
                    )

                # 6b. F8: restrict the search to an allowed TMDB-id set. Streaming
                # availability isn't a Qdrant payload field (it lives in Postgres), so
                # the rail's provider filter is resolved to a film-id set and pushed in
                # HERE — keeping provider filtering filter-at-source (taste-rank WITHIN
                # the provider catalogue) instead of a post-filter on a small pool.
                if "include_tmdb_ids" in filters and filters["include_tmdb_ids"]:
                    from qdrant_client.models import HasIdCondition
                    must_conditions.append(
                        HasIdCondition(has_id=filters["include_tmdb_ids"])
                    )

                # 7. Vote Count Filter
                if "min_vote_count" in filters and filters["min_vote_count"]:
                    must_conditions.append(
                        FieldCondition(
                            key="vote_count",
                            range={"gte": filters["min_vote_count"]}
                        )
                    )

                # 7b. VectorBox quality floor (payload field) — lets the rail Q slider
                # filter the whole catalogue at search time instead of post-filtering a
                # taste-ranked top-N (which starved: Q90 matched only ~2 of the 200).
                if "min_vectorbox_score" in filters and filters["min_vectorbox_score"]:
                    must_conditions.append(
                        FieldCondition(
                            key="vectorbox_score",
                            range={"gte": filters["min_vectorbox_score"]}
                        )
                    )

                if "max_vote_count" in filters and filters["max_vote_count"]:
                    must_conditions.append(
                        FieldCondition(
                            key="vote_count",
                            range={"lte": filters["max_vote_count"]}
                        )
                    )

                # 8. Rating Filter
                if "min_rating" in filters and filters["min_rating"]:
                    must_conditions.append(
                        FieldCondition(
                            key="vote_average",
                            range={"gte": filters["min_rating"]}
                        )
                    )

                # 9. Language Filter
                if "original_language" in filters and filters["original_language"]:
                    must_conditions.append(
                        FieldCondition(
                            key="original_language",
                            match=MatchValue(value=filters["original_language"])
                        )
                    )

                # 10. Keywords Filter
                if "include_keywords" in filters and filters["include_keywords"]:
                    from qdrant_client.models import MatchAny
                    must_conditions.append(
                        FieldCondition(
                            key="keywords",
                            match=MatchAny(any=filters["include_keywords"])
                        )
                    )

                # 11. VectorBox Score Filter
                if "min_vectorbox_score" in filters and filters["min_vectorbox_score"]:
                    must_conditions.append(
                        FieldCondition(
                            key="vectorbox_score",
                            range={"gte": filters["min_vectorbox_score"]}
                        )
                    )
                
                # 12. TMDB Popularity Filter (for Hidden Gems - Hype Ceiling)
                if "max_popularity" in filters and filters["max_popularity"]:
                    must_conditions.append(
                        FieldCondition(
                            key="popularity",
                            range={"lte": filters["max_popularity"]}
                        )
                    )

                # 13. Country / language / certification / awards / adult —
                # payload-backed since 2026-07-29. They used to be post-filtered
                # in Postgres AFTER the search returned, which meant they could
                # only subtract from the twenty nearest neighbours of the query
                # vector: "thrillers coreanos" kept ONE film out of the 219
                # Korean ones the catalogue holds. Applied here, Qdrant narrows
                # DURING the search and the neighbours are all candidates.
                #
                # A point missing the key is excluded by a `must` — correct: we
                # do not know its country, so it cannot be claimed to match.
                # Coverage measured 2026-07-29: countries 97.2%, languages 95.9%,
                # mpaa 74.9%.
                if "countries" in filters and filters["countries"]:
                    from qdrant_client.models import MatchAny
                    must_conditions.append(
                        FieldCondition(key="countries", match=MatchAny(any=filters["countries"]))
                    )
                if "spoken_languages" in filters and filters["spoken_languages"]:
                    from qdrant_client.models import MatchAny
                    must_conditions.append(
                        FieldCondition(key="spoken_languages", match=MatchAny(any=filters["spoken_languages"]))
                    )
                if "mpaa_ratings" in filters and filters["mpaa_ratings"]:
                    from qdrant_client.models import MatchAny
                    must_conditions.append(
                        FieldCondition(key="mpaa_rating", match=MatchAny(any=filters["mpaa_ratings"]))
                    )
                if "min_oscar_wins" in filters and filters["min_oscar_wins"]:
                    must_conditions.append(
                        FieldCondition(key="oscar_wins", range={"gte": filters["min_oscar_wins"]})
                    )
                if filters.get("exclude_adult"):
                    # must_not, so a point with no is_adult key still passes —
                    # absence means "not flagged", which is the safe reading.
                    must_not_conditions.append(
                        FieldCondition(key="is_adult", match=MatchValue(value=True))
                    )

                # IMDb / Metacritic — payload-backed (set by reembed_catalog).
                if "min_imdb_rating" in filters and filters["min_imdb_rating"] is not None:
                    must_conditions.append(
                        FieldCondition(
                            key="imdb_rating",
                            range={"gte": filters["min_imdb_rating"]}
                        )
                    )
                if "min_metacritic" in filters and filters["min_metacritic"] is not None:
                    must_conditions.append(
                        FieldCondition(
                            key="metacritic_rating",
                            range={"gte": filters["min_metacritic"]}
                        )
                    )
            # Build final filter (outside the `if filters:` block — the enriched
            # gate must apply even when the caller passes no filters at all)
            if must_conditions or must_not_conditions:
                filter_params = {}
                if must_conditions:
                    filter_params["must"] = must_conditions
                if must_not_conditions:
                    filter_params["must_not"] = must_not_conditions
                qdrant_filter = Filter(**filter_params)

            # Score threshold is independent of filters — do not reset when filters are present
            effective_threshold = score_threshold
            
            from qdrant_client.http import models

            with _tracer.start_as_current_span("qdrant.search") as _span:
                _span.set_attribute("qdrant.limit", limit)
                _span.set_attribute("qdrant.filters", ",".join(sorted(filters)) or "none")
                _span.set_attribute("qdrant.threshold", effective_threshold)
                results = await self.client.query_points(
                    collection_name=self.COLLECTION_NAME,
                    query=query_vector,
                limit=limit,
                offset=offset,
                score_threshold=effective_threshold,
                query_filter=qdrant_filter,
                # Search-time HNSW ef: higher = better recall at cost of latency.
                # ef=128 is the recommended production baseline for 768-dim embeddinggemma.
                search_params=SearchParams(hnsw_ef=128, exact=False),
                # [OPTIMIZATION] Payload Selector
                # Excludes the genuinely heavy fields: keywords, cast, directors.
                #
                # runtime/genres/overview were excluded too until 2026-07-29, and
                # every caller reads them off this metadata. The visible symptom was
                # a landing full of "TBA" durations; the expensive one was that
                # `not metadata.get("overview")` is then true for EVERY row, so
                # routers/search.py fanned out 20 TMDB detail calls per search to
                # re-fetch what Qdrant already held. The item-to-item path has no
                # such fallback and simply returned empty overviews.
                with_payload=[
                    "tmdb_id",
                    "title",
                    "year",
                    "vectorbox_score",
                    "vote_count",
                    "popularity",
                    "poster_path",  # Useful for debugging or quick UI
                    "vote_average",
                    "runtime",
                    "genres",
                    "overview",
                ]
            )
            
            return [
                {
                    "movie_id": hit.id,
                    "score": hit.score,
                    "metadata": hit.payload
                }
                for hit in results.points
            ]
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    async def search_similar_movies(self, movie_id: int, limit: int = 20) -> List[Dict]:
        """
        Find similar movies by ID
        1. Get vector for movie_id
        2. Search similar vectors
        """
        vector = await self.get_vector(movie_id)
        if not vector:
            logger.warning(f"No vector found for movie {movie_id}")
            return []
        
        return await self.search_similar(
            query_vector=vector,
            limit=limit,
            score_threshold=0.4  # Higher threshold for direct similarity
        )
    
    async def get_vector(self, movie_id: int) -> Optional[List[float]]:
        """Retrieve vector for a specific movie"""
        try:
            points = await self.client.retrieve(
                collection_name=self.COLLECTION_NAME,
                ids=[movie_id],
                with_vectors=True
            )
            
            if points:
                return points[0].vector
            return None
        except Exception as e:
            logger.error(f"Failed to retrieve vector for movie {movie_id}: {e}")
            return None
    
    async def get_vectors_batch(self, movie_ids: List[int]) -> Dict[int, List[float]]:
        """
        Retrieve vectors for multiple movies in a SINGLE Qdrant call.
        Returns a dict mapping movie_id -> vector.
        Eliminates N+1 when fetching vectors for a list of candidates.
        """
        if not movie_ids:
            return {}
        try:
            points = await self.client.retrieve(
                collection_name=self.COLLECTION_NAME,
                ids=movie_ids,
                with_vectors=True
            )
            return {p.id: p.vector for p in points if p.vector is not None}
        except Exception as e:
            logger.error(f"Failed to batch-retrieve vectors for {len(movie_ids)} movies: {e}")
            return {}

    async def delete_movie(self, movie_id: int):
        """Delete movie vector"""
        try:
            await self.client.delete(
                collection_name=self.COLLECTION_NAME,
                points_selector=[movie_id]
            )
        except Exception as e:
            logger.error(f"Failed to delete movie {movie_id}: {e}")
