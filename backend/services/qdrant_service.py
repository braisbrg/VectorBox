"""
Qdrant vector database service for semantic search
"""
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue
from typing import List, Dict, Optional, Union
from models.external_schemas import QdrantPayload
import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class QdrantService:
    """Qdrant vector database operations"""
    
    COLLECTION_NAME = "movies"
    VECTOR_SIZE = 384  # all-MiniLM-L6-v2 embedding size
    
    def __init__(self):
        qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
        self.client = AsyncQdrantClient(url=qdrant_url)
    
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
                            distance=Distance.COSINE
                        )
                    )
                    logger.info("Collection created successfully")
                except Exception as e:
                    # Handle Race Condition: 409 Conflict means it was created by another process
                    if "409" in str(e) or "already exists" in str(e):
                        logger.warning(f"Collection creation race condition handled: {e}")
                    else:
                        raise e
            else:
                logger.info(f"Collection {self.COLLECTION_NAME} already exists")
        except Exception as e:
            logger.error(f"Failed to initialize Qdrant collection: {e}")
            raise
    
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
        
        # Convert Pydantic model to dict if necessary
        payload = metadata
        if hasattr(metadata, "model_dump"):
             payload = metadata.model_dump(exclude_none=True)
        elif hasattr(metadata, "dict"): # Compat
             payload = metadata.dict(exclude_none=True)
        
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
        
        try:
            # Build advanced filter
            qdrant_filter = None
            if filters:
                must_conditions = []
                must_not_conditions = []
                
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

                # 7. Vote Count Filter
                if "min_vote_count" in filters and filters["min_vote_count"]:
                    must_conditions.append(
                        FieldCondition(
                            key="vote_count",
                            range={"gte": filters["min_vote_count"]}
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
                # Build final filter
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

            results = await self.client.query_points(
                collection_name=self.COLLECTION_NAME,
                query=query_vector,
                limit=limit,
                offset=offset,
                score_threshold=effective_threshold,
                query_filter=qdrant_filter,
                # [OPTIMIZATION] Payload Selector
                # Only fetch essential fields for sorting/filtering.
                # Exclude heavy text fields (overview, keywords, cast, directors)
                with_payload=[
                    "tmdb_id", 
                    "title", 
                    "year", 
                    "vectorbox_score", 
                    "vote_count", 
                    "popularity",
                    "poster_path", # Useful for debugging or quick UI
                    "vote_average"
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
