"""
Qdrant vector database service for semantic search
"""
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue
from typing import List, Dict, Optional
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
        self.client = QdrantClient(url=qdrant_url)
    
    async def init_collection(self):
        """Initialize Qdrant collection if it doesn't exist"""
        try:
            collections = self.client.get_collections().collections
            collection_names = [c.name for c in collections]
            
            if self.COLLECTION_NAME not in collection_names:
                logger.info(f"Creating Qdrant collection: {self.COLLECTION_NAME}")
                self.client.create_collection(
                    collection_name=self.COLLECTION_NAME,
                    vectors_config=VectorParams(
                        size=self.VECTOR_SIZE,
                        distance=Distance.COSINE
                    )
                )
                logger.info("Collection created successfully")
            else:
                logger.info(f"Collection {self.COLLECTION_NAME} already exists")
        except Exception as e:
            logger.error(f"Failed to initialize Qdrant collection: {e}")
            raise
    
    async def upsert_movie_vector(
        self,
        movie_id: int,
        vector: List[float],
        metadata: Dict
    ):
        """
        Insert or update movie vector
        Security: Validate vector dimensions
        """
        if len(vector) != self.VECTOR_SIZE:
            raise ValueError(f"Vector size mismatch. Expected {self.VECTOR_SIZE}, got {len(vector)}")
        
        try:
            point = PointStruct(
                id=movie_id,
                vector=vector,
                payload=metadata
            )
            
            self.client.upsert(
                collection_name=self.COLLECTION_NAME,
                points=[point]
            )
            logger.info(f"Successfully upserted vector for movie: {metadata.get('title', movie_id)}")
        except Exception as e:
            logger.error(f"Failed to upsert vector for movie {movie_id}: {e}")
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
                
                # Build final filter
                if must_conditions or must_not_conditions:
                    filter_params = {}
                    if must_conditions:
                        filter_params["must"] = must_conditions
                    if must_not_conditions:
                        filter_params["must_not"] = must_not_conditions
                    qdrant_filter = Filter(**filter_params)
            
            # If filters are present, lower the threshold
            effective_threshold = score_threshold
            if qdrant_filter:
                effective_threshold = 0.0 
            
            results = self.client.search(
                collection_name=self.COLLECTION_NAME,
                query_vector=query_vector,
                limit=limit,
                offset=offset,
                score_threshold=effective_threshold,
                query_filter=qdrant_filter,
                with_payload=True
            )
            
            return [
                {
                    "movie_id": hit.id,
                    "score": hit.score,
                    "metadata": hit.payload
                }
                for hit in results
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
            points = self.client.retrieve(
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
    
    async def delete_movie(self, movie_id: int):
        """Delete movie vector"""
        try:
            self.client.delete(
                collection_name=self.COLLECTION_NAME,
                points_selector=[movie_id]
            )
        except Exception as e:
            logger.error(f"Failed to delete movie {movie_id}: {e}")
